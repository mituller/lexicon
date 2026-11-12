fn main() {
    // Deployment settings compiled into the app (see src/config.rs); rebuild when they change.
    for name in [
        "LEXICON_API_BASE",
        "LEXICON_COGNITO_DOMAIN",
        "LEXICON_COGNITO_CLIENT_ID",
    ] {
        println!("cargo:rerun-if-env-changed={name}");
    }
    tauri_build::build()
}
