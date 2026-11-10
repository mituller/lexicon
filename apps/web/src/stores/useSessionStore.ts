// Adaptive study session: start -> exercise -> answer (graded server-side) -> feedback -> next.
// The server decides what comes next (requeues, confirmations); this store only follows it.

import { create } from "zustand";

import { getTransport } from "../lib/transport";
import type { AnswerResult, Exercise, Lesson, SessionStarted, SessionSummary } from "../lib/types";
import { useVocabStore } from "./useVocabStore";

const transport = getTransport();

type Screen = "home" | "session" | "summary";

interface SessionState {
  screen: Screen;
  session: SessionStarted | null;
  exercise: Exercise | null;
  feedback: AnswerResult | null;
  summary: SessionSummary | null;
  explanation: string | null;
  tutorAnswer: string | null;
  /** Ungraded hint ("that's a synonym"); the exercise stays open. */
  nudge: string | null;
  /** Teaching card, loaded automatically after a miss. */
  lesson: Lesson | null;
  loading: boolean;
  explaining: boolean;
  askingTutor: boolean;
  error: string | null;

  start: () => Promise<void>;
  submit: (answer: { choice?: number; text?: string }) => Promise<void>;
  next: () => Promise<void>;
  explain: () => Promise<void>;
  askTutor: (question: string) => Promise<void>;
  quit: () => void;
}

const cleared = { feedback: null, explanation: null, tutorAnswer: null, nudge: null, lesson: null };

export const useSessionStore = create<SessionState>()((set, get) => ({
  screen: "home",
  session: null,
  exercise: null,
  feedback: null,
  summary: null,
  explanation: null,
  tutorAnswer: null,
  nudge: null,
  lesson: null,
  loading: false,
  explaining: false,
  askingTutor: false,
  error: null,

  start: async () => {
    set({ loading: true, error: null, summary: null, ...cleared });
    try {
      const session = await transport.startSession();
      // Grow the vocabulary ahead of the learner without making them wait for it.
      void transport.prefetch().catch(() => undefined);
      set({ session, screen: "session" });
      await get().next();
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  next: async () => {
    const { session, feedback } = get();
    if (!session) return;
    if (feedback?.summary) {
      set({ screen: "summary", summary: feedback.summary, exercise: null, ...cleared });
      void useVocabStore.getState().loadStats();
      return;
    }
    // Clear the answered exercise so it doesn't linger (disabled) while the next one loads.
    set({ loading: true, error: null, exercise: null, ...cleared });
    try {
      const { exercise, summary } = await transport.nextExercise(session.session_id);
      if (summary) {
        set({ screen: "summary", summary, exercise: null, loading: false });
        void useVocabStore.getState().loadStats();
      } else {
        set({ exercise, loading: false });
      }
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  submit: async (answer) => {
    const { session, exercise, feedback, loading } = get();
    if (!session || !exercise || feedback || loading) return;
    set({ loading: true, error: null });
    try {
      const result = await transport.answer(session.session_id, {
        exercise_id: exercise.exercise_id,
        ...answer,
      });
      if (result.next_step === "retry") {
        set({ nudge: result.nudge, loading: false });
        return;
      }
      set({ feedback: result, nudge: null, loading: false });
      if (!result.correct) {
        // A miss is the moment to teach: fetch the card without blocking the feedback.
        void transport
          .lesson(session.session_id, exercise.exercise_id)
          .then((lesson) => {
            if (get().feedback === result) set({ lesson });
          })
          .catch(() => undefined);
      }
    } catch (error) {
      set({ error: message(error), loading: false });
    }
  },

  explain: async () => {
    const { session, exercise, explaining } = get();
    if (!session || !exercise || explaining) return;
    set({ explaining: true });
    try {
      const { explanation } = await transport.explain(session.session_id, exercise.exercise_id);
      set({ explanation, explaining: false });
    } catch (error) {
      set({ error: message(error), explaining: false });
    }
  },

  askTutor: async (question) => {
    const { feedback, askingTutor } = get();
    if (!feedback?.entry || askingTutor || !question.trim()) return;
    const term = feedback.entry.term;
    set({ askingTutor: true, tutorAnswer: null });
    try {
      const { answer } = await transport.tutor({
        query: `About the word "${term}": ${question.trim()}`,
        mode: "answer",
        top_k: 5,
      });
      set({ tutorAnswer: answer, askingTutor: false });
    } catch (error) {
      set({ error: message(error), askingTutor: false });
    }
  },

  quit: () => {
    set({ screen: "home", session: null, exercise: null, summary: null, ...cleared });
    void useVocabStore.getState().loadStats();
  },
}));

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
