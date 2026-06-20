import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const VERBS = ["click", "type", "select"];

// On-theme palette (matches App.css light theme).
const C = {
  blue: "#2f6feb", blueSoft: "#eef4fc", ink: "#24344d", indigo: "#1e3a8a",
  muted: "#6b7280", faint: "#9ca3af", line: "#e5edf6", surface: "#f1f5f9",
  amber: "#b45309", amberBg: "#fef6e7", amberLine: "#f5d9a8",
  green: "#15803d", red: "#dc2626",
};

/**
 * Training Space — keyboard-driven AX confirm/correct.
 *
 * Walks an active-learning queue (the model's weakest states first) and, for each,
 * shows the screenshot with candidate bboxes overlaid + the model's SUGGESTED pick
 * pre-lit. The operator CONFIRMS (Enter) or CORRECTS (pick another), writing a golden
 * `positive_candidate_id`. This is the ground-truth faucet for the L3/L4 models.
 */
export function TrainingSpaceSection() {
  const [queue, setQueue] = useState(null);
  const [idx, setIdx] = useState(0);
  const [item, setItem] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [verb, setVerb] = useState("click");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ confirmed: 0, corrected: 0, none: 0 });
  const [imgDims, setImgDims] = useState(null);
  const [flash, setFlash] = useState(null);

  const items = queue?.items ?? [];
  const current = items[idx] ?? null;

  const loadQueue = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(`${API}/api/training/label_queue?limit=80`);
      if (!r.ok) throw new Error(`Queue failed: ${r.status}`);
      const d = await r.json();
      setQueue(d); setIdx(0);
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const loadItem = useCallback(async (filename) => {
    setImgDims(null);
    const r = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}/candidate_suggestion`);
    if (!r.ok) { setError(`Load failed: ${r.status}`); return; }
    const d = await r.json();
    setItem(d);
    setSelectedId(d.suggestion?.candidate_id ?? d.candidates?.[0]?.candidate_id ?? null);
    setVerb(d.suggestion?.action_id && VERBS.includes(d.suggestion.action_id) ? d.suggestion.action_id : "click");
  }, []);

  useEffect(() => { if (current) loadItem(current.filename); }, [current, loadItem]);

  const showFlash = (text, color) => { setFlash({ text, color }); setTimeout(() => setFlash(null), 750); };
  const advance = useCallback(() => setIdx((i) => Math.min(i + 1, items.length)), [items.length]);

  const commit = useCallback(async () => {
    if (!item || !selectedId || busy) return;
    const isCorrection = selectedId !== item.suggestion?.candidate_id;
    setBusy(true);
    try {
      const rejected = item.suggestion?.candidate_id && isCorrection ? [item.suggestion.candidate_id] : [];
      const r = await fetch(`${API}/api/observations/${encodeURIComponent(item.filename)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_annotation: { positive_candidate_id: selectedId, rejected_candidate_ids: rejected, review_status: "reviewed" },
          action_type: verb,
        }),
      });
      if (!r.ok) throw new Error(`Save failed: ${r.status}`);
      setStats((s) => ({ ...s, [isCorrection ? "corrected" : "confirmed"]: s[isCorrection ? "corrected" : "confirmed"] + 1 }));
      showFlash(isCorrection ? "Corrected" : "Confirmed", isCorrection ? C.amber : C.green);
      advance();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [item, selectedId, verb, busy, advance]);

  const markNone = useCallback(async () => {
    if (!item || busy) return;
    setBusy(true);
    try {
      await fetch(`${API}/api/observations/${encodeURIComponent(item.filename)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: "needs_vision", status: "reviewed" }),
      });
      setStats((s) => ({ ...s, none: s.none + 1 }));
      showFlash("Needs vision", C.red);
      advance();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [item, busy, advance]);

  useEffect(() => {
    const onKey = (e) => {
      if (!item) return;
      const cands = item.candidates ?? [];
      const pos = cands.findIndex((c) => c.candidate_id === selectedId);
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); setSelectedId(cands[Math.min(pos + 1, cands.length - 1)]?.candidate_id ?? selectedId); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); setSelectedId(cands[Math.max(pos - 1, 0)]?.candidate_id ?? selectedId); }
      else if (e.key >= "1" && e.key <= "9") { const n = Number(e.key) - 1; if (cands[n]) setSelectedId(cands[n].candidate_id); }
      else if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "n" || e.key === "N") { markNone(); }
      else if (e.key === "ArrowRight" || e.key === "s") { advance(); }
      else if (e.key === "t" || e.key === "T") { setVerb((v) => VERBS[(VERBS.indexOf(v) + 1) % VERBS.length]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, selectedId, commit, markNone, advance]);

  const screenshotUrl = item?.screenshot_filename
    ? `${API}/api/observations/screenshots/${encodeURIComponent(item.screenshot_filename)}`
    : null;
  const scaleBox = (bbox) => {
    if (!bbox || !imgDims) return null;
    const sx = imgDims.dispW / imgDims.natW, sy = imgDims.dispH / imgDims.natH;
    return { left: bbox.x * sx, top: bbox.y * sy, width: bbox.width * sx, height: bbox.height * sy };
  };

  const done = !loading && items.length > 0 && idx >= items.length;
  const labeled = stats.confirmed + stats.corrected + stats.none;
  const totalQ = queue?.unlabeled ?? 0;
  const progressPct = totalQ ? Math.min(100, Math.round((100 * labeled) / totalQ)) : 0;
  const agreePct = stats.confirmed + stats.corrected > 0
    ? Math.round((100 * stats.confirmed) / (stats.confirmed + stats.corrected)) : null;
  const sug = item?.suggestion;
  const isCorrection = item && selectedId !== sug?.candidate_id;

  return (
    <section className="panel" style={{ padding: 22 }}>
      {/* header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 18 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, color: C.ink }}>Training Space</h2>
          <p style={{ margin: "6px 0 0", color: C.muted, fontSize: 13, maxWidth: 520 }}>
            Confirm or correct the model's pick. Your golden labels are the ground truth the cheap models train on.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Stat value={stats.confirmed} label="confirmed" color={C.green} />
          <Stat value={stats.corrected} label="corrected" color={C.amber} />
          <Stat value={stats.none} label="needs vision" color={C.red} />
          {agreePct !== null ? <Stat value={`${agreePct}%`} label="agreement" color={C.blue} /> : null}
          <button className="ghost-btn small-btn" onClick={loadQueue} disabled={loading} title="Reload queue">↻</button>
        </div>
      </div>

      {/* progress */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12, color: C.muted }}>
          <span>{labeled} labeled this session{totalQ ? ` · ${Math.max(0, totalQ - labeled)} left in queue` : ""}</span>
          {current ? (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              <PriorityChip reason={current.priority_reason} conf={current.suggestion_confidence} />
              <span style={{ color: C.faint }}>{(current.url || "").replace("https://", "").slice(0, 48)}</span>
            </span>
          ) : null}
        </div>
        <div style={{ height: 6, borderRadius: 999, background: C.surface, overflow: "hidden" }}>
          <div style={{ width: `${progressPct}%`, height: "100%", background: C.blue, transition: "width 220ms" }} />
        </div>
      </div>

      {error ? <div className="empty-state error" style={{ marginBottom: 12 }}>{error}</div> : null}

      {done ? (
        <div style={{ textAlign: "center", padding: "56px 20px", background: C.surface, borderRadius: 14 }}>
          <div style={{ fontSize: 34, marginBottom: 8 }}>🎉</div>
          <div style={{ fontSize: 16, fontWeight: 600, color: C.ink }}>Queue cleared</div>
          <div style={{ color: C.muted, fontSize: 13, marginTop: 6 }}>
            {labeled} labeled — {stats.confirmed} confirmed · {stats.corrected} corrected · {stats.none} needs-vision
          </div>
          <button className="primary-btn" style={{ marginTop: 16 }} onClick={loadQueue}>Reload queue</button>
        </div>
      ) : null}

      {!done && item ? (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.55fr) minmax(300px,1fr)", gap: 18, alignItems: "start" }}>
          {/* screenshot stage */}
          <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
            background: C.surface, border: `1px solid ${C.line}`, borderRadius: 16, padding: 14, minHeight: 420 }}>
            {screenshotUrl ? (
              <div style={{ position: "relative", display: "inline-block", lineHeight: 0, borderRadius: 8, overflow: "hidden", boxShadow: "0 4px 16px rgba(15,23,42,0.10)" }}>
                <img src={screenshotUrl} alt="capture" style={{ maxHeight: 560, maxWidth: "100%", width: "auto", height: "auto", display: "block" }}
                  onLoad={(e) => setImgDims({ natW: e.currentTarget.naturalWidth, natH: e.currentTarget.naturalHeight, dispW: e.currentTarget.clientWidth, dispH: e.currentTarget.clientHeight })} />
                {imgDims && (item.candidates ?? []).map((c) => {
                  const box = scaleBox(c.bbox); if (!box) return null;
                  const isSel = c.candidate_id === selectedId;
                  const isSug = c.candidate_id === sug?.candidate_id;
                  const color = isSel ? C.blue : isSug ? C.amber : "rgba(99,102,241,0.4)";
                  return (
                    <div key={c.candidate_id} onClick={() => setSelectedId(c.candidate_id)} title={`${c.role}: ${c.name}`}
                      style={{ position: "absolute", ...box, border: `2px solid ${color}`,
                        background: isSel ? "rgba(47,111,235,0.16)" : "transparent",
                        borderRadius: 4, cursor: "pointer", zIndex: isSel ? 3 : isSug ? 2 : 1,
                        boxShadow: isSel ? `0 0 0 3px rgba(47,111,235,0.25)` : "none", transition: "background 120ms" }} />
                  );
                })}
              </div>
            ) : <div style={{ color: C.muted, fontSize: 13 }}>No screenshot for this capture.</div>}
            {flash ? (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 16,
                background: "rgba(255,255,255,0.6)", backdropFilter: "blur(1px)", pointerEvents: "none" }}>
                <span style={{ fontSize: 22, fontWeight: 700, color: flash.color, background: "#fff", padding: "10px 20px", borderRadius: 999, boxShadow: "0 6px 20px rgba(15,23,42,0.15)" }}>
                  {flash.text} ✓
                </span>
              </div>
            ) : null}
          </div>

          {/* control column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
            {/* suggestion callout */}
            {sug?.candidate_id ? (
              <div style={{ padding: "10px 12px", borderRadius: 12, background: C.amberBg, border: `1px solid ${C.amberLine}` }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: C.amber, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 3 }}>★ Model suggests</div>
                <div style={{ fontSize: 13, color: C.ink }}>
                  <strong>{candName(item, sug.candidate_id)}</strong>
                  <span style={{ color: C.muted }}> · {sug.action_id} · conf {sug.confidence}{sug.needs_human ? " · ⚠ escalated" : ""}</span>
                </div>
              </div>
            ) : (
              <div style={{ padding: "10px 12px", borderRadius: 12, background: C.surface, border: `1px solid ${C.line}`, fontSize: 13, color: C.muted }}>
                No model suggestion — cold state. Pick the correct element.
              </div>
            )}

            {/* action verb */}
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: C.muted }}>Action</span>
              <div style={{ display: "inline-flex", background: C.surface, borderRadius: 10, padding: 3, gap: 2 }}>
                {VERBS.map((v) => (
                  <button key={v} onClick={() => setVerb(v)}
                    style={{ border: "none", cursor: "pointer", borderRadius: 8, padding: "5px 12px", fontSize: 12, fontWeight: 600,
                      background: v === verb ? "#fff" : "transparent", color: v === verb ? C.blue : C.muted,
                      boxShadow: v === verb ? "0 1px 3px rgba(15,23,42,0.12)" : "none" }}>{v}</button>
                ))}
              </div>
              <Kbd>T</Kbd>
            </div>

            {/* candidate list */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 360, overflowY: "auto", paddingRight: 2 }}>
              {(item.candidates ?? []).map((c, i) => {
                const isSel = c.candidate_id === selectedId;
                const isSug = c.candidate_id === sug?.candidate_id;
                return (
                  <div key={c.candidate_id} onClick={() => setSelectedId(c.candidate_id)}
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 10, cursor: "pointer",
                      background: isSel ? "rgba(47,111,235,0.10)" : "#fff",
                      border: `1px solid ${isSel ? C.blue : C.line}`, transition: "background 120ms" }}>
                    <span style={{ width: 18, height: 18, flexShrink: 0, borderRadius: 5, background: isSel ? C.blue : C.surface,
                      color: isSel ? "#fff" : C.faint, fontSize: 10, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>
                      {i < 9 ? i + 1 : "·"}
                    </span>
                    <span className="chip muted" style={{ flexShrink: 0 }}>{c.role}</span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13, color: C.ink }}>
                      {c.name || <span style={{ color: C.faint }}>—</span>}
                    </span>
                    {isSug ? <span title="model suggestion" style={{ color: C.amber }}>★</span> : null}
                    {isSel ? <span style={{ color: C.blue, fontSize: 12 }}>●</span> : null}
                  </div>
                );
              })}
            </div>

            {/* actions */}
            <button className="primary-btn" onClick={commit} disabled={busy || !selectedId} style={{ width: "100%" }}>
              {isCorrection ? "Save correction" : "Confirm"} &nbsp;<Kbd dark>↵</Kbd>
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="ghost-btn" style={{ flex: 1 }} onClick={markNone} disabled={busy} title="No candidate is correct → flag for the vision layer">
                None&nbsp;<Kbd>N</Kbd>
              </button>
              <button className="ghost-btn" style={{ flex: 1 }} onClick={advance} disabled={busy} title="Skip without labeling">
                Skip&nbsp;<Kbd>→</Kbd>
              </button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center", fontSize: 11, color: C.faint, marginTop: 2 }}>
              <span><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
              <span><Kbd>1</Kbd>–<Kbd>9</Kbd> jump</span>
              <span><Kbd>↵</Kbd> commit</span>
              <span><Kbd>N</Kbd> needs-vision</span>
              <span><Kbd>→</Kbd> skip</span>
            </div>
          </div>
        </div>
      ) : null}

      {!loading && items.length === 0 && !error && !done ? (
        <div className="empty-state">Queue empty — capture some states (with AX sidecars) first.</div>
      ) : null}
    </section>
  );
}

function candName(item, id) {
  const c = (item.candidates ?? []).find((x) => x.candidate_id === id);
  return c ? `${c.role}: ${c.name || "—"}` : (id || "—");
}

function Stat({ value, label, color }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minWidth: 56 }}>
      <span style={{ fontSize: 20, fontWeight: 700, color, lineHeight: 1 }}>{value}</span>
      <span style={{ fontSize: 10, color: "#9ca3af", marginTop: 3, textAlign: "center" }}>{label}</span>
    </div>
  );
}

function PriorityChip({ reason, conf }) {
  const map = {
    escalated: { bg: "#fdecec", fg: "#b91c1c", text: "escalated" },
    low_confidence: { bg: "#fef6e7", fg: "#b45309", text: `low conf ${conf ?? ""}` },
    confident: { bg: "#eaf3ec", fg: "#15803d", text: `conf ${conf ?? ""}` },
    uncorpused: { bg: "#f1f5f9", fg: "#475569", text: "cold" },
  };
  const m = map[reason] ?? map.uncorpused;
  return <span style={{ background: m.bg, color: m.fg, borderRadius: 999, padding: "2px 9px", fontSize: 11, fontWeight: 600 }}>{m.text}</span>;
}

function Kbd({ children, dark }) {
  return (
    <kbd style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: 18, height: 18, padding: "0 4px",
      borderRadius: 5, fontSize: 11, fontWeight: 600, fontFamily: "inherit",
      background: dark ? "rgba(255,255,255,0.22)" : "#fff",
      color: dark ? "#fff" : "#475569",
      border: dark ? "1px solid rgba(255,255,255,0.35)" : "1px solid #d4deeb",
      boxShadow: dark ? "none" : "0 1px 0 #d4deeb",
    }}>{children}</kbd>
  );
}
