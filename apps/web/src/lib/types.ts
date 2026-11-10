// TypeScript mirrors of the Python domain models (python/lexicon/models.py).

export type DifficultyTier = "beginner" | "intermediate" | "advanced";
export type Scope = "global" | "user";
export type TutorMode = "explain" | "quiz" | "compare" | "answer";
export type Rating = 1 | 2 | 3 | 4; // Again | Hard | Good | Easy
export type CardState = "new" | "learning" | "review" | "relearning";

export interface SearchHit {
  term_id: string;
  term: string;
  definition: string;
  example?: string;
  synonyms?: string[];
  domain?: string;
  subfield?: string;
  difficulty_tier?: string;
  cefr_level?: string;
  scope?: Scope;
  distance?: number | null;
}

export interface SearchRequest {
  query: string;
  domains?: string[];
  difficulty_tiers?: string[];
  cefr_levels?: string[];
  list_ids?: string[];
  top_k?: number;
}

export interface TutorRequest extends SearchRequest {
  mode?: TutorMode;
}

export interface TutorResponse {
  answer: string;
  hits: SearchHit[];
}

export interface ProgressRecord {
  user_id: string;
  term_id: string;
  state: CardState;
  due: string;
  stability: number;
  difficulty: number;
  reps: number;
  lapses: number;
  last_rating?: Rating | null;
  note?: string;
  starred?: boolean;
}

export interface UserAggregate {
  user_id: string;
  current_streak: number;
  longest_streak: number;
  last_study_date: string;
  daily_goal: number;
  total_learned: number;
  due_today: number;
  total_xp: number;
  level: number | null;
}

export interface ProgressUpdate {
  rating: Rating;
  note?: string;
  starred?: boolean;
}

// --- adaptive sessions (python/lexicon/session.py) ---

export type ExerciseType =
  | "choose_definition"
  | "choose_word"
  | "cloze"
  | "type_word"
  | "nuance" // pick between the word and its near-synonyms
  | "use_word"; // write a sentence using the word

/** One exercise. The answer stays on the server. */
export interface Exercise {
  exercise_id: string;
  session_id: string;
  type: ExerciseType;
  prompt: string;
  options: string[];
  hint: string;
  kind: "new" | "review";
  stage: "first" | "confirm" | "retry";
  attempt: number;
  remaining: number;
}

export interface EntryView {
  term: string;
  pos: string;
  definition: string;
  examples: string[];
  synonyms: string[];
  confused_with: string[];
  etymology: string;
  /** Imported words: the sentence the learner met it in. */
  met_in: string;
}

export interface SessionSummary {
  new_words: number;
  already_known: number;
  learned: number;
  reviews_remembered: number;
  reviews_relearned: number;
  gave_up: number;
  level_start: number;
  level_end: number;
}

/** `next_step === "retry"`: not graded (see `nudge`); the same exercise stays open. */
export interface AnswerResult {
  correct: boolean;
  typo: boolean;
  chosen: string;
  correct_answer: string;
  entry: EntryView | null;
  next_step: "confirm" | "requeued" | "done" | "retry";
  remaining: number;
  level: number;
  nudge: string;
  /** Sentence grading, or how the near-synonyms differ. */
  feedback: string;
  improved: string;
  summary: SessionSummary | null;
}

/** Teaching card shown after a miss (python/lexicon/lesson.py). */
export interface Lesson {
  term: string;
  pos: string;
  definition: string;
  ipa: string;
  parts: string;
  hook: string;
  examples: string[];
  etymology: string;
}

// --- importing words from the learner's reading (python/lexicon/importer.py) ---

export interface ImportRequest {
  text?: string;
  url?: string;
}

export interface ImportedWord {
  term_id: string;
  term: string;
  definition: string;
  difficulty: number;
  context: string;
}

export interface ImportResult {
  source: string;
  added: ImportedWord[];
  already_studying: string[];
  too_easy: string[];
  rejected: string[];
}

// --- progress page (python/lexicon/overview.py) ---

export interface LevelPoint {
  date: string;
  level: number;
}

export interface Overview {
  level: number | null;
  level_history: LevelPoint[];
  learning: number;
  known: number;
  queued: number;
  due_forecast: { date: string; count: number }[];
  struggling: { term_id: string; term: string; definition: string; lapses: number }[];
}

export interface SessionStarted {
  session_id: string;
  reviews: number;
  new_words: number;
  level: number;
}

export interface NextResponse {
  exercise: Exercise | null;
  summary: SessionSummary | null;
}

export interface AnswerRequest {
  exercise_id: string;
  choice?: number;
  text?: string;
}
