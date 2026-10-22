import { useEffect, useState } from "react";

import { SearchPanel } from "./components/SearchPanel";
import { StudyPanel } from "./components/StudyPanel";
import { useAuthStore } from "./stores/useAuthStore";
import { useVocabStore } from "./stores/useVocabStore";

type Tab = "study" | "search";

export default function App() {
  const stats = useVocabStore((state) => state.stats);
  const loadStats = useVocabStore((state) => state.loadStats);
  const pendingCount = useVocabStore((state) => state.pendingCount);

  const { ready, authenticated, error: authError, init, login, logout } = useAuthStore();

  const [tab, setTab] = useState<Tab>("study");

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    if (authenticated) void loadStats();
  }, [authenticated, loadStats]);

  return (
    <main className="app">
      <header className="app-header">
        <div>
          <h1>Lexicon</h1>
          <p className="tagline">Learn a set, test yourself, keep the words.</p>
        </div>
        {ready && (
          <div className="auth">
            {authenticated ? (
              <button type="button" onClick={() => void logout()}>
                Sign out
              </button>
            ) : (
              <button className="primary" type="button" onClick={() => void login()}>
                Sign in
              </button>
            )}
          </div>
        )}
      </header>

      {authError && <p className="error">{authError}</p>}

      {authenticated && (
        <>
          <div className="stats">
            <div className="stat">
              <span className="stat-value">{stats?.current_streak ?? 0}</span>
              <span className="stat-label">day streak</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats?.total_learned ?? 0}</span>
              <span className="stat-label">terms learned</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats?.due_today ?? 0}</span>
              <span className="stat-label">due today</span>
            </div>
          </div>

          {pendingCount > 0 && (
            <p className="pending">{pendingCount} review(s) waiting to sync</p>
          )}

          <nav className="tabs">
            <button
              type="button"
              className={tab === "study" ? "active" : ""}
              onClick={() => setTab("study")}
            >
              Study
            </button>
            <button
              type="button"
              className={tab === "search" ? "active" : ""}
              onClick={() => setTab("search")}
            >
              Search
            </button>
          </nav>

          {tab === "study" ? <StudyPanel /> : <SearchPanel />}
        </>
      )}
    </main>
  );
}