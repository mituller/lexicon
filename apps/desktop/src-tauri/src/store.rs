//! Local SQLite store: a read cache of progress plus an offline outbox of review events.
//!
//! FSRS stays in Python, so Rust never schedules reviews. It caches what the API returns and
//! queues review intents (`term_id`, rating) while offline; `sync_outbox` drains them and
//! writes the server's authoritative response back into the cache.

use std::path::Path;
use std::sync::{Mutex, OnceLock};

use chrono::Utc;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;

static DB: OnceLock<Mutex<Connection>> = OnceLock::new();

const SCHEMA: &str = "\
CREATE TABLE IF NOT EXISTS progress (
    term_id    TEXT PRIMARY KEY,
    json       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id    TEXT NOT NULL,
    rating     INTEGER NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    starred    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    synced     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
";

/// A queued review that still needs to reach the API.
#[derive(Debug, Serialize, Deserialize)]
pub struct PendingReview {
    pub id: i64,
    pub term_id: String,
    pub rating: i64,
    pub note: String,
    pub starred: bool,
}

/// Open (or create) the database and run migrations.
pub fn init(path: &Path) -> Result<(), String> {
    if DB.get().is_some() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let connection = Connection::open(path).map_err(|error| error.to_string())?;
    connection
        .execute_batch(SCHEMA)
        .map_err(|error| error.to_string())?;
    let _ = DB.set(Mutex::new(connection));
    Ok(())
}

fn connection() -> Result<std::sync::MutexGuard<'static, Connection>, String> {
    DB.get()
        .ok_or_else(|| "local store is not initialised".to_string())?
        .lock()
        .map_err(|error| error.to_string())
}

// --- meta (current user) ---

pub fn set_user(user_id: &str) -> Result<(), String> {
    connection()?
        .execute(
            "INSERT INTO meta (key, value) VALUES ('user_id', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![user_id],
        )
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn get_user() -> Result<Option<String>, String> {
    let conn = connection()?;
    let mut statement = conn
        .prepare("SELECT value FROM meta WHERE key = 'user_id'")
        .map_err(|error| error.to_string())?;
    let mut rows = statement.query([]).map_err(|error| error.to_string())?;
    match rows.next().map_err(|error| error.to_string())? {
        Some(row) => Ok(Some(row.get(0).map_err(|error| error.to_string())?)),
        None => Ok(None),
    }
}

// --- progress cache ---

/// Replace cached progress with the server's authoritative records.
pub fn cache_records(records: &[Value]) -> Result<usize, String> {
    let now = Utc::now().to_rfc3339();
    let conn = connection()?;
    conn.execute("DELETE FROM progress", [])
        .map_err(|error| error.to_string())?;
    let mut statement = conn
        .prepare("INSERT INTO progress (term_id, json, updated_at) VALUES (?1, ?2, ?3)")
        .map_err(|error| error.to_string())?;
    let mut count = 0;
    for record in records {
        let term_id = record
            .get("term_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if term_id.is_empty() {
            continue;
        }
        statement
            .execute(params![term_id, record.to_string(), now])
            .map_err(|error| error.to_string())?;
        count += 1;
    }
    Ok(count)
}

/// Insert or update a single cached record (after a successful sync).
pub fn upsert_cached(record: &Value) -> Result<(), String> {
    let term_id = record
        .get("term_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "record has no term_id".to_string())?;
    connection()?
        .execute(
            "INSERT INTO progress (term_id, json, updated_at) VALUES (?1, ?2, ?3)
             ON CONFLICT(term_id) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at",
            params![term_id, record.to_string(), Utc::now().to_rfc3339()],
        )
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn cached_progress() -> Result<Vec<Value>, String> {
    let conn = connection()?;
    let mut statement = conn
        .prepare("SELECT json FROM progress ORDER BY updated_at DESC")
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?;
    let mut records = Vec::new();
    for row in rows {
        let text = row.map_err(|error| error.to_string())?;
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            records.push(value);
        }
    }
    Ok(records)
}

// --- outbox ---

/// Queue a review intent for later sync, with an optimistic local flag.
pub fn enqueue_review(term_id: &str, rating: i64, note: &str, starred: bool) -> Result<(), String> {
    connection()?
        .execute(
            "INSERT INTO outbox (term_id, rating, note, starred, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                term_id,
                rating,
                note,
                if starred { 1 } else { 0 },
                Utc::now().to_rfc3339()
            ],
        )
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn pending_reviews() -> Result<Vec<PendingReview>, String> {
    let conn = connection()?;
    let mut statement = conn
        .prepare(
            "SELECT id, term_id, rating, note, starred FROM outbox
             WHERE synced = 0 ORDER BY id ASC",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| {
            Ok(PendingReview {
                id: row.get(0)?,
                term_id: row.get(1)?,
                rating: row.get(2)?,
                note: row.get(3)?,
                starred: row.get::<_, i64>(4)? != 0,
            })
        })
        .map_err(|error| error.to_string())?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())
}

pub fn mark_synced(id: i64) -> Result<(), String> {
    connection()?
        .execute("UPDATE outbox SET synced = 1 WHERE id = ?1", params![id])
        .map_err(|error| error.to_string())?;
    Ok(())
}

pub fn pending_count() -> Result<i64, String> {
    connection()?
        .query_row("SELECT COUNT(*) FROM outbox WHERE synced = 0", [], |row| {
            row.get(0)
        })
        .map_err(|error| error.to_string())
}
