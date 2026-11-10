// Import words from the learner's own reading: paste text or give a link. The server picks the
// words worth learning (at or above the learner's level) and queues them for the next sessions.

import { useState, type FormEvent } from "react";

import { getTransport } from "../lib/transport";
import type { ImportResult } from "../lib/types";
import { useVocabStore } from "../stores/useVocabStore";

type Mode = "text" | "url";

export function ImportPanel() {
  const [mode, setMode] = useState<Mode>("text");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const ready = mode === "text" ? text.trim().split(/\s+/).length >= 5 : /^https?:\/\/\S+$/.test(url.trim());

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const imported = await getTransport().importWords(mode === "text" ? { text } : { url: url.trim() });
      setResult(imported);
      void useVocabStore.getState().loadStats();
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>Add words from your reading</h2>
      <p className="tagline">
        Words you meet in things you actually read stick best. Paste a passage or a link; Lexicon picks the
        words worth learning at your level and puts them first in your next sessions.
      </p>

      <div className="segmented" role="tablist">
        <button type="button" className={mode === "text" ? "active" : ""} onClick={() => setMode("text")}>
          Paste text
        </button>
        <button type="button" className={mode === "url" ? "active" : ""} onClick={() => setMode("url")}>
          From a link
        </button>
      </div>

      <form className="import-form" onSubmit={(event) => void onSubmit(event)}>
        {mode === "text" ? (
          <textarea
            className="search-input"
            rows={8}
            value={text}
            maxLength={50000}
            placeholder="Paste a paragraph or two from an article, book or email…"
            onChange={(event) => setText(event.target.value)}
          />
        ) : (
          <input
            className="search-input"
            type="url"
            value={url}
            placeholder="https://…"
            onChange={(event) => setUrl(event.target.value)}
          />
        )}
        <button className="primary" type="submit" disabled={!ready || busy}>
          {busy ? "Finding words…" : "Find words"}
        </button>
      </form>
      {busy && <p className="muted">New words get a full entry written for them, so this can take up to half a minute.</p>}
      {error && <p className="error">{error}</p>}
      {result && <ImportSummary result={result} />}
    </section>
  );
}

function ImportSummary({ result }: { result: ImportResult }) {
  const skipped = [
    result.already_studying.length > 0 && `already studying: ${result.already_studying.join(", ")}`,
    result.too_easy.length > 0 && `below your level: ${result.too_easy.join(", ")}`,
    result.rejected.length > 0 && `not added: ${result.rejected.join(", ")}`,
  ].filter(Boolean);

  return (
    <div className="import-result">
      {result.added.length === 0 ? (
        <p>No new words to add from {result.source}.</p>
      ) : (
        <>
          <p>
            Added {result.added.length} word{result.added.length === 1 ? "" : "s"} from{" "}
            <span className="muted">{result.source}</span>. They'll come first in your next sessions.
          </p>
          <ul className="word-list">
            {result.added.map((word) => (
              <li key={word.term_id}>
                <strong>{word.term}</strong> <span className="muted">— {word.definition}</span>
                {word.context && <p className="example">“{word.context}”</p>}
              </li>
            ))}
          </ul>
        </>
      )}
      {skipped.map((line) => (
        <p key={line as string} className="muted">
          {line}
        </p>
      ))}
    </div>
  );
}

/** The Rust shell reports API errors as `API error 422 …: {"detail": "…"}`; show just the detail. */
function readableError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  const detail = /"detail"\s*:\s*"([^"]+)"/.exec(text);
  return detail ? detail[1] : text;
}
