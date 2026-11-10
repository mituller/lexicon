//! Thin Rust shell for the Lexicon desktop app.
//!
//! This layer deliberately holds almost no business logic. It is a secure bridge between the
//! React UI and the Python API: it stores the Cognito token, attaches it to requests, and
//! exposes typed `invoke()` commands. RAG, progress, and scheduling all live in Python.

mod auth;
mod secrets;
mod store;

use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::Manager;

/// In-memory token cache. TODO(Phase 1): persist via the OS keychain plugin.
static TOKEN: Mutex<Option<String>> = Mutex::new(None);
static REFRESH_TOKEN: Mutex<Option<String>> = Mutex::new(None);

/// Store the freshly exchanged tokens (called from the `auth_login` command).
pub(crate) fn store_tokens(id_token: String, refresh_token: String) {
    if let Ok(mut cache) = TOKEN.lock() {
        *cache = Some(id_token);
    }
    if let Ok(mut cache) = REFRESH_TOKEN.lock() {
        *cache = Some(refresh_token);
    }
}

fn api_base() -> String {
    std::env::var("LEXICON_API_BASE").unwrap_or_else(|_| "http://localhost:8000".to_string())
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .build()
        .map_err(|error| error.to_string())
}

/// Refresh the ID token from the stored refresh token, returning the new pair on success.
async fn refresh_tokens() -> Option<(String, String)> {
    let refresh = REFRESH_TOKEN.lock().ok()?.clone()?;
    match auth::refresh(&refresh).await {
        Ok(tokens) => Some((tokens.id_token, tokens.refresh_token)),
        Err(_) => None,
    }
}

async fn call_api(
    method: reqwest::Method,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    let mut refreshed_once = false;
    loop {
        let url = format!("{}{}", api_base(), path);
        let mut builder = client()?.request(method.clone(), url);
        if let Some(token) = TOKEN.lock().map_err(|e| e.to_string())?.clone() {
            builder = builder.bearer_auth(token);
        }
        if let Some(payload) = &body {
            builder = builder.json(payload);
        }
        let response = builder.send().await.map_err(|error| error.to_string())?;
        let status = response.status();

        // The ID token expires after an hour; transparently refresh and retry once on 401.
        if status == reqwest::StatusCode::UNAUTHORIZED && !refreshed_once {
            if let Some((id_token, refresh_token)) = refresh_tokens().await {
                store_tokens(id_token, refresh_token);
                refreshed_once = true;
                continue;
            }
        }

        let value: Value = response.json().await.map_err(|error| error.to_string())?;
        eprintln!("[api] {method} {path} -> {status}");
        if !status.is_success() {
            return Err(format!("API error {status}: {value}"));
        }
        return Ok(value);
    }
}

#[tauri::command]
async fn search(request: Value) -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/search", Some(request)).await
}

#[tauri::command]
async fn tutor(request: Value) -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/tutor", Some(request)).await
}

#[tauri::command]
async fn list_progress() -> Result<Value, String> {
    call_api(reqwest::Method::GET, "/v1/progress", None).await
}

#[tauri::command]
async fn review_term(term_id: String, update: Value) -> Result<Value, String> {
    eprintln!("[review] invoked term_id={term_id} update={update}");
    let path = format!("/v1/progress/{}", term_id);
    let result = call_api(reqwest::Method::PUT, &path, Some(update)).await;
    eprintln!("[review] result={result:?}");
    result
}

#[tauri::command]
async fn stats() -> Result<Value, String> {
    call_api(reqwest::Method::GET, "/v1/stats", None).await
}

// --- decks & retention (passthrough to the Python API) ---

#[tauri::command]
async fn list_decks() -> Result<Value, String> {
    call_api(reqwest::Method::GET, "/v1/decks", None).await
}

#[tauri::command]
async fn start_deck(request: Value) -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/deck/start", Some(request)).await
}

#[tauri::command]
async fn review_due(limit: i64) -> Result<Value, String> {
    let path = format!("/v1/review/due?limit={limit}");
    call_api(reqwest::Method::GET, &path, None).await
}

#[tauri::command]
async fn quiz(request: Value) -> Result<Value, String> {
    let domain = request.get("domain").and_then(Value::as_str).unwrap_or("");
    let tier = request
        .get("difficulty_tier")
        .and_then(Value::as_str)
        .unwrap_or("");
    let count = request.get("count").and_then(Value::as_i64).unwrap_or(10);
    let path = format!("/v1/quiz?domain={domain}&difficulty_tier={tier}&count={count}");
    call_api(reqwest::Method::GET, &path, None).await
}

#[tauri::command]
async fn grade_quiz(request: Value) -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/quiz/grade", Some(request)).await
}

// --- adaptive sessions (passthrough; grading happens in the Python API) ---

/// Session ids are server-generated hex; reject anything else before it reaches a URL path.
fn session_path(session_id: &str, action: &str) -> Result<String, String> {
    if session_id.is_empty() || !session_id.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err("invalid session id".to_string());
    }
    Ok(format!("/v1/session/{session_id}/{action}"))
}

#[tauri::command]
async fn start_session() -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/session", None).await
}

#[tauri::command]
async fn next_exercise(session_id: String) -> Result<Value, String> {
    call_api(
        reqwest::Method::GET,
        &session_path(&session_id, "next")?,
        None,
    )
    .await
}

#[tauri::command]
async fn answer_exercise(session_id: String, request: Value) -> Result<Value, String> {
    call_api(
        reqwest::Method::POST,
        &session_path(&session_id, "answer")?,
        Some(request),
    )
    .await
}

#[tauri::command]
async fn explain_answer(session_id: String, request: Value) -> Result<Value, String> {
    call_api(
        reqwest::Method::POST,
        &session_path(&session_id, "explain")?,
        Some(request),
    )
    .await
}

#[tauri::command]
async fn lesson(session_id: String, request: Value) -> Result<Value, String> {
    call_api(
        reqwest::Method::POST,
        &session_path(&session_id, "lesson")?,
        Some(request),
    )
    .await
}

#[tauri::command]
async fn pronounce(word: String) -> Result<Value, String> {
    let path = format!("/v1/pronounce?word={}", urlencoding::encode(&word));
    call_api(reqwest::Method::GET, &path, None).await
}

#[tauri::command]
async fn import_words(request: Value) -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/import", Some(request)).await
}

#[tauri::command]
async fn overview() -> Result<Value, String> {
    call_api(reqwest::Method::GET, "/v1/overview", None).await
}

#[tauri::command]
async fn prefetch_pool() -> Result<Value, String> {
    call_api(reqwest::Method::POST, "/v1/pool/prefetch", None).await
}

#[tauri::command]
fn store_token(token: String) {
    if let Ok(mut cache) = TOKEN.lock() {
        *cache = Some(token);
    }
}

#[tauri::command]
fn clear_token() {
    if let Ok(mut cache) = TOKEN.lock() {
        *cache = None;
    }
    if let Ok(mut cache) = REFRESH_TOKEN.lock() {
        *cache = None;
    }
}

#[tauri::command]
fn is_authenticated() -> bool {
    TOKEN.lock().map(|cache| cache.is_some()).unwrap_or(false)
}

/// Interactive Cognito Hosted UI login (system browser + loopback PKCE).
#[tauri::command]
async fn auth_login() -> Result<Value, String> {
    let tokens = auth::login().await?;
    let sub = auth::sub_from_id_token(&tokens.id_token);
    if let Some(user) = &sub {
        store::set_user(user)?;
        if !tokens.refresh_token.is_empty() {
            let _ = secrets::save(user, &tokens.refresh_token);
        }
    }
    store_tokens(tokens.id_token, tokens.refresh_token);
    Ok(json!({ "authenticated": true, "user_id": sub }))
}

/// Restore a session on startup by silently refreshing the stored refresh token.
#[tauri::command]
async fn auth_restore() -> Result<Value, String> {
    if TOKEN.lock().map(|cache| cache.is_some()).unwrap_or(false) {
        return Ok(json!({ "authenticated": true }));
    }
    let user = store::get_user()?.unwrap_or_default();
    if user.is_empty() {
        return Ok(json!({ "authenticated": false }));
    }
    let Some(refresh_token) = secrets::load(&user) else {
        return Ok(json!({ "authenticated": false }));
    };
    match auth::refresh(&refresh_token).await {
        Ok(tokens) => {
            if !tokens.refresh_token.is_empty() {
                let _ = secrets::save(&user, &tokens.refresh_token);
            }
            store_tokens(tokens.id_token, tokens.refresh_token);
            Ok(json!({ "authenticated": true, "user_id": user }))
        }
        Err(_) => {
            secrets::clear(&user);
            Ok(json!({ "authenticated": false }))
        }
    }
}

#[tauri::command]
fn auth_logout() -> Value {
    if let Ok(Some(user)) = store::get_user() {
        secrets::clear(&user);
    }
    if let Ok(mut cache) = TOKEN.lock() {
        *cache = None;
    }
    if let Ok(mut cache) = REFRESH_TOKEN.lock() {
        *cache = None;
    }
    json!({ "authenticated": false })
}

#[tauri::command]
fn auth_status() -> Value {
    let authenticated = TOKEN.lock().map(|cache| cache.is_some()).unwrap_or(false);
    let has_refresh = REFRESH_TOKEN
        .lock()
        .map(|cache| cache.as_deref().is_some_and(|value| !value.is_empty()))
        .unwrap_or(false);
    json!({ "authenticated": authenticated, "has_refresh_token": has_refresh })
}

/// Dev-only echo used by the health check.
#[tauri::command]
fn ping() -> Value {
    json!({ "status": "ok", "runtime": "tauri" })
}

// --- Offline store (read cache + review outbox) ---

/// Progress records currently cached on disk (works offline).
#[tauri::command]
fn cached_progress() -> Result<Value, String> {
    Ok(json!(store::cached_progress()?))
}

/// Fetch the authoritative progress from the API and refresh the local cache.
#[tauri::command]
async fn sync_progress() -> Result<Value, String> {
    let records = call_api(reqwest::Method::GET, "/v1/progress", None).await?;
    let array = records
        .as_array()
        .ok_or_else(|| "unexpected progress payload".to_string())?
        .clone();
    store::cache_records(&array)?;
    Ok(records)
}

/// Queue a review locally (optimistic, works offline). Synced by `sync_outbox`.
#[tauri::command]
fn local_review(term_id: String, update: Value) -> Result<Value, String> {
    let rating = update.get("rating").and_then(Value::as_i64).unwrap_or(3);
    let note = update
        .get("note")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let starred = update
        .get("starred")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    store::enqueue_review(&term_id, rating, note, starred)?;
    Ok(json!({ "queued": true, "pending": store::pending_count()? }))
}

/// Drain the outbox to the API; the server's FSRS result replaces the cached record.
#[tauri::command]
async fn sync_outbox() -> Result<Value, String> {
    let pending = store::pending_reviews()?;
    let mut synced = 0;
    for item in pending {
        let path = format!("/v1/progress/{}", item.term_id);
        let body = json!({
            "rating": item.rating,
            "note": item.note,
            "starred": item.starred,
        });
        let record = call_api(reqwest::Method::PUT, &path, Some(body)).await?;
        store::upsert_cached(&record)?;
        store::mark_synced(item.id)?;
        synced += 1;
    }
    Ok(json!({ "synced": synced, "pending": store::pending_count()? }))
}

#[tauri::command]
fn pending_review_count() -> Result<i64, String> {
    store::pending_count()
}

/// Remember which user the local cache belongs to.
#[tauri::command]
fn set_current_user(user_id: String) -> Result<(), String> {
    store::set_user(&user_id)
}

#[tauri::command]
fn current_user() -> Result<Option<String>, String> {
    store::get_user()
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let data_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::path::PathBuf::from("."));
            let db_path = data_dir.join("lexicon.db");
            store::init(&db_path).map_err(Box::<dyn std::error::Error>::from)?;
            secrets::init(data_dir);
            // Dev tools stay closed at start (right-click → Inspect still works in debug builds);
            // set LEXICON_DEVTOOLS=1 to open them automatically.
            #[cfg(debug_assertions)]
            if std::env::var("LEXICON_DEVTOOLS").is_ok_and(|v| v == "1") {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            search,
            tutor,
            list_progress,
            review_term,
            stats,
            store_token,
            clear_token,
            is_authenticated,
            auth_login,
            auth_logout,
            auth_status,
            auth_restore,
            list_decks,
            start_deck,
            review_due,
            quiz,
            grade_quiz,
            start_session,
            next_exercise,
            answer_exercise,
            explain_answer,
            lesson,
            pronounce,
            import_words,
            overview,
            prefetch_pool,
            cached_progress,
            sync_progress,
            local_review,
            sync_outbox,
            pending_review_count,
            set_current_user,
            current_user,
            ping,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Lexicon");
}

#[cfg(test)]
mod tests {
    use super::session_path;

    #[test]
    fn session_path_accepts_server_ids_only() {
        assert_eq!(
            session_path("bf4fd63dbf06", "next").unwrap(),
            "/v1/session/bf4fd63dbf06/next"
        );
        assert!(session_path("", "next").is_err());
        assert!(session_path("../progress", "next").is_err());
        assert!(session_path("abc?x=1", "answer").is_err());
    }
}
