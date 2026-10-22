// Auth state for both transports: desktop uses the Rust loopback PKCE flow via invoke(),
// web uses the Cognito Hosted UI redirect (Phase 5).

import { create } from "zustand";

import * as webAuth from "../lib/auth";
import { isTauri } from "../lib/transport";

interface AuthState {
  ready: boolean;
  authenticated: boolean;
  error: string | null;
  init: () => Promise<void>;
  login: () => Promise<void>;
  logout: () => Promise<void>;
}

async function invoke<T>(command: string): Promise<T> {
  const { invoke: rawInvoke } = await import("@tauri-apps/api/core");
  return rawInvoke<T>(command);
}

export const useAuthStore = create<AuthState>()((set) => ({
  ready: false,
  authenticated: false,
  error: null,

  init: async () => {
    try {
      if (isTauri()) {
        // Tries a silent refresh from the keychain-stored refresh token, then falls back.
        const restored = await invoke<{ authenticated: boolean }>("auth_restore");
        set({ authenticated: restored.authenticated, ready: true });
      } else {
        const justLoggedIn = webAuth.isConfigured() ? await webAuth.completeLogin() : false;
        set({ authenticated: justLoggedIn || webAuth.isAuthenticated(), ready: true });
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error), ready: true });
    }
  },

  login: async () => {
    set({ error: null });
    try {
      if (isTauri()) {
        await invoke("auth_login");
        set({ authenticated: true });
      } else {
        await webAuth.beginLogin(); // redirects away
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },

  logout: async () => {
    try {
      if (isTauri()) {
        await invoke("auth_logout");
        set({ authenticated: false });
      } else {
        webAuth.logout(); // redirects away
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
