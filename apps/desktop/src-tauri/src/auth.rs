//! Desktop OAuth 2.0 Authorization Code + PKCE against the Cognito Hosted UI.
//!
//! Flow: open the Hosted UI in the system browser -> capture the redirect on a loopback
//! server (127.0.0.1) -> exchange the code for tokens. This mirrors `python/lexicon/cognito.py`
//! so the same flow is defined once per platform.
//!
//! The WebView is never used for login (it would be a security and CORS problem).

use std::sync::atomic::{AtomicBool, Ordering};

use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use base64::Engine;
use rand::RngCore;
use sha2::{Digest, Sha256};

const SCOPES: &str = "openid email profile";

/// Registered on the Cognito app client (see infra/cdk/lexicon_stack.py).
const DEFAULT_CALLBACK_URL: &str = "http://127.0.0.1:4200/callback";

pub struct AuthConfig {
    pub domain: String,
    pub client_id: String,
    pub callback_url: String,
    pub port: u16,
}

pub struct Tokens {
    pub id_token: String,
    pub refresh_token: String,
}

impl AuthConfig {
    /// Read Cognito settings from the environment (set by the CDK stack / shell profile).
    pub fn from_env() -> Result<Self, String> {
        let domain = std::env::var("LEXICON_COGNITO_DOMAIN")
            .map_err(|_| "LEXICON_COGNITO_DOMAIN is not set".to_string())?;
        let client_id = std::env::var("LEXICON_COGNITO_CLIENT_ID")
            .map_err(|_| "LEXICON_COGNITO_CLIENT_ID is not set".to_string())?;
        // The redirect URI MUST exactly match a URL registered on the Cognito app client.
        // It is hardcoded on purpose: an env-var override that doesn't match the allow-list
        // produces Cognito's opaque "An error was encountered with the requested page"
        // (redirect_mismatch) instead of anything actionable.
        //
        // To use a different port, register the new URL on the app client first, e.g.:
        //   cdk deploy -c extra_callback_urls='["http://127.0.0.1:4321/callback"]'
        let callback_url = std::env::var("LEXICON_COGNITO_CALLBACK_URL")
            .ok()
            .filter(|value| value == DEFAULT_CALLBACK_URL)
            .unwrap_or_else(|| DEFAULT_CALLBACK_URL.to_string());
        let port = callback_url
            .rsplit(':')
            .next()
            .and_then(|tail| tail.split('/').next())
            .and_then(|port| port.parse::<u16>().ok())
            .unwrap_or(4200);
        Ok(Self {
            domain,
            client_id,
            callback_url,
            port,
        })
    }
}

/// Return a PKCE `(verifier, challenge)` pair using S256.
fn pkce_pair() -> (String, String) {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    let verifier = URL_SAFE_NO_PAD.encode(bytes);
    let digest = Sha256::digest(verifier.as_bytes());
    let challenge = URL_SAFE_NO_PAD.encode(digest);
    (verifier, challenge)
}

fn build_authorize_url(config: &AuthConfig, challenge: &str, state: &str) -> String {
    let redirect = urlencoding::encode(&config.callback_url);
    format!(
        "https://{}/oauth2/authorize?response_type=code&client_id={}&redirect_uri={}\
         &scope={}&code_challenge={}&code_challenge_method=S256&state={}",
        config.domain,
        config.client_id,
        redirect,
        urlencoding::encode(SCOPES),
        challenge,
        state,
    )
}

/// Only one login may run at a time — a second bind to the same loopback port fails with
/// `EADDRINUSE` and surfaces as a confusing "could not bind loopback port" error.
static LOGGING_IN: AtomicBool = AtomicBool::new(false);

struct LoginGuard;

impl LoginGuard {
    fn acquire() -> Result<Self, String> {
        if LOGGING_IN.swap(true, Ordering::SeqCst) {
            Err("a login is already in progress".to_string())
        } else {
            Ok(Self)
        }
    }
}

impl Drop for LoginGuard {
    fn drop(&mut self) {
        LOGGING_IN.store(false, Ordering::SeqCst);
    }
}

/// Loopback-login timeout: if the user abandons the browser, release the port after this.`
const LOGIN_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(300);

/// Bind the loopback listener. Called **before** the browser opens so the OAuth redirect can
/// never arrive at an unbound port (which shows as "Firefox can't connect to 127.0.0.1:4200").
fn bind_loopback(port: u16) -> Result<std::sync::Arc<tiny_http::Server>, String> {
    let server = tiny_http::Server::http(format!("127.0.0.1:{port}"))
        .map_err(|error| format!("could not bind loopback port {port}: {error}"))?;
    eprintln!("[auth] loopback listener bound on 127.0.0.1:{port}");
    Ok(std::sync::Arc::new(server))
}

/// Block the calling thread until the (already-bound) loopback server receives the redirect.
fn wait_for_code(
    server: std::sync::Arc<tiny_http::Server>,
    expected_state: &str,
) -> Result<String, String> {
    // Watchdog: `unblock()` makes `incoming_requests()` terminate, releasing the port so a
    // later login isn't blocked by an abandoned browser tab.
    {
        let watchdog = server.clone();
        std::thread::spawn(move || {
            std::thread::sleep(LOGIN_TIMEOUT);
            watchdog.unblock();
        });
    }

    let result = (|| -> Result<String, String> {
        for request in server.incoming_requests() {
            let url = request.url().to_string();
            eprintln!("[auth] loopback received request: {url}");
            let params = parse_query(url.split('?').nth(1).unwrap_or(""));

            if let Some(error) = params.iter().find(|(k, _)| k == "error") {
                let _ = request.respond(html_response("Login failed. You can close this window."));
                return Err(format!("authorization failed: {}", error.1));
            }

            if let Some((_, code)) = params.iter().find(|(k, _)| k == "code") {
                let state_ok = params
                    .iter()
                    .find(|(k, _)| k == "state")
                    .map(|(_, value)| value == expected_state)
                    .unwrap_or(false);
                let _ =
                    request.respond(html_response("Login complete. You can close this window."));
                if !state_ok {
                    return Err("state mismatch in OAuth callback".to_string());
                }
                return Ok(code.clone());
            }

            let _ = request.respond(html_response("Waiting for login..."));
        }
        Err("loopback server closed before receiving a code".to_string())
    })();

    // Always release the port, even on error paths, so the next login can bind it.
    server.unblock();
    result
}

fn parse_query(query: &str) -> Vec<(String, String)> {
    query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            (key.to_string(), decode(value))
        })
        .collect()
}

fn decode(value: &str) -> String {
    urlencoding::decode(value)
        .map(|v| v.into_owned())
        .unwrap_or_else(|_| value.to_string())
}

fn html_response(message: &str) -> tiny_http::Response<std::io::Cursor<Vec<u8>>> {
    let body = format!("<!doctype html><html><body><h1>{message}</h1></body></html>");
    tiny_http::Response::from_string(body).with_header(
        tiny_http::Header::from_bytes("Content-Type", "text/html; charset=utf-8").unwrap(),
    )
}

async fn exchange_code(config: &AuthConfig, code: &str, verifier: &str) -> Result<Tokens, String> {
    let client = reqwest::Client::new();
    let response = client
        .post(format!("https://{}/oauth2/token", config.domain))
        .form(&[
            ("grant_type", "authorization_code"),
            ("client_id", config.client_id.as_str()),
            ("code", code),
            ("code_verifier", verifier),
            ("redirect_uri", config.callback_url.as_str()),
        ])
        .send()
        .await
        .map_err(|error| error.to_string())?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        return Err(format!("token exchange failed ({status}): {body}"));
    }

    let payload: serde_json::Value = response.json().await.map_err(|error| error.to_string())?;
    let id_token = payload
        .get("id_token")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "token response missing id_token".to_string())?
        .to_string();
    let refresh_token = payload
        .get("refresh_token")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    Ok(Tokens {
        id_token,
        refresh_token,
    })
}

/// Run the full interactive login and return the token set.
pub async fn login() -> Result<Tokens, String> {
    let _guard = LoginGuard::acquire()?; // no concurrent logins; released on drop
    let config = AuthConfig::from_env()?;
    let (verifier, challenge) = pkce_pair();
    let state = URL_SAFE_NO_PAD.encode(rand::random::<[u8; 16]>());
    let url = build_authorize_url(&config, &challenge, &state);

    // Bind BEFORE opening the browser. Otherwise a fast redirect (cached session / quick
    // sign-up) can hit 127.0.0.1:4200 while nothing is listening yet.
    let server = bind_loopback(config.port)?;
    eprintln!("[auth] opening browser for: {url}");
    open::that(&url).map_err(|error| format!("could not open the browser: {error}"))?;

    let state_clone = state.clone();
    let code = tokio::task::spawn_blocking(move || wait_for_code(server, &state_clone))
        .await
        .map_err(|error| error.to_string())??;

    exchange_code(&config, &code, &verifier).await
}

/// Exchange a stored refresh token for a fresh ID token (silent re-login on startup).
pub async fn refresh(refresh_token: &str) -> Result<Tokens, String> {
    let config = AuthConfig::from_env()?;
    let response = reqwest::Client::new()
        .post(format!("https://{}/oauth2/token", config.domain))
        .form(&[
            ("grant_type", "refresh_token"),
            ("client_id", config.client_id.as_str()),
            ("refresh_token", refresh_token),
        ])
        .send()
        .await
        .map_err(|error| error.to_string())?;

    if !response.status().is_success() {
        return Err(format!("refresh failed: {}", response.status()));
    }

    let payload: serde_json::Value = response.json().await.map_err(|error| error.to_string())?;
    let id_token = payload
        .get("id_token")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "refresh response missing id_token".to_string())?
        .to_string();
    let rotated = payload
        .get("refresh_token")
        .and_then(|value| value.as_str())
        .unwrap_or(refresh_token)
        .to_string();
    Ok(Tokens {
        id_token,
        refresh_token: rotated,
    })
}

/// Decode the `sub` claim without verifying the signature.
///
/// Used only to key *local* storage (keychain account name / SQLite user). The server always
/// re-verifies the token, so this is not a trust decision.
pub fn sub_from_id_token(token: &str) -> Option<String> {
    let payload = token.split('.').nth(1)?;
    let decoded = URL_SAFE_NO_PAD
        .decode(payload)
        .or_else(|_| URL_SAFE.decode(payload))
        .ok()?;
    let claims: serde_json::Value = serde_json::from_slice(&decoded).ok()?;
    claims
        .get("sub")
        .and_then(|value| value.as_str())
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_binds_and_serves_a_request() {
        let port = 43210u16; // avoid the real login port
        let server = bind_loopback(port).expect("bind should succeed");

        let handle = std::thread::spawn(move || {
            if let Some(request) = server.incoming_requests().next() {
                let _ = request.respond(tiny_http::Response::from_string("ok-lexicon"));
            }
        });

        use std::io::{Read, Write};
        let mut stream =
            std::net::TcpStream::connect(("127.0.0.1", port)).expect("connect should succeed");
        stream
            .write_all(b"GET /callback?code=x HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            .unwrap();
        let mut body = String::new();
        stream.read_to_string(&mut body).unwrap();
        assert!(body.contains("ok-lexicon"), "unexpected response: {body}");
        handle.join().unwrap();
    }
}
