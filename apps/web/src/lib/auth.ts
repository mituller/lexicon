// Web-side Cognito auth: Hosted UI + Authorization Code + PKCE.
//
// Desktop uses the Rust loopback flow instead (see apps/desktop/src-tauri/src/auth.rs); this
// module is only used in the browser build. The web target is not deployed yet, so treat this
// as the prepared (stubbed) implementation for Phase 5.

const DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN ?? "";
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID ?? "";
const REDIRECT_URI = import.meta.env.VITE_COGNITO_REDIRECT_URI ?? window.location.origin;
const LOGOUT_URI = import.meta.env.VITE_COGNITO_LOGOUT_URI ?? window.location.origin;
const SCOPES = "openid email profile";

const VERIFIER_KEY = "lexicon.pkce.verifier";
const STATE_KEY = "lexicon.pkce.state";
const TOKEN_KEY = "lexicon.id_token";

export function isConfigured(): boolean {
  return Boolean(DOMAIN && CLIENT_ID);
}

export function getIdToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getIdToken() !== null;
}

export function logout(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  if (!isConfigured()) return;
  const url =
    `https://${DOMAIN}/logout?client_id=${encodeURIComponent(CLIENT_ID)}` +
    `&logout_uri=${encodeURIComponent(LOGOUT_URI)}`;
  window.location.assign(url);
}

/** Redirect to the Hosted UI, remembering the PKCE verifier + state. */
export async function beginLogin(): Promise<void> {
  if (!isConfigured()) throw new Error("Cognito is not configured (VITE_COGNITO_* unset)");
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64Url(
    new Uint8Array(await crypto.subtle.digest("SHA-256", encode(verifier).buffer as ArrayBuffer)),
  );
  const state = base64Url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  window.location.assign(buildAuthorizeUrl(challenge, state));
}

/** Handle the redirect back from the Hosted UI. Returns true when a login just completed. */
export async function completeLogin(): Promise<boolean> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code) return false;

  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const expectedState = sessionStorage.getItem(STATE_KEY);
  if (!verifier || params.get("state") !== expectedState) {
    throw new Error("OAuth state mismatch");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: CLIENT_ID,
    code,
    code_verifier: verifier,
    redirect_uri: REDIRECT_URI,
  });
  const response = await fetch(`https://${DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!response.ok) throw new Error(`token exchange failed: ${response.status}`);

  const payload = (await response.json()) as { id_token?: string };
  if (!payload.id_token) throw new Error("token response missing id_token");
  sessionStorage.setItem(TOKEN_KEY, payload.id_token);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);

  // Strip the code from the URL.
  window.history.replaceState({}, document.title, window.location.pathname);
  return true;
}

function buildAuthorizeUrl(challenge: string, state: string): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    scope: SCOPES,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
  });
  return `https://${DOMAIN}/oauth2/authorize?${params.toString()}`;
}

function encode(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
