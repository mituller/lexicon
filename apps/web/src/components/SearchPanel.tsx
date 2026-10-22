import { useVocabStore } from "../stores/useVocabStore";
import type { Rating } from "../lib/types";

const DOMAINS = ["general", "ai"];
const TIERS = ["beginner", "intermediate", "advanced"];
const RATINGS: { label: string; value: Rating }[] = [
  { label: "Again", value: 1 },
  { label: "Hard", value: 2 },
  { label: "Good", value: 3 },
  { label: "Easy", value: 4 },
];

export function SearchPanel() {
  const {
    query,
    domains,
    tiers,
    results,
    loading,
    error,
    setQuery,
    toggleDomain,
    toggleTier,
    search,
    review,
  } = useVocabStore();

  return (
    <section className="panel">
      <form
        className="search-form"
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <input
          className="search-input"
          value={query}
          placeholder="Search vocabulary, e.g. 'grounding a model in sources'"
          onChange={(event) => setQuery(event.target.value)}
        />
        <button className="primary" type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="filters">
        <fieldset>
          <legend>Domain</legend>
          {DOMAINS.map((domain) => (
            <label key={domain}>
              <input
                type="checkbox"
                checked={domains.includes(domain)}
                onChange={() => toggleDomain(domain)}
              />
              {domain}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>Difficulty</legend>
          {TIERS.map((tier) => (
            <label key={tier}>
              <input
                type="checkbox"
                checked={tiers.includes(tier)}
                onChange={() => toggleTier(tier)}
              />
              {tier}
            </label>
          ))}
        </fieldset>
      </div>

      {error && <p className="error">{error}</p>}

      <ul className="results">
        {results.map((hit) => (
          <li key={hit.term_id} className="result">
            <div className="result-head">
              <span className="term">{hit.term}</span>
              <span className="badges">
                {hit.cefr_level && <span className="badge">{hit.cefr_level}</span>}
                {hit.difficulty_tier && <span className="badge">{hit.difficulty_tier}</span>}
                {hit.domain && <span className="badge">{hit.domain}</span>}
              </span>
            </div>
            <p className="definition">{hit.definition}</p>
            {hit.example && <p className="example">“{hit.example}”</p>}
            <div className="review-buttons">
              {RATINGS.map((rating) => (
                <button
                  key={rating.value}
                  type="button"
                  onClick={() => void review(hit.term_id, rating.value)}
                >
                  {rating.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
