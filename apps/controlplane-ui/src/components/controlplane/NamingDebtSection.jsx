import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const POLL_MS = 30000;

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "—"; }
}

function shotUrl(name) {
  return name ? `${API}/api/observations/screenshots/${encodeURIComponent(name)}` : null;
}

function Shot({ name, label, wide }) {
  const [missing, setMissing] = useState(false);
  const url = shotUrl(name);
  if (!url || missing) {
    return (
      <figure style={{ margin: 0, flex: wide ? 1 : "0 0 180px", minWidth: 120 }}>
        <figcaption className="chrome-label muted">{label}</figcaption>
        <div className="chrome-label muted" style={{ padding: "18px 8px", borderRadius: 6, background: "rgba(127,127,127,0.08)", textAlign: "center" }}>
          {missing ? "file gone from disk" : "no screenshot"}
        </div>
      </figure>
    );
  }
  return (
    <figure style={{ margin: 0, flex: wide ? 1 : "0 0 180px", minWidth: 120 }}>
      <figcaption className="chrome-label muted">{label}</figcaption>
      <a href={url} target="_blank" rel="noreferrer">
        <img src={url} alt={`${label} screenshot`} loading="lazy" onError={() => setMissing(true)}
          style={{ maxWidth: "100%", borderRadius: 6, border: "1px solid rgba(127,127,127,0.25)" }} />
      </a>
    </figure>
  );
}

/** Name one situation by labeling its exemplar row — the SAME correction seam as the Queue
 *  (`POST /api/transitions/{key}/correct`), so a name written here trains the witnesses and
 *  refits programs like any other teacher label. Contract unchanged: note required, states
 *  both-or-neither, witness readings are PLACEHOLDERS never values (a prefill would let one
 *  keypress teach a disputed belief back as ground truth). */
function NameForm({ exemplar, onNamed }) {
  const [note, setNote] = useState("");
  const [beforeState, setBeforeState] = useState("");
  const [afterState, setAfterState] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const statesHalf = (beforeState.trim() === "") !== (afterState.trim() === "");

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const r = await fetch(`${API}/api/transitions/${encodeURIComponent(exemplar.key)}/correct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          index: exemplar.index, ts: exemplar.ts, note,
          before_state: beforeState.trim(), after_state: afterState.trim(),
        }),
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({})))?.detail;
        throw new Error(typeof detail === "string" ? detail : `correction failed: ${r.status}`);
      }
      onNamed();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const hint = exemplar.half === "before" ? "this screen is the BEFORE half" : "this screen is the AFTER half";
  return (
    <form onSubmit={save} style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
      <textarea
        rows={2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={`Why this name — describe the screen in one sentence (required). ${hint}.`}
        style={{ width: "100%", boxSizing: "border-box" }}
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input style={{ minWidth: 180 }} value={beforeState}
          placeholder="true before-state"
          onChange={(e) => setBeforeState(e.target.value)} />
        <span aria-hidden>→</span>
        <input style={{ minWidth: 180 }} value={afterState}
          placeholder={exemplar.state && exemplar.state !== "(unnamed)" ? `witnesses read: ${exemplar.state}` : "true after-state"}
          onChange={(e) => setAfterState(e.target.value)} />
        <button type="submit" className="ghost-btn small-btn"
          disabled={saving || !note.trim() || statesHalf}>
          {saving ? "Saving…" : "Name it"}
        </button>
        <span className="chrome-label muted">
          labels the exemplar row ({exemplar.key} · row {exemplar.index}); both states → trains + refit.
        </span>
      </div>
      {error ? <span className="capture-error">{error}</span> : null}
    </form>
  );
}

function SituationRow({ item, onNamed }) {
  const [open, setOpen] = useState(false);
  const called = item.called || [];
  const disputed = called.length > 1;
  return (
    <div style={{ borderRadius: 10, background: "rgba(127,127,127,0.06)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 18, fontWeight: 600, minWidth: 40, textAlign: "right" }}>{item.encounters}</span>
        <span className="chrome-label muted">met</span>
        {called.slice(0, 3).map((c) => (
          <span key={c.state} className="chip" title={`the witnesses called this screen ${c.state} on ${c.n} of ${item.encounters} meetings`}>
            {c.state} ×{c.n}
          </span>
        ))}
        {disputed ? <span className="chip" title="one screen, more than one name — a naming dispute">disputed</span> : null}
        <span className="chrome-label muted" style={{ marginLeft: "auto" }}>
          ambiguous {Math.round((item.ambiguous_share || 0) * 100)}% · {item.situation}
        </span>
      </div>
      <div className="chrome-label muted" style={{ marginTop: 4 }}>
        {(item.routes || []).slice(0, 2).join(" · ")}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 8, alignItems: "flex-start" }}>
        <Shot name={item.exemplar?.screenshot} label={`exemplar · ${hostOf(item.exemplar?.url)}`} wide />
        <div style={{ flex: 2, minWidth: 240 }}>
          {item.exemplar ? (
            <>
              <div className="chrome-label muted">{item.exemplar.url}</div>
              <button className="ghost-btn small-btn" style={{ marginTop: 6 }} onClick={() => setOpen(!open)}>
                {open ? "Close" : "Name this screen"}
              </button>
              {open ? <NameForm exemplar={item.exemplar} onNamed={onNamed} /> : null}
            </>
          ) : (
            <span className="chrome-label muted">no screenshot survived for this situation — name it from the Queue when it recurs</span>
          )}
        </div>
      </div>
    </div>
  );
}

function SplitName({ item }) {
  return (
    <div style={{ borderRadius: 10, background: "rgba(127,127,127,0.06)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <strong>{item.state}</strong>
        <span className="chrome-label muted">
          {item.situations} recurrent shapes (of {item.situations_incl_one_offs} seen) over {item.encounters} meetings
        </span>
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
        {(item.examples || []).map((ex) => (
          <Shot key={ex.situation} name={ex.screenshot} label={`${ex.situation} ×${ex.encounters}`} wide />
        ))}
      </div>
    </div>
  );
}

/**
 * Naming debt — the ranked report of screens we keep meeting without really knowing their name
 * (`GET /api/transitions/naming_debt`, SESSION 15). Discovery used to be by collision: a drive
 * tripped over an unnamed screen and paid for it live. This is the burn-down instead. Naming is
 * the operator's call — the report exhibits (screenshot + counts), the form below each entry
 * writes through the same correction seam as the Queue.
 */
export function NamingDebtSection() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/transitions/naming_debt?limit=25`);
      if (!r.ok) throw new Error(`naming debt failed: ${r.status}`);
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

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const queue = data?.queue ?? [];
  const splits = data?.split_names ?? [];

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Naming debt</h2>
          <p>
            Screens we keep meeting whose name the witnesses dispute, lack, or spread across
            several shapes — ranked by how often we meet them. A missing name presents as a
            confidently wrong one, never as a blank, so this counts ambiguity, not nulls.
            Name the top entries and the collisions stop paying for themselves.
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <span className="chrome-label muted">recurrent screens queued</span>
            <span style={{ fontSize: 22, fontWeight: 600 }}>{data?.queue_total ?? "—"}</span>
          </div>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {data ? (
        <div className="chrome-label muted" style={{ marginBottom: 10 }}>
          corpus: {data.rows} rows / {data.halves} screens read from {data.root} ·{" "}
          {data.one_off_situations} one-off shapes and {data.blank_halves} blank tabs counted, not shown
          {data.rows === 0 ? " — an empty corpus at a wrong root looks exactly like this; check the data root" : ""}
        </div>
      ) : null}

      {data && queue.length === 0 && data.rows > 0 ? (
        <div className="empty-state">
          No recurrent screen is missing a name right now — new debt appears here the moment a
          drive keeps meeting a screen the witnesses cannot confidently name.
        </div>
      ) : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {queue.map((item) => (
          <SituationRow key={item.situation} item={item} onNamed={load} />
        ))}
      </div>

      {splits.length > 0 ? (
        <>
          <h3 style={{ marginTop: 18 }}>One name, several shapes</h3>
          <p className="chrome-label muted" style={{ marginTop: 0 }}>
            States whose meetings split into visually distinct recurring shapes — the trap where a
            new surface classifies as an old state instead of as unknown (the preferences-landing
            shape). Compare the screenshots; if two are genuinely different screens, the name needs
            a sibling.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {splits.map((item) => (
              <SplitName key={item.state} item={item} />
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}
