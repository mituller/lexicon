import { useEffect, useState } from "react";

import logoUrl from "./assets/logo.svg";
import { ImportPanel } from "./components/ImportPanel";
import { ProgressPanel } from "./components/ProgressPanel";
import { SearchPanel } from "./components/SearchPanel";
import { StudyPanel } from "./components/StudyPanel";
import { useAuthStore } from "./stores/useAuthStore";
import { useVocabStore } from "./stores/useVocabStore";

type Tab = "study" | "import" | "progress" | "search";

const TABS: { id: Tab; label: string }[] = [
  { id: "study", label: "Study" },
  { id: "import", label: "Add words" },
  { id: "progress", label: "Progress" },
  { id: "search", label: "Search" },
];

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
          <h1 className="brand">
            <img src={logoUrl} alt="" width={40} height={40} />
            Lexicon
          </h1>
          <p className="tagline">Words that get harder as you get better.</p>
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
              <span className="stat-value">{stats?.level != null ? Math.round(stats.level) : "–"}</span>
              <span className="stat-label">level</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats?.current_streak ?? 0}</span>
              <span className="stat-label">day streak</span>
            </div>
            <div className="stat">
              <span className="stat-value">{stats?.total_learned ?? 0}</span>
              <span className="stat-label">words studied</span>
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
            {TABS.map(({ id, label }) => (
              <button key={id} type="button" className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
                {label}
              </button>
            ))}
          </nav>

          {tab === "study" && <StudyPanel />}
          {tab === "import" && <ImportPanel />}
          {tab === "progress" && <ProgressPanel />}
          {tab === "search" && <SearchPanel />}
        </>
      )}
    </main>
  );
}