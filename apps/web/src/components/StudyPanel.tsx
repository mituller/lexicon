import { useEffect } from "react";

import { useStudyStore } from "../stores/useStudyStore";
import type { Rating } from "../lib/types";

const RATINGS: { label: string; value: Rating }[] = [
  { label: "Again", value: 1 },
  { label: "Hard", value: 2 },
  { label: "Good", value: 3 },
  { label: "Easy", value: 4 },
];

export function StudyPanel() {
  const { screen, decks, loading, error, loadDecks, startDeck, loadQuiz } = useStudyStore();

  useEffect(() => {
    void loadDecks();
  }, [loadDecks]);

  if (screen === "study") return <StudyScreen />;
  if (screen === "quiz") return <QuizScreen />;

  return (
    <section className="panel">
      <h2>Vocabulary sets</h2>
      <p className="tagline">Pick a set, go through the flashcards, then test your retention.</p>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      <ul className="decks">
        {decks.map((deck) => (
          <li key={`${deck.domain}:${deck.difficulty_tier}`} className="deck">
            <div className="deck-meta">
              <span className="deck-title">
                {deck.domain === "general" ? "General English" : "AI / Technical"}
              </span>
              <span className="badges">
                <span className="badge">{deck.difficulty_tier}</span>
                {deck.cefr_levels.map((level) => (
                  <span key={level} className="badge">
                    {level}
                  </span>
                ))}
              </span>
              <span className="muted">{deck.count} terms</span>
            </div>
            <div className="deck-actions">
              <button
                className="primary"
                type="button"
                disabled={loading}
                onClick={() => void startDeck(deck.domain, deck.difficulty_tier)}
              >
                Study
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={() => void loadQuiz(deck.domain, deck.difficulty_tier)}
              >
                Quiz
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function StudyScreen() {
  const cardIndex = useStudyStore((state) => state.cardIndex);
  const cards = useStudyStore((state) => state.cards);
  const revealed = useStudyStore((state) => state.revealed);
  const reveal = useStudyStore((state) => state.reveal);
  const rate = useStudyStore((state) => state.rate);
  const backToDecks = useStudyStore((state) => state.backToDecks);

  const card = cards[cardIndex];
  if (!card) {
    return (
      <section className="panel">
        <h2>All done 🎉</h2>
        <p className="tagline">No terms are due right now. Pick another set.</p>
        <button className="primary" type="button" onClick={backToDecks}>
          Back to sets
        </button>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="progress-line">
        <span>
          Card {cardIndex + 1} of {cards.length}
        </span>
        <button type="button" className="ghost" onClick={backToDecks}>
          ← Back to sets
        </button>
      </div>

      {!revealed ? (
        <button className="card" type="button" onClick={reveal}>
          <span className="card-front">{card.term}</span>
          <span className="muted hint">Tap to reveal the definition</span>
        </button>
      ) : (
        <>
          <div className="card revealed">
            <span className="card-front">{card.term}</span>
            <span className="card-back">
              <span className="definition">{card.definition}</span>
              {card.example && <span className="example">“{card.example}”</span>}
              {card.synonyms && card.synonyms.length > 0 && (
                <span className="muted">synonyms: {card.synonyms.join(", ")}</span>
              )}
            </span>
          </div>
          <p className="rate-prompt">How well did you know it?</p>
          <div className="review-buttons">
            {RATINGS.map((rating) => (
              <button key={rating.value} type="button" onClick={() => void rate(rating.value)}>
                {rating.label}
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function QuizScreen() {
  const quiz = useStudyStore((state) => state.quiz);
  const quizAnswers = useStudyStore((state) => state.quizAnswers);
  const quizScore = useStudyStore((state) => state.quizScore);
  const loading = useStudyStore((state) => state.loading);
  const answerQuiz = useStudyStore((state) => state.answerQuiz);
  const submitQuiz = useStudyStore((state) => state.submitQuiz);
  const backToDecks = useStudyStore((state) => state.backToDecks);

  if (!quiz) return null;

  if (quizScore) {
    const pct = quizScore.total ? Math.round((quizScore.correct / quizScore.total) * 100) : 0;
    return (
      <section className="panel">
        <h2>Quiz result</h2>
        <p className="score">
          {quizScore.correct} / {quizScore.total} correct ({pct}%)
        </p>
        <button className="primary" type="button" onClick={backToDecks}>
          Back to sets
        </button>
      </section>
    );
  }

  const answered = quizAnswers.filter((answer) => answer !== null).length;

  return (
    <section className="panel">
      <h2>Quiz — {quiz.domain === "general" ? "General English" : "AI / Technical"}</h2>
      <p className="muted">
        {answered} of {quiz.questions.length} answered
      </p>
      {quiz.questions.map((question, index) => (
        <div key={question.term_id} className="quiz-question">
          <p className="term">
            {index + 1}. {question.term}
          </p>
          {question.options.map((option, optionIndex) => {
            const selected = quizAnswers[index] !== null && quizAnswers[index] !== undefined;
            const chosen = selected && quizAnswers[index] === (optionIndex === question.answer_index);
            return (
              <label key={optionIndex} className="quiz-option">
                <input
                  type="radio"
                  name={`q-${index}`}
                  disabled={selected}
                  onChange={() => answerQuiz(index, optionIndex === question.answer_index)}
                />
                <span className={chosen ? "chosen" : ""}>{option}</span>
              </label>
            );
          })}
        </div>
      ))}
      <button
        className="primary"
        type="button"
        disabled={loading || answered < quiz.questions.length}
        onClick={() => void submitQuiz()}
      >
        Submit
      </button>
      {loading && <span className="muted"> grading…</span>}
    </section>
  );
}