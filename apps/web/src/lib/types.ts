// TypeScript mirrors of the Python domain models (python/lexicon/models.py).

export type DifficultyTier = "beginner" | "intermediate" | "advanced";
export type Scope = "global" | "user";
export type TutorMode = "explain" | "quiz" | "compare";
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
}

export interface ProgressUpdate {
  rating: Rating;
  note?: string;
  starred?: boolean;
}

export interface DeckSummary {
  domain: string;
  difficulty_tier: string;
  count: number;
  cefr_levels: string[];
}

export interface DueTerm {
  term_id: string;
  term: string;
  definition: string;
  example?: string;
  synonyms?: string[];
  domain?: string;
  difficulty_tier?: string;
  cefr_level?: string;
  state?: string;
  reps?: number;
  stability?: number;
}

export interface QuizQuestion {
  term_id: string;
  term: string;
  options: string[];
  answer_index: number;
}

export interface Quiz {
  domain: string;
  difficulty_tier: string;
  questions: QuizQuestion[];
}
