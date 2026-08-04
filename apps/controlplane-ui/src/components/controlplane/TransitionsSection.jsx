import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

// Verdict → tone + what it means, shown wherever the verdict is.
const VERDICT_META = {
  confirmed: { tone: "success", hint: "the world moved the way the step predicted" },
  mismatch: { tone: "danger", hint: "the action claimed ok; the world disagrees" },
  unobserved: { tone: "neutral", hint: "the eyes were unavailable or nothing was predicted — the claim stands unchallenged" },
  read_only: { tone: "info", hint: "a read-only step, paired for the corpus without judgement" },
};

/**
 * Transition review — the inspect-and-correct step of PLAN_step_runner.md.
 *
 * Every wrapped step wrote a row: what the system BELIEVED before (the witnesses' own
 * rationale, verbatim), what it PREDICTED (declared before the act), what it DID, what it
 * SAW change, and how the verdict was reached. This panel renders that thinking in the order
 * the system lived it, and lets the operator confirm or correct the verdict — both sides
 * kept, and every override must cite the row's own evidence.
 */
export function TransitionsSection() {
  const [landing, setLanding] = useState(null);
  const [corpusKey, setCorpusKey] = useState(null);
  const [corpus, setCorpus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadLanding = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/transitions`);
      if (!r.ok) throw new Error(`transitions list failed: ${r.status}`);
      setLanding(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCorpus = useCallback(async (key) => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/transitions/${encodeURIComponent(key)}`);
      if (!r.ok) throw new Error(`corpus ${key} failed: ${r.status}`);
      setCorpus(await r.json());
      setCorpusKey(key);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadLanding(); }, [loadLanding]);

  const correct = useCallback(async (row, verdict, note) => {
    const r = await fetch(`${API}/api/transitions/${encodeURIComponent(corpusKey)}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: row.index, ts: row.ts, verdict, note }),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({})))?.detail;
      throw new Error(detail || `correction failed: ${r.status}`);
    }
    await loadCorpus(corpusKey);
    await loadLanding();
  }, [corpusKey, loadCorpus, loadLanding]);

  const corpora = landing?.corpora ?? [];
  const health = corpus?.health ?? landing?.health;

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Transition Review</h2>
          <p>
            Every wrapped step, in the order the system lived it: believed → predicted → did →
            saw → settled. Confirm the verdicts the world got right; correct the ones it got
            wrong, citing the row&apos;s own evidence. This corpus is what the state classifier and
            the verifier will train on.
          </p>
        </div>
        <button className="ghost-btn small-btn" onClick={() => (corpusKey ? loadCorpus(corpusKey) : loadLanding())} disabled={loading}>
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {health ? <HealthStrip health={health} scope={corpusKey ?? "all corpora"} /> : null}

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
        {corpora.map((c) => (
          <button
            key={c.key}
            className="ghost-btn small-btn"
            style={corpusKey === c.key ? { borderColor: "var(--accent, #7c3aed)" } : undefined}
            onClick={() => loadCorpus(c.key)}
            title={`last row ${c.last_ts || "—"}`}
          >
            {c.key} · {c.rows} row{c.rows === 1 ? "" : "s"}
            {c.corrected ? ` · ${c.corrected} corrected` : ""}
            {c.verdicts?.mismatch ? ` · ${c.verdicts.mismatch} mismatch` : ""}
          </button>
        ))}
      </div>

      {landing && corpora.length === 0 ? (
        <div className="empty-state">
          No transition rows yet. The corpus appears the moment a live drive walks any wrapped
          step — every execution path writes here now.
        </div>
      ) : null}

      {corpus ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {corpus.rows.map((row) => (
            <TransitionRow key={`${row.index}-${row.ts}`} row={row} onCorrect={correct} />
          ))}
        </div>
      ) : corpora.length > 0 ? (
        <div className="empty-state">Pick a corpus above to review its steps.</div>
      ) : null}
    </section>
  );
}

function HealthStrip({ health, scope }) {
  const verdicts = health.verdicts ?? {};
  const unmodeledPct = health.unmodeled_share != null ? Math.round(health.unmodeled_share * 100) : null;
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="coverage-totals" style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <Stat label={`Rows (${scope})`} value={health.rows} />
        <Stat label="Confirmed" value={verdicts.confirmed ?? 0} good />
        <Stat label="Mismatch" value={verdicts.mismatch ?? 0} accent={(verdicts.mismatch ?? 0) > 0} />
        <Stat label="Unobserved" value={verdicts.unobserved ?? 0} />
        <Stat label="Demotions" value={health.demotions} accent={health.demotions > 0} />
        <Stat label="Distinct states" value={health.distinct_states} />
        <Stat label="Claim agreement" value={health.claim_agreement != null ? `${Math.round(health.claim_agreement * 100)}%` : "—"} />
        <Stat label="Unmodeled" value={unmodeledPct != null ? `${unmodeledPct}%` : "—"} accent={unmodeledPct > 40} />
        <Stat label="Visual witness" value={`${health.with_screenshot}/${health.rows}`} accent={health.rows > 0 && health.with_screenshot === 0} />
        <Stat label="Corrected" value={health.corrected} />
      </div>
      <div className="chrome-label muted" style={{ marginTop: 4 }}>
        unmodeled is the share of steps with no measured prediction yet — inspection exists to
        shrink it; visual witness counts rows where a screenshot let the eyes testify.
      </div>
    </div>
  );
}

function TransitionRow({ row, onCorrect }) {
  const [expanded, setExpanded] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [note, setNote] = useState("");
  const [verdict, setVerdict] = useState("mismatch");
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState(null);

  const n = row.narration ?? {};
  const meta = VERDICT_META[row.verdict] ?? { tone: "neutral", hint: "" };
  const corrected = row.teacher_correction;

  const submit = async (v, text) => {
    setBusy(true);
    setRowError(null);
    try {
      await onCorrect(row, v, text);
    } catch (e) {
      setRowError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ borderRadius: 10, background: "rgba(127,127,127,0.06)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}
        onClick={() => setExpanded((x) => !x)}>
        <span className={`coverage-status coverage-status--${meta.tone}`} title={meta.hint} />
        <span className="chip">{row.rung}</span>
        <span style={{ flex: 1, fontSize: 13 }} title={meta.hint}>{n.headline}</span>
        {corrected ? (
          <span className="chip" title={corrected.note}
            style={{ borderColor: "var(--warning, #d97706)" }}>
            corrected{corrected.verdict ? ` → ${corrected.verdict}` : " (note)"}
          </span>
        ) : null}
        <span className="chrome-label muted">{(row.ts || "").slice(11, 19)}</span>
      </div>

      {expanded ? (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          <Thought label="believed" text={n.believed} />
          <Thought label="predicted" text={n.expected} />
          <Thought label="did" text={n.did} />
          <Thought label="saw" text={n.saw} />
          <Thought label="changed" text={n.changed} />
          {corrected ? (
            <Thought label="correction"
              text={`${corrected.by} @ ${(corrected.ts || "").slice(0, 19)} — ${corrected.verdict ? `verdict → ${corrected.verdict} (was ${corrected.original_verdict})` : "note"}: ${corrected.note}`} />
          ) : null}

          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            {!overriding ? (
              <>
                <button className="ghost-btn small-btn" disabled={busy}
                  onClick={() => submit(null, "reviewed — the evidence supports the recorded verdict")}>
                  Agree
                </button>
                <button className="ghost-btn small-btn" disabled={busy} onClick={() => setOverriding(true)}>
                  Override…
                </button>
              </>
            ) : (
              <>
                <select className="status-select" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
                  <option value="confirmed">confirmed</option>
                  <option value="mismatch">mismatch</option>
                  <option value="unobserved">unobserved</option>
                  <option value="read_only">read_only</option>
                </select>
                <input
                  style={{ flex: 1, minWidth: 240 }}
                  placeholder="cite what the row's own evidence shows — required"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
                <button className="ghost-btn small-btn" disabled={busy || !note.trim()}
                  onClick={() => submit(verdict, note)}>
                  Save
                </button>
                <button className="ghost-btn small-btn" disabled={busy} onClick={() => setOverriding(false)}>
                  Cancel
                </button>
              </>
            )}
            {rowError ? <span className="capture-error">{rowError}</span> : null}
          </div>

          <details style={{ marginTop: 4 }}>
            <summary className="chrome-label muted" style={{ cursor: "pointer" }}>raw row</summary>
            <pre style={{ fontSize: 11, overflowX: "auto" }}>{JSON.stringify(row, null, 2)}</pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}

function Thought({ label, text }) {
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <span className="chrome-label muted" style={{ minWidth: 74, textAlign: "right" }}>{label}</span>
      <span style={{ flex: 1 }}>{text || "—"}</span>
    </div>
  );
}

function Stat({ label, value, accent, good }) {
  const color = good ? "var(--success)" : accent ? "var(--warning)" : "inherit";
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span className="chrome-label muted">{label}</span>
      <span style={{ fontSize: 22, fontWeight: 600, color }}>{value}</span>
    </div>
  );
}
