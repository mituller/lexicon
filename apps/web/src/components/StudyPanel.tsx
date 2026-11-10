import { useEffect, useRef, useState, type FormEvent } from "react";

import { playWord } from "../lib/audio";
import { useSessionStore } from "../stores/useSessionStore";
import { useVocabStore } from "../stores/useVocabStore";
import type { AnswerResult, EntryView, Exercise, Lesson } from "../lib/types";

const BLANK = "_____";

export function StudyPanel() {
  const screen = useSessionStore((state) => state.screen);
  if (screen === "session") return <SessionScreen />;
  if (screen === "summary") return <SummaryScreen />;
  return <HomeScreen />;
}

function HomeScreen() {
  const stats = useVocabStore((state) => state.stats);
  const { start, loading, error } = useSessionStore();
  const due = stats?.due_today ?? 0;

  return (
    <section className="panel home">
      <h2>{stats?.level == null ? "Find your level" : "Ready for a session?"}</h2>
      <p className="tagline">
        {stats?.level == null
          ? "Your first sessions place you: words get harder as you get them right."
          : `${due} review${due === 1 ? "" : "s"} due, plus a few new words just above your level.`}
      </p>
      {error && <p className="error">{error}</p>}
      <button className="primary big" type="button" disabled={loading} onClick={() => void start()}>
        {loading ? "Preparing words…" : "Start session"}
      </button>
      {loading && (
        <p className="muted">New words are written on demand, so the first start can take a few seconds.</p>
      )}
    </section>
  );
}

function SessionScreen() {
  const { exercise, feedback, loading, error, submit, next, quit } = useSessionStore();

  // 1-4 picks an option; Enter continues once the answer has been graded.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      // Inputs and focused buttons handle their own keys (Enter on "Why?" must not skip ahead).
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLButtonElement && event.key === "Enter")
      ) {
        return;
      }
      if (feedback && event.key === "Enter") {
        event.preventDefault();
        void next();
      } else if (!feedback && exercise?.options.length) {
        const index = Number(event.key) - 1;
        if (index >= 0 && index < exercise.options.length) void submit({ choice: index });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exercise, feedback, next, submit]);

  return (
    <section className="panel">
      <div className="progress-line">
        <span>
          {exercise ? `${exercise.remaining} word${exercise.remaining === 1 ? "" : "s"} left` : ""}
          {exercise && <StageBadge exercise={exercise} />}
        </span>
        <button type="button" className="ghost" onClick={quit}>
          End session
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      {!exercise && loading && <p className="muted">Loading…</p>}
      {exercise && <ExerciseCard exercise={exercise} feedback={feedback} />}
      {feedback && <Feedback result={feedback} />}
    </section>
  );
}

function StageBadge({ exercise }: { exercise: Exercise }) {
  if (exercise.stage === "confirm") return <span className="badge">quick check</span>;
  if (exercise.stage === "retry") return <span className="badge">try again</span>;
  return exercise.kind === "new" ? <span className="badge">new word</span> : null;
}

function ExerciseCard({ exercise, feedback }: { exercise: Exercise; feedback: AnswerResult | null }) {
  const submit = useSessionStore((state) => state.submit);
  const loading = useSessionStore((state) => state.loading);
  const nudge = useSessionStore((state) => state.nudge);

  return (
    <div className="exercise">
      <p className="instruction">{instruction(exercise)}</p>
      <Prompt exercise={exercise} />

      {exercise.type === "type_word" ? (
        <TypedAnswer key={exercise.exercise_id} hint={exercise.hint} disabled={!!feedback || loading} />
      ) : exercise.type === "use_word" ? (
        <SentenceAnswer key={exercise.exercise_id} term={exercise.prompt} disabled={!!feedback || loading} />
      ) : (
        <ol className="options">
          {exercise.options.map((option, index) => (
            <li key={option}>
              <button
                type="button"
                className={optionClass(option, feedback)}
                disabled={!!feedback || loading}
                onClick={() => void submit({ choice: index })}
              >
                <span className="key">{index + 1}</span>
                <span>{option}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
      {nudge && !feedback && <p className="nudge">{nudge}</p>}
    </div>
  );
}

function instruction(exercise: Exercise): string {
  switch (exercise.type) {
    case "choose_definition":
      return "Which definition fits?";
    case "choose_word":
      return "Which word means this?";
    case "cloze":
      return "Which word completes the sentence?";
    case "type_word":
      return "Type the word that means this:";
    case "nuance":
      return "Which word fits best? They're close in meaning; one is right here.";
    case "use_word":
      return "Use this word in a sentence of your own:";
  }
}

function Prompt({ exercise }: { exercise: Exercise }) {
  if (exercise.type === "choose_definition" || exercise.type === "use_word") {
    return (
      <p className="prompt-term">
        {exercise.prompt} <SpeakButton word={exercise.prompt} />
        {exercise.type === "use_word" && exercise.hint && <span className="muted pos">{exercise.hint}</span>}
      </p>
    );
  }
  if (exercise.type === "cloze" || exercise.type === "nuance") {
    const [before, after] = exercise.prompt.split(BLANK);
    return (
      <>
        <p className="prompt-sentence">
          {before}
          <span className="blank" aria-label="blank" />
          {after}
        </p>
        {exercise.hint && <p className="muted attribution">{exercise.hint}</p>}
      </>
    );
  }
  return <p className="prompt-definition">{exercise.prompt}</p>;
}

function optionClass(option: string, feedback: AnswerResult | null): string {
  if (!feedback) return "option";
  if (option === feedback.correct_answer) return "option right";
  if (option === feedback.chosen) return "option wrong";
  return "option";
}

function TypedAnswer({ hint, disabled }: { hint: string; disabled: boolean }) {
  const submit = useSessionStore((state) => state.submit);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), []);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit({ text });
  }

  return (
    <form className="typed" onSubmit={onSubmit}>
      <input
        ref={input}
        className="search-input"
        value={text}
        placeholder={hint}
        autoCapitalize="off"
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
      />
      <button className="primary" type="submit" disabled={disabled}>
        Check
      </button>
    </form>
  );
}

function SentenceAnswer({ term, disabled }: { term: string; disabled: boolean }) {
  const submit = useSessionStore((state) => state.submit);
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => input.current?.focus(), []);

  function send() {
    if (text.trim()) void submit({ text });
  }

  return (
    <form
      className="typed sentence"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        ref={input}
        className="search-input"
        value={text}
        rows={3}
        maxLength={400}
        placeholder={`A sentence that shows what “${term}” means… (⌘↵ to check)`}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            send();
          }
        }}
      />
      <button className="primary" type="submit" disabled={disabled || !text.trim()}>
        Check
      </button>
    </form>
  );
}

function verdict(result: AnswerResult, type: Exercise["type"] | undefined): string {
  if (type === "use_word") {
    if (!result.correct) return "Not quite — see the note below.";
    return result.typo ? "Nearly — the meaning's right; see the note." : "Well used.";
  }
  if (result.correct) {
    return result.typo ? `Accepted — watch the spelling: ${result.correct_answer}` : "Correct";
  }
  return result.correct_answer === result.entry?.definition
    ? "Not quite — the right definition is highlighted."
    : `Not quite — the answer was “${result.correct_answer}”`;
}

function Feedback({ result }: { result: AnswerResult }) {
  const { next, explain, explanation, explaining, loading, lesson, exercise } = useSessionStore();
  const continueButton = useRef<HTMLButtonElement>(null);

  useEffect(() => continueButton.current?.focus(), []);

  return (
    <div className={`feedback ${result.correct ? "is-right" : "is-wrong"}`}>
      <p className="verdict">{verdict(result, exercise?.type)}</p>
      <p className="muted">{nextStepText(result)}</p>

      {result.feedback && <p className="grader">{result.feedback}</p>}
      {result.improved && (
        <p className="improved">
          <span className="muted">Better: </span>“{result.improved}”
        </p>
      )}

      {lesson && <LessonCard lesson={lesson} />}

      {result.entry && <Entry entry={result.entry} />}

      {!result.correct && !result.feedback && (
        <div className="explain">
          {explanation ? (
            <p>{explanation}</p>
          ) : (
            <button type="button" disabled={explaining} onClick={() => void explain()}>
              {explaining ? "Thinking…" : "Why was that wrong?"}
            </button>
          )}
        </div>
      )}

      {result.entry && <TutorBox term={result.entry.term} />}

      <button
        ref={continueButton}
        className="primary"
        type="button"
        disabled={loading}
        onClick={() => void next()}
      >
        {result.summary ? "See results" : "Continue"} <span className="muted">↵</span>
      </button>
    </div>
  );
}

function nextStepText(result: AnswerResult): string {
  if (result.next_step === "confirm") return "It will come back once more, to make sure it wasn't a guess.";
  if (result.next_step === "requeued") return "It will come back in a few cards.";
  return result.correct ? "" : "Moving on; it will come back in a later session.";
}

function SpeakButton({ word }: { word: string }) {
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");
  return (
    <button
      type="button"
      className="speak"
      title={state === "failed" ? "Pronunciation unavailable" : `Hear “${word}”`}
      aria-label={`Pronounce ${word}`}
      disabled={state === "loading"}
      onClick={() => {
        setState("loading");
        playWord(word).then(
          () => setState("idle"),
          () => setState("failed"),
        );
      }}
    >
      {state === "failed" ? "🔇" : "🔊"}
    </button>
  );
}

function LessonCard({ lesson }: { lesson: Lesson }) {
  if (!lesson.ipa && !lesson.parts && !lesson.hook) return null;
  return (
    <div className="lesson">
      <p className="lesson-title">
        Make “{lesson.term}” stick {lesson.ipa && <span className="muted ipa">{lesson.ipa}</span>}
      </p>
      {lesson.parts && (
        <p>
          <span className="muted">Parts: </span>
          {lesson.parts}
        </p>
      )}
      {lesson.hook && (
        <p>
          <span className="muted">Remember it: </span>
          {lesson.hook}
        </p>
      )}
    </div>
  );
}

function Entry({ entry }: { entry: EntryView }) {
  return (
    <div className="entry">
      <p className="term">
        {entry.term} <SpeakButton word={entry.term} /> {entry.pos && <span className="muted">{entry.pos}</span>}
      </p>
      <p className="definition">{entry.definition}</p>
      {entry.met_in && (
        <p className="met-in">
          <span className="muted">Where you met it: </span>“{entry.met_in}”
        </p>
      )}
      {entry.examples.map((example) => (
        <p key={example} className="example">
          “{example}”
        </p>
      ))}
      {entry.synonyms.length > 0 && <p className="muted">Synonyms: {entry.synonyms.join(", ")}</p>}
      {entry.confused_with.length > 0 && (
        <p className="muted">Often confused with: {entry.confused_with.join(", ")}</p>
      )}
      {entry.etymology && <p className="muted">Origin: {entry.etymology}</p>}
    </div>
  );
}

function TutorBox({ term }: { term: string }) {
  const { askTutor, tutorAnswer, askingTutor } = useSessionStore();
  const [question, setQuestion] = useState("");

  return (
    <details className="tutor">
      <summary>Ask the tutor about “{term}”</summary>
      <form
        className="search-form"
        onSubmit={(event) => {
          event.preventDefault();
          void askTutor(question);
        }}
      >
        <input
          className="search-input"
          value={question}
          placeholder="e.g. How is it different from a synonym? Use it in a formal sentence."
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={askingTutor || !question.trim()}>
          {askingTutor ? "…" : "Ask"}
        </button>
      </form>
      {tutorAnswer && <p className="tutor-answer">{tutorAnswer}</p>}
    </details>
  );
}

function SummaryScreen() {
  const { summary, start, quit, loading } = useSessionStore();
  if (!summary) return null;
  const delta = summary.level_end - summary.level_start;

  return (
    <section className="panel">
      <h2>Session complete</h2>
      <div className="stats">
        <Stat value={summary.already_known} label="already knew" />
        <Stat value={summary.learned} label="new words learned" />
        <Stat value={summary.reviews_remembered} label="reviews remembered" />
        <Stat value={summary.reviews_relearned} label="reviews relearned" />
      </div>
      <p>
        Level {summary.level_start.toFixed(0)} → <strong>{summary.level_end.toFixed(0)}</strong>{" "}
        <span className="muted">
          ({delta >= 0 ? "+" : ""}
          {delta.toFixed(1)})
        </span>
      </p>
      {summary.gave_up > 0 && (
        <p className="muted">
          {summary.gave_up} word{summary.gave_up === 1 ? "" : "s"} still shaky; they'll be back soon.
        </p>
      )}
      <div className="review-buttons">
        <button className="primary" type="button" disabled={loading} onClick={() => void start()}>
          Another session
        </button>
        <button type="button" onClick={quit}>
          Done
        </button>
      </div>
    </section>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
