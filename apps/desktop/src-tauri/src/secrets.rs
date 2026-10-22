//! Persistence for the Cognito refresh token.
//!
//! Uses the OS keychain (macOS Keychain / Windows Credential Manager) when available and falls
//! back to a `0600` file in the app data directory on other platforms (e.g. Linux CI). Only the
//! **refresh token** is stored; the short-lived ID token stays in memory.

use std::path::PathBuf;
use std::sync::OnceLock;

const SERVICE: &str = "com.lexicon.app";
static DIR: OnceLock<PathBuf> = OnceLock::new();

/// Set the directory used by the file fallback.
pub fn init(dir: PathBuf) {
    let _ = DIR.set(dir);
}

fn file_path(user: &str) -> PathBuf {
    let base = DIR.get().cloned().unwrap_or_else(|| PathBuf::from("."));
    base.join(format!("refresh-{}.token", sanitize(user)))
}

fn sanitize(user: &str) -> String {
    user.chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect()
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn keychain_save(user: &str, token: &str) -> Result<(), String> {
    keyring::Entry::new(SERVICE, user)
        .and_then(|entry| entry.set_password(token))
        .map_err(|error| error.to_string())
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn keychain_load(user: &str) -> Option<String> {
    keyring::Entry::new(SERVICE, user)
        .and_then(|entry| entry.get_password())
        .ok()
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn keychain_clear(user: &str) -> Result<(), String> {
    keyring::Entry::new(SERVICE, user)
        .and_then(|entry| entry.delete_credential())
        .map_err(|error| error.to_string())
}

/// Store the refresh token (keychain if possible, else a restricted file).
pub fn save(user: &str, token: &str) -> Result<(), String> {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        if keychain_save(user, token).is_ok() {
            return Ok(());
        }
    }
    write_file(user, token)
}

/// Load the refresh token, if one was stored.
pub fn load(user: &str) -> Option<String> {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        if let Some(token) = keychain_load(user) {
            return Some(token);
        }
    }
    std::fs::read_to_string(file_path(user))
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

/// Remove the stored refresh token.
pub fn clear(user: &str) {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        let _ = keychain_clear(user);
    }
    let _ = std::fs::remove_file(file_path(user));
}

fn write_file(user: &str, token: &str) -> Result<(), String> {
    let path = file_path(user);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    std::fs::write(&path, token).map_err(|error| error.to_string())?;
    restrict_permissions(&path);
    Ok(())
}

#[cfg(unix)]
fn restrict_permissions(path: &std::path::Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn restrict_permissions(_path: &std::path::Path) {}
