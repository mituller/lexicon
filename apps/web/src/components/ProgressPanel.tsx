// Progress page: level over time, reviews coming up, and the words that keep slipping.
// Charts are single-series SVG in the accent colour, with hover tooltips and a table view.

import { useEffect, useState } from "react";

import { getTransport } from "../lib/transport";
import type { LevelPoint, Overview } from "../lib/types";

export function ProgressPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTransport()
      .overview()
      .then(setOverview, (err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!overview) return <p className="muted">Loading…</p>;

  return (
    <section className="panel progress-page">
      <div className="stats">
        <Tile value={overview.level != null ? Math.round(overview.level).toString() : "–"} label="level" />
        <Tile value={overview.known.toString()} label="words known well" />
        <Tile value={overview.learning.toString()} label="still learning" />
        <Tile value={overview.queued.toString()} label="imported, waiting" />
      </div>

      <h3>Level over time</h3>
      {overview.level_history.length >= 2 ? (
        <LevelChart points={overview.level_history} />
      ) : (
        <p className="muted">Your level history appears here after sessions on two different days.</p>
      )}

      <h3>Reviews due, next 7 days</h3>
      <DueChart days={overview.due_forecast} />

      <h3>Words that keep slipping</h3>
      {overview.struggling.length === 0 ? (
        <p className="muted">None right now. Words you miss more than once show up here.</p>
      ) : (
        <ul className="word-list">
          {overview.struggling.map((word) => (
            <li key={word.term_id}>
              <strong>{word.term}</strong> <span className="muted">— {word.definition}</span>{" "}
              <span className="badge">missed {word.lapses}×</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

const W = 640;
const H = 200;
const PAD = { top: 16, right: 48, bottom: 28, left: 36 };

function niceTicks(lo: number, hi: number): number[] {
  const step = hi - lo > 20 ? 10 : 5;
  const ticks: number[] = [];
  for (let t = Math.floor(lo / step) * step; t <= hi; t += step) ticks.push(t);
  return ticks;
}

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function LevelChart({ points }: { points: LevelPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const levels = points.map((p) => p.level);
  const lo = Math.max(0, Math.floor((Math.min(...levels) - 3) / 5) * 5);
  const hi = Math.min(100, Math.ceil((Math.max(...levels) + 3) / 5) * 5);
  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.level).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const active = hover != null ? points[hover] : null;

  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Level from ${points[0].level.toFixed(0)} to ${last.level.toFixed(0)}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const px = ((event.clientX - box.left) / box.width) * W;
          const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (points.length - 1));
          setHover(Math.max(0, Math.min(points.length - 1, i)));
        }}
      >
        {niceTicks(lo, hi).map((t) => (
          <g key={t}>
            <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="axis" x={PAD.left - 6} y={y(t)} textAnchor="end" dominantBaseline="middle">
              {t}
            </text>
          </g>
        ))}
        <text className="axis" x={PAD.left} y={H - 8}>
          {shortDate(points[0].date)}
        </text>
        <text className="axis" x={W - PAD.right} y={H - 8} textAnchor="end">
          {shortDate(last.date)}
        </text>
        <path className="area" d={`${path} L${x(points.length - 1)},${y(lo)} L${x(0)},${y(lo)} Z`} />
        <path className="line" d={path} />
        <circle className="dot" cx={x(points.length - 1)} cy={y(last.level)} r={4} />
        <text className="value" x={x(points.length - 1) + 8} y={y(last.level)} dominantBaseline="middle">
          {last.level.toFixed(0)}
        </text>
        {active && hover != null && (
          <g pointerEvents="none">
            <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} />
            <circle className="dot" cx={x(hover)} cy={y(active.level)} r={4} />
          </g>
        )}
      </svg>
      {active && (
        <div className="tooltip">
          {shortDate(active.date)}: level <strong>{active.level.toFixed(1)}</strong>
        </div>
      )}
      <TableView
        head={["Date", "Level"]}
        rows={points.map((p) => [p.date, p.level.toFixed(1)])}
      />
    </figure>
  );
}

function DueChart({ days }: { days: Overview["due_forecast"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...days.map((d) => d.count));
  const slot = (W - PAD.left - PAD.right) / days.length;
  const barWidth = Math.min(24, slot * 0.6);
  const y = (v: number) => PAD.top + (1 - v / max) * (H - PAD.top - PAD.bottom);
  const base = y(0);
  const active = hover != null ? days[hover] : null;

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Reviews due per day for the next week" onMouseLeave={() => setHover(null)}>
        <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={base} y2={base} />
        {days.map((day, i) => {
          const cx = PAD.left + slot * (i + 0.5);
          const top = y(day.count);
          const h = base - top;
          const r = Math.min(4, h / 2);
          return (
            <g key={day.date} onMouseEnter={() => setHover(i)}>
              <rect className="hit" x={cx - slot / 2} y={PAD.top} width={slot} height={base - PAD.top} />
              {day.count > 0 && (
                <path
                  className="bar"
                  d={`M${cx - barWidth / 2},${base} V${top + r} Q${cx - barWidth / 2},${top} ${cx - barWidth / 2 + r},${top} H${cx + barWidth / 2 - r} Q${cx + barWidth / 2},${top} ${cx + barWidth / 2},${top + r} V${base} Z`}
                />
              )}
              {day.count > 0 && (
                <text className="value" x={cx} y={top - 6} textAnchor="middle">
                  {day.count}
                </text>
              )}
              <text className="axis" x={cx} y={H - 8} textAnchor="middle">
                {i === 0 ? "Today" : new Date(`${day.date}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" })}
              </text>
            </g>
          );
        })}
      </svg>
      {active && (
        <div className="tooltip">
          {hover === 0 ? "Today (incl. overdue)" : shortDate(active.date)}: <strong>{active.count}</strong> review
          {active.count === 1 ? "" : "s"}
        </div>
      )}
      <TableView head={["Date", "Reviews due"]} rows={days.map((d) => [d.date, d.count.toString()])} />
    </figure>
  );
}

function TableView({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <details className="table-view">
      <summary>Show as table</summary>
      <table>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, i) => (
                <td key={i}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
