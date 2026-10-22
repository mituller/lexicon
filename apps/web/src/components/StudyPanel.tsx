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
  const {
    screen,
    decks,
    cards,
    cardIndex,
    revealed,
    quiz,
    quizAnswers,
    quizScore,
    loading,
    error,
    loadDecks,
    startDeck,
    reveal,
    rate,
    loadQuiz,
    answerQuiz,
    submitQuiz,
    backToDecks,
  } = useStudyStore();

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

  function StudyScreen() {
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
            {cardIndex + 1} / {cards.length}
          </span>
          <span className="badges">
            {card.cefr_level && <span className="badge">{card.cefr_level}</span>}
            {card.difficulty_tier && <span className="badge">{card.difficulty_tier}</span>}
          </span>
        </div>

        <button className="card" type="button" onClick={reveal}>
          <span className="card-front">{card.term}</span>
          {revealed && (
            <span className="card-back">
              <span className="definition">{card.definition}</span>
              {card.example && <span className="example">“{card.example}”</span>}
              {card.synonyms && card.synonyms.length > 0 && (
                <span className="muted">synonyms: {card.synonyms.join(", ")}</span>
              )}
            </span>
          )}
          {!revealed && <span className="muted hint">tap to reveal</span>}
        </button>

        {revealed && (
          <div className="review-buttons">
            {RATINGS.map((rating) => (
              <button key={rating.value} type="button" onClick={() => void rate(rating.value)}>
                {rating.label}
              </button>
            ))}
          </div>
        )}
      </section>
    );
  }

  function QuizScreen() {
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

    return (
      <section className="panel">
        <h2>Quiz — {quiz.domain === "general" ? "General English" : "AI / Technical"}</h2>
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
          disabled={loading || quizAnswers.filter((answer) => answer !== null).length < quiz.questions.length}
          onClick={() => void submitQuiz()}
        >
          Submit
        </button>
        {loading && <span className="muted"> grading…</span>}
      </section>
    );
  }
}