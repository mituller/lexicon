//! Deployment settings (API URL, Cognito Hosted UI domain and app client id).
//!
//! An app launched from Finder, the Dock or the Start menu does not inherit shell exports, so
//! each setting is compiled in from the environment of `tauri build` (see build.rs). A variable set
//! at run time still wins, which keeps `export LEXICON_...` working for development. None of these
//! are secrets: the Cognito app client is public (PKCE, no client secret), and the web build ships
//! the same values.

/// A runtime environment variable, or else the value compiled in at build time.
pub(crate) fn setting(name: &str, compiled: Option<&'static str>) -> Option<String> {
    std::env::var(name)
        .ok()
        .or_else(|| compiled.map(str::to_string))
        .filter(|value| !value.trim().is_empty())
}

/// Like [`setting`], but with an error that says how to fix a build that lacks it.
pub(crate) fn required(name: &str, compiled: Option<&'static str>) -> Result<String, String> {
    setting(name, compiled).ok_or_else(|| {
        format!(
            "{name} is not set. This build of Lexicon was made without it: set {name} \
             (from the CDK stack outputs) when running `tauri build`, or export it before \
             launching the app."
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn falls_back_to_compiled_value() {
        assert_eq!(
            setting("LEXICON_TEST_UNSET_SETTING", Some("baked")).as_deref(),
            Some("baked")
        );
    }

    #[test]
    fn missing_setting_names_the_variable() {
        let error = required("LEXICON_TEST_UNSET_SETTING", None).unwrap_err();
        assert!(error.starts_with("LEXICON_TEST_UNSET_SETTING is not set"));
    }

    #[test]
    fn blank_compiled_value_counts_as_missing() {
        assert!(setting("LEXICON_TEST_UNSET_SETTING", Some("  ")).is_none());
    }
}
