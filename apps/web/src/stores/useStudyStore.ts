// Study workflow: pick a deck -> flashcards (rate -> FSRS) -> quiz (retention check).

import { create } from "zustand";

import { getTransport } from "../lib/transport";
import type { DeckSummary, DueTerm, Quiz, QuizQuestion, Rating } from "../lib/types";

const transport = getTransport();

type Screen = "decks" | "study" | "quiz";

interface StudyState {
  screen: Screen;
  decks: DeckSummary[];
  currentDeck: { domain: string; difficulty_tier: string } | null;
  cards: DueTerm[];
  cardIndex: number;
  revealed: boolean;
  quiz: Quiz | null;
  quizAnswers: (boolean | null)[];
  quizScore: { correct: number; total: number } | null;
  loading: boolean;
  error: string | null;

  loadDecks: () => Promise<void>;
  startDeck: (domain: string, difficultyTier: string) => Promise<void>;
  reveal: () => void;
  rate: (rating: Rating) => Promise<void>;
  loadQuiz: (domain: string, difficultyTier: string) => Promise<void>;
  answerQuiz: (index: number, correct: boolean) => void;
  submitQuiz: () => Promise<void>;
  backToDecks: () => void;
}

export const useStudyStore = create<StudyState>()((set, get) => ({
  screen: "decks",
  decks: [],
  currentDeck: null,
  cards: [],
  cardIndex: 0,
  revealed: false,
  quiz: null,
  quizAnswers: [],
  quizScore: null,
  loading: false,
  error: null,

  loadDecks: async () => {
    set({ loading: true, error: null });
    try {
      set({ decks: await transport.listDecks(), loading: false });
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  startDeck: async (domain, difficultyTier) => {
    set({ loading: true, error: null, currentDeck: { domain, difficulty_tier: difficultyTier }, cardIndex: 0, revealed: false });
    try {
      await transport.startDeck(domain, difficultyTier);
      const cards = await transport.dueTerms(30);
      set({ cards, screen: "study", loading: false });
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  reveal: () => set((state) => ({ revealed: !state.revealed })),

  rate: async (rating) => {
    const { cards, cardIndex } = get();
    const card = cards[cardIndex];
    if (!card) return;
    try {
      await transport.review(card.term_id, { rating });
      if (cardIndex + 1 >= cards.length) {
        // Deck done — return to the deck list so the learner can pick another set.
        set({ screen: "decks", cards: [], cardIndex: 0, revealed: false });
        await get().loadDecks();
      } else {
        set({ cardIndex: cardIndex + 1, revealed: false });
      }
    } catch (error) {
      set({ error: message(error) });
    }
  },

  loadQuiz: async (domain, difficultyTier) => {
    set({ loading: true, error: null, quizAnswers: [], quizScore: null });
    try {
      const quiz = await transport.quiz(domain, difficultyTier, 10);
      set({ quiz, currentDeck: { domain, difficulty_tier: difficultyTier }, screen: "quiz", loading: false });
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  answerQuiz: (index, correct) =>
    set((state) => {
      const answers = [...state.quizAnswers];
      answers[index] = correct;
      return { quizAnswers: answers };
    }),

  submitQuiz: async () => {
    const { quiz, quizAnswers } = get();
    if (!quiz) return;
    set({ loading: true, error: null });
    try {
      const answers = quiz.questions
        .map((question: QuizQuestion, index: number) => ({
          term_id: question.term_id,
          correct: quizAnswers[index] === true,
        }))
        .filter((answer) => answer.term_id);
      const score = await transport.gradeQuiz(answers);
      set({ quizScore: score, loading: false });
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  backToDecks: () => set({ screen: "decks", quiz: null, quizScore: null, cards: [], cardIndex: 0 }),
}));

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}