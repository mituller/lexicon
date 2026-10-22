// Zustand store: derived UI state over the Transport. Mutations call the transport, then update
// in-memory state immediately (optimistic). See prompt.md persistence notes.

import { create } from "zustand";

import { getTransport } from "../lib/transport";
import type { Rating, SearchHit, UserAggregate } from "../lib/types";

const transport = getTransport();

interface VocabState {
  query: string;
  domains: string[];
  tiers: string[];
  results: SearchHit[];
  stats: UserAggregate | null;
  pendingCount: number;
  loading: boolean;
  error: string | null;

  setQuery: (query: string) => void;
  toggleDomain: (domain: string) => void;
  toggleTier: (tier: string) => void;
  search: () => Promise<void>;
  loadStats: () => Promise<void>;
  review: (termId: string, rating: Rating) => Promise<void>;
}

export const useVocabStore = create<VocabState>()((set, get) => ({
  query: "",
  domains: [],
  tiers: [],
  results: [],
  stats: null,
  pendingCount: 0,
  loading: false,
  error: null,

  setQuery: (query) => set({ query }),

  toggleDomain: (domain) =>
    set((state) => ({
      domains: state.domains.includes(domain)
        ? state.domains.filter((d) => d !== domain)
        : [...state.domains, domain],
    })),

  toggleTier: (tier) =>
    set((state) => ({
      tiers: state.tiers.includes(tier)
        ? state.tiers.filter((t) => t !== tier)
        : [...state.tiers, tier],
    })),

  search: async () => {
    const { query, domains, tiers } = get();
    if (!query.trim()) return;
    set({ loading: true, error: null });
    try {
      const results = await transport.search({
        query,
        domains: domains.length ? domains : undefined,
        difficulty_tiers: tiers.length ? tiers : undefined,
        top_k: 8,
      });
      set({ results, loading: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error), loading: false });
    }
  },

  loadStats: async () => {
    try {
      set({ stats: await transport.stats() });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },

  review: async (termId, rating) => {
    try {
      // Desktop queues locally first so reviews work offline, then tries to sync.
      if (transport.queueReview) {
        const pending = await transport.queueReview(termId, { rating });
        set({ pendingCount: pending });
        try {
          if (transport.drainOutbox) {
            const remaining = await transport.drainOutbox();
            set({ pendingCount: remaining });
          }
          await get().loadStats();
        } catch {
          // Offline: the review stays queued and syncs later.
        }
      } else {
        await transport.review(termId, { rating });
        await get().loadStats();
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
