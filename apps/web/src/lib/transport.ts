// The Transport abstraction lets the same React UI run on desktop (Tauri `invoke`) and on the
// web/PWA (`fetch`), with no component knowing which it is. The web implementation is present
// but not wired to a deployed API yet — see prompt.md Phase 5 (web is stubbed).

import { getIdToken as getWebIdToken } from "./auth";
import type {
  AnswerRequest,
  AnswerResult,
  ImportRequest,
  ImportResult,
  Lesson,
  NextResponse,
  Overview,
  ProgressRecord,
  ProgressUpdate,
  SearchHit,
  SearchRequest,
  TutorRequest,
  SessionStarted,
  TutorResponse,
  UserAggregate,
} from "./types";

export interface Transport {
  search(request: SearchRequest): Promise<SearchHit[]>;
  tutor(request: TutorRequest): Promise<TutorResponse>;
  progress(): Promise<ProgressRecord[]>;
  review(termId: string, update: ProgressUpdate): Promise<ProgressRecord>;
  stats(): Promise<UserAggregate>;

  // Adaptive sessions (graded server-side)
  startSession(): Promise<SessionStarted>;
  nextExercise(sessionId: string): Promise<NextResponse>;
  answer(sessionId: string, request: AnswerRequest): Promise<AnswerResult>;
  explain(sessionId: string, exerciseId: string): Promise<{ explanation: string }>;
  lesson(sessionId: string, exerciseId: string): Promise<Lesson>;
  /** Base64 MP3 from Amazon Polly. */
  pronounce(word: string): Promise<{ audio: string; format: string }>;
  importWords(request: ImportRequest): Promise<ImportResult>;
  overview(): Promise<Overview>;
  /** Grows the learner's next difficulty band; callers don't await it. */
  prefetch(): Promise<{ added: number }>;

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

  async startSession() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<SessionStarted>("start_session", {});
  },
  async nextExercise(sessionId) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<NextResponse>("next_exercise", { sessionId });
  },
  async answer(sessionId, request) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<AnswerResult>("answer_exercise", { sessionId, request });
  },
  async explain(sessionId, exerciseId) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<{ explanation: string }>("explain_answer", {
      sessionId,
      request: { exercise_id: exerciseId },
    });
  },
  async lesson(sessionId, exerciseId) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<Lesson>("lesson", { sessionId, request: { exercise_id: exerciseId } });
  },
  async pronounce(word) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<{ audio: string; format: string }>("pronounce", { word });
  },
  async importWords(request) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ImportResult>("import_words", { request });
  },
  async overview() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<Overview>("overview", {});
  },
  async prefetch() {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<{ added: number }>("prefetch_pool", {});
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

  async startSession() {
    return call<SessionStarted>("/v1/session", "POST");
  },
  async nextExercise(sessionId) {
    return call<NextResponse>(`/v1/session/${encodeURIComponent(sessionId)}/next`, "GET");
  },
  async answer(sessionId, request) {
    return call<AnswerResult>(`/v1/session/${encodeURIComponent(sessionId)}/answer`, "POST", request);
  },
  async explain(sessionId, exerciseId) {
    return call<{ explanation: string }>(
      `/v1/session/${encodeURIComponent(sessionId)}/explain`,
      "POST",
      { exercise_id: exerciseId },
    );
  },
  async lesson(sessionId, exerciseId) {
    return call<Lesson>(`/v1/session/${encodeURIComponent(sessionId)}/lesson`, "POST", {
      exercise_id: exerciseId,
    });
  },
  async pronounce(word) {
    return call<{ audio: string; format: string }>(
      `/v1/pronounce?word=${encodeURIComponent(word)}`,
      "GET",
    );
  },
  async importWords(request) {
    return call<ImportResult>("/v1/import", "POST", request);
  },
  async overview() {
    return call<Overview>("/v1/overview", "GET");
  },
  async prefetch() {
    return call<{ added: number }>("/v1/pool/prefetch", "POST");
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
