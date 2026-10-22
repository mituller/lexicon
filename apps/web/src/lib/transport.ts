// The Transport abstraction lets the same React UI run on desktop (Tauri `invoke`) and on the
// web/PWA (`fetch`), with no component knowing which it is. The web implementation is present
// but not wired to a deployed API yet — see prompt.md Phase 5 (web is stubbed).

import { getIdToken as getWebIdToken } from "./auth";
import type {
  DeckSummary,
  DueTerm,
  ProgressRecord,
  ProgressUpdate,
  Quiz,
  SearchHit,
  SearchRequest,
  TutorRequest,
  TutorResponse,
  UserAggregate,
} from "./types";

export interface Transport {
  search(request: SearchRequest): Promise<SearchHit[]>;
  tutor(request: TutorRequest): Promise<TutorResponse>;
  progress(): Promise<ProgressRecord[]>;
  review(termId: string, update: ProgressUpdate): Promise<ProgressRecord>;
  stats(): Promise<UserAggregate>;

  // Decks & retention
  listDecks(): Promise<DeckSummary[]>;
  startDeck(domain: string, difficultyTier: string): Promise<{ created: number }>;
  dueTerms(limit?: number): Promise<DueTerm[]>;
  quiz(domain: string, difficultyTier: string, count?: number): Promise<Quiz>;
  gradeQuiz(answers: { term_id: string; correct: boolean }[]): Promise<{ correct: number; total: number }>;

  // Offline support (desktop only). Web omits these, so callers must feature-detect.
  queueReview?(termId: string, update: ProgressUpdate): Promise<number>;
  drainOutbox?(): Promise<number>;
  pendingCount?(): Promise<number>;
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function apiBase(): string {
  return import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
}

/** Desktop transport: every call goes through the thin Rust shell, which attaches the JWT. */
export const tauriTransport: Transport = {
  async search(request) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<SearchHit[]>("search", { request });
  },
  async tutor(request) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<TutorResponse>("tutor", { request });
  },
  async progress() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ProgressRecord[]>("list_progress", {});
  },
  async review(termId, update) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ProgressRecord>("review_term", { termId, update });
  },
  async stats() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<UserAggregate>("stats", {});
  },

  async listDecks() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<DeckSummary[]>("list_decks", {});
  },
  async startDeck(domain, difficultyTier) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<{ created: number }>("start_deck", {
      request: { domain, difficulty_tier: difficultyTier },
    });
  },
  async dueTerms(limit = 20) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<DueTerm[]>("review_due", { limit });
  },
  async quiz(domain, difficultyTier, count = 10) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<Quiz>("quiz", { request: { domain, difficulty_tier: difficultyTier, count } });
  },
  async gradeQuiz(answers) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<{ correct: number; total: number }>("grade_quiz", { request: { answers } });
  },

  async queueReview(termId, update) {
    const { invoke } = await import("@tauri-apps/api/core");
    const result = await invoke<{ pending: number }>("local_review", { termId, update });
    return result.pending;
  },
  async drainOutbox() {
    const { invoke } = await import("@tauri-apps/api/core");
    const result = await invoke<{ pending: number }>("sync_outbox", {});
    return result.pending;
  },
  async pendingCount() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<number>("pending_review_count", {});
  },
};

/** Web/PWA transport (stub): talks to the same FastAPI endpoints over HTTPS + JWT. */
export const webTransport: Transport = {
  async search(request) {
    return call<SearchHit[]>("/v1/search", "POST", request);
  },
  async tutor(request) {
    return call<TutorResponse>("/v1/tutor", "POST", request);
  },
  async progress() {
    return call<ProgressRecord[]>("/v1/progress", "GET");
  },
  async review(termId, update) {
    return call<ProgressRecord>(`/v1/progress/${encodeURIComponent(termId)}`, "PUT", update);
  },
  async stats() {
    return call<UserAggregate>("/v1/stats", "GET");
  },

  async listDecks() {
    return call<DeckSummary[]>("/v1/decks", "GET");
  },
  async startDeck(domain, difficultyTier) {
    return call<{ created: number }>("/v1/deck/start", "POST", { domain, difficulty_tier: difficultyTier });
  },
  async dueTerms(limit = 20) {
    return call<DueTerm[]>(`/v1/review/due?limit=${limit}`, "GET");
  },
  async quiz(domain, difficultyTier, count = 10) {
    return call<Quiz>(
      `/v1/quiz?domain=${domain}&difficulty_tier=${difficultyTier}&count=${count}`,
      "GET",
    );
  },
  async gradeQuiz(answers) {
    return call<{ correct: number; total: number }>("/v1/quiz/grade", "POST", { answers });
  },
};

async function call<T>(path: string, method: string, body?: unknown): Promise<T> {
  const token = await getIdToken();
  const response = await fetch(`${apiBase()}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Web-only Cognito token retrieval (Phase 5 wires the Hosted UI end to end). */
async function getIdToken(): Promise<string | null> {
  return getWebIdToken();
}

export function getTransport(): Transport {
  return isTauri() ? tauriTransport : webTransport;
}
