import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

const CLASS_TONE = {
  MEASURED: "coverage-status--ok",
  HYPOTHESIS: "coverage-status--warning",
  UNVERIFIED: "coverage-status--neutral",
  RETRACTED: "coverage-status--danger",
};

function FactRow({ item, stale }) {
  return (
    <div style={{ borderRadius: 10, background: "rgba(127,127,127,0.06)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        {stale ? (
          <span style={{ fontSize: 17, fontWeight: 600, minWidth: 52, textAlign: "right" }}>
            +{item.outdriven_by_days}d
          </span>
        ) : null}
        <span className={`coverage-status ${CLASS_TONE[item.evidence_class] || ""}`}
          title={item.evidence_class} />
        <span className="chip">{item.evidence_class}</span>
        <span className="mono" style={{ fontSize: 13 }}>{item.id}</span>
        <span className="chrome-label muted" style={{ marginLeft: "auto" }}>
          observed {item.observed_at}
          {item.last_drive_on_surface ? ` · surface last driven ${item.last_drive_on_surface.slice(0, 10)}` : " · surface not driven since"}
        </span>
      </div>
      <div style={{ marginTop: 4, fontSize: 13.5 }}>{item.claim}</div>
      {item.recheck ? (
        <div className="chrome-label muted" style={{ marginTop: 4 }}>
          re-verify: {item.recheck}
        </div>
      ) : null}
    </div>
  );
}

/**
 * World-facts staleness — which claims about a site predate the last drive on it (§14, S16).
 * A claim does not expire on a timer; it becomes worth re-checking when the world has been
 * TOUCHED since it was written. The top entry is the next re-verify drive.
 */
export function WorldFactsSection() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/world_facts/staleness`);
      if (!r.ok) throw new Error(`world facts failed: ${r.status}`);
      const payload = await r.json();
      if (seq === loadSeq.current) {
        setData(payload);
        setError(null);
      }
    } catch (e) {
      if (seq === loadSeq.current) setError(e.message);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const outdriven = data?.outdriven ?? [];
  const fresh = data?.fresh_by_silence ?? [];

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>World-facts staleness</h2>
          <p>
            A code-fact stays true until we change it; a world-fact stays true until the SITE
            changes it, with no line of ours moving. These are the dated claims the recipes cite,
            ranked by how far the world has been driven past them — the top entry is the next
            re-verify drive. Retractions keep both sides.
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <span className="chrome-label muted">claims to re-verify</span>
            <span style={{ fontSize: 22, fontWeight: 600 }}>{data ? outdriven.length : "—"}</span>
          </div>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {data ? (
        <div className="chrome-label muted" style={{ marginBottom: 10 }}>
          {data.facts} facts from {String((data.migrated_modules || []).join(", "))} ·{" "}
          {data.retracted_kept} retraction(s) kept · drive evidence: {data.corpus_rows} transition
          rows at {data.root}
          {data.corpus_rows === 0 ? " — an empty corpus at a wrong root looks exactly like this" : ""}
        </div>
      ) : null}

      {data && outdriven.length === 0 ? (
        <div className="empty-state">
          Nothing has been driven past a claim — every registered fact is at least as fresh as the
          last drive on its surface.
        </div>
      ) : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {outdriven.map((item) => (
          <FactRow key={item.id} item={item} stale />
        ))}
      </div>

      {fresh.length > 0 ? (
        <>
          <h3 style={{ marginTop: 18 }}>Fresh by silence</h3>
          <p className="chrome-label muted" style={{ marginTop: 0 }}>
            Nothing recorded has driven these surfaces since the claim — which is not the same as
            confirmed. A pure sweep banks no transition rows, so these can be fresher than they
            look, never staler.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {fresh.map((item) => (
              <FactRow key={item.id} item={item} stale={false} />
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}
