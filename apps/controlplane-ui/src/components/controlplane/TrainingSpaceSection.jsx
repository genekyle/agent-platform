import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const VERBS = ["click", "type", "select"];

// On-theme palette (matches App.css light theme).
const C = {
  blue: "#2f6feb", blueSoft: "#eef4fc", ink: "#24344d", indigo: "#1e3a8a",
  muted: "#6b7280", faint: "#9ca3af", line: "#e5edf6", surface: "#f1f5f9",
  amber: "#b45309", amberBg: "#fef6e7", amberLine: "#f5d9a8",
  teal: "#0d9488", green: "#15803d", red: "#dc2626",
};
// candidate mark → visual
const MARK = {
  golden: { color: C.blue, glyph: "★", fill: "rgba(47,111,235,0.14)", label: "golden" },
  acceptable: { color: C.teal, glyph: "✓", fill: "rgba(13,148,136,0.12)", label: "acceptable" },
  rejected: { color: C.red, glyph: "✕", fill: "rgba(220,38,38,0.08)", label: "rejected" },
};

/**
 * Training Space — keyboard-driven AX confirm/correct with three candidate tiers.
 *
 *  golden (approve)  one preferred target → positive_candidate_id
 *  acceptable        also-correct-but-not-preferred (a state can have several)
 *  rejected          clearly wrong (strengthens the negative signal)
 *
 * Plus mission context + a from→now→to trajectory, so every label is a full
 * state-graph EDGE, not an isolated node.
 *
 *  ↑/↓ move cursor · 1-9 jump · G golden · A acceptable · X reject
 *  Enter commit · N none→needs-vision · → skip · T verb
 */
export function TrainingSpaceSection() {
  const [queue, setQueue] = useState(null);
  const [idx, setIdx] = useState(0);
  const [item, setItem] = useState(null);
  const [goldenId, setGoldenId] = useState(null);
  const [cursorId, setCursorId] = useState(null);
  const [marks, setMarks] = useState({});           // {candidate_id: "acceptable"|"rejected"}
  const [verb, setVerb] = useState("click");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ confirmed: 0, corrected: 0, none: 0 });
  const [imgDims, setImgDims] = useState(null);
  const [flash, setFlash] = useState(null);
  const [pageStates, setPageStates] = useState([]);
  const [fromState, setFromState] = useState("");
  const [toState, setToState] = useState("");

  const items = queue?.items ?? [];
  const current = items[idx] ?? null;
  const shotUrl = (fn) => (fn ? `${API}/api/observations/screenshots/${encodeURIComponent(fn)}` : null);
  const markOf = (id) => (id === goldenId ? "golden" : marks[id] || null);

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
    // pre-load any existing labels; else default golden = the model's suggestion
    const existing = d.golden?.candidate_labels || {};
    let golden = d.golden?.positive_candidate_id || null;
    const m = {};
    Object.entries(existing).forEach(([id, lab]) => {
      if (lab === "approve") golden = golden || id;
      else if (lab === "acceptable") m[id] = "acceptable";
      else if (lab === "reject") m[id] = "rejected";
    });
    if (!golden) golden = d.suggestion?.candidate_id ?? d.candidates?.[0]?.candidate_id ?? null;
    setGoldenId(golden); setCursorId(golden); setMarks(m);
    setVerb(d.suggestion?.action_id && VERBS.includes(d.suggestion.action_id) ? d.suggestion.action_id : "click");
    const ctx = d.context || {};
    setFromState(ctx.observed_page_state || "");
    setToState(ctx.post_action_state || d.trajectory?.next?.observed_page_state || "");
    const qs = new URLSearchParams({ domain_id: ctx.domain_id || "", goal_id: ctx.goal_id || "", scenario_id: ctx.scenario_id || "" });
    fetch(`${API}/api/training/page-states?${qs}`).then((x) => (x.ok ? x.json() : [])).then(setPageStates).catch(() => setPageStates([]));
  }, []);

  useEffect(() => { if (current) loadItem(current.filename); }, [current, loadItem]);

  const showFlash = (text, color) => { setFlash({ text, color }); setTimeout(() => setFlash(null), 750); };
  const advance = useCallback(() => setIdx((i) => Math.min(i + 1, items.length)), [items.length]);

  // tier setters (mutually exclusive per candidate)
  const setGolden = useCallback((id) => { setGoldenId(id); setMarks((m) => { const n = { ...m }; delete n[id]; return n; }); }, []);
  const toggleMark = useCallback((id, kind) => {
    setMarks((m) => { const n = { ...m }; if (n[id] === kind) delete n[id]; else n[id] = kind; return n; });
    setGoldenId((g) => (g === id ? null : g));   // can't be golden and marked
  }, []);

  const commit = useCallback(async () => {
    if (!item || !goldenId || busy) return;
    const isCorrection = goldenId !== item.suggestion?.candidate_id;
    const candidate_labels = { [goldenId]: "approve" };
    const rejected = [];
    Object.entries(marks).forEach(([id, kind]) => {
      candidate_labels[id] = kind === "rejected" ? "reject" : "acceptable";
      if (kind === "rejected") rejected.push(id);
    });
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/observations/${encodeURIComponent(item.filename)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_annotation: { positive_candidate_id: goldenId, candidate_labels, rejected_candidate_ids: rejected, review_status: "reviewed" },
          action_type: verb, observed_page_state: fromState || "", post_action_state: toState || "",
        }),
      });
      if (!r.ok) throw new Error(`Save failed: ${r.status}`);
      setStats((s) => ({ ...s, [isCorrection ? "corrected" : "confirmed"]: s[isCorrection ? "corrected" : "confirmed"] + 1 }));
      showFlash(isCorrection ? "Corrected" : "Confirmed", isCorrection ? C.amber : C.green);
      advance();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [item, goldenId, marks, verb, busy, advance, fromState, toState]);

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
      const pos = cands.findIndex((c) => c.candidate_id === cursorId);
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); setCursorId(cands[Math.min(pos + 1, cands.length - 1)]?.candidate_id ?? cursorId); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); setCursorId(cands[Math.max(pos - 1, 0)]?.candidate_id ?? cursorId); }
      else if (e.key >= "1" && e.key <= "9") { const n = Number(e.key) - 1; if (cands[n]) setCursorId(cands[n].candidate_id); }
      else if (e.key === "g" || e.key === "G") { if (cursorId) setGolden(cursorId); }
      else if (e.key === "a" || e.key === "A") { if (cursorId) toggleMark(cursorId, "acceptable"); }
      else if (e.key === "x" || e.key === "X") { if (cursorId) toggleMark(cursorId, "rejected"); }
      else if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "n" || e.key === "N") { markNone(); }
      else if (e.key === "ArrowRight" || e.key === "s") { advance(); }
      else if (e.key === "t" || e.key === "T") { setVerb((v) => VERBS[(VERBS.indexOf(v) + 1) % VERBS.length]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, cursorId, commit, markNone, advance, setGolden, toggleMark]);

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
  const acceptCount = Object.values(marks).filter((v) => v === "acceptable").length;
  const rejectCount = Object.values(marks).filter((v) => v === "rejected").length;

  return (
    <section className="panel" style={{ padding: 22 }}>
      {/* header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 18 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, color: C.ink }}>Training Space</h2>
          <p style={{ margin: "6px 0 0", color: C.muted, fontSize: 13, maxWidth: 540 }}>
            Mark a <b style={{ color: C.blue }}>golden</b> pick, plus any <b style={{ color: C.teal }}>acceptable</b> alternates and <b style={{ color: C.red }}>rejected</b> ones. Your labels are the ground truth the cheap models train on.
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
      <div style={{ marginBottom: 16 }}>
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
        <>
        {/* mission band */}
        {item.context ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "9px 14px", background: C.blueSoft, borderRadius: 12, marginBottom: 12 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: C.indigo, textTransform: "uppercase", letterSpacing: 0.5 }}>Mission</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: C.ink }}>{prettify(item.context.goal_id) || item.goal || "—"}</span>
            <span className="chip">{item.context.domain_id}</span>
            {item.context.scenario_id ? <span className="chip muted">{item.context.scenario_id}</span> : null}
            {item.context.task_id ? <span className="chip muted">{item.context.task_id}</span> : null}
            {item.context.element_query ? (
              <span style={{ fontSize: 12, color: C.muted, flex: 1, minWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                intent: “{item.context.element_query}”
              </span>
            ) : null}
          </div>
        ) : null}

        {/* trajectory */}
        <div style={{ display: "flex", alignItems: "stretch", gap: 8, marginBottom: 14 }}>
          <TrajNode label="came from" node={item.trajectory?.prev} shotUrl={shotUrl} />
          <TrajArrow verb={verb} />
          <TrajNode label="now · labeling" node={{ url: item.url, screenshot_filename: item.screenshot_filename, observed_page_state: fromState }} current shotUrl={shotUrl} />
          <TrajArrow />
          <TrajNode label="leads to" node={item.trajectory?.next} target shotUrl={shotUrl} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.55fr) minmax(320px,1fr)", gap: 18, alignItems: "start" }}>
          {/* screenshot stage */}
          <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
            background: C.surface, border: `1px solid ${C.line}`, borderRadius: 16, padding: 14, minHeight: 420 }}>
            {shotUrl(item.screenshot_filename) ? (
              <div style={{ position: "relative", display: "inline-block", lineHeight: 0, borderRadius: 8, overflow: "hidden", boxShadow: "0 4px 16px rgba(15,23,42,0.10)" }}>
                <img src={shotUrl(item.screenshot_filename)} alt="capture" style={{ maxHeight: 560, maxWidth: "100%", width: "auto", height: "auto", display: "block" }}
                  onLoad={(e) => setImgDims({ natW: e.currentTarget.naturalWidth, natH: e.currentTarget.naturalHeight, dispW: e.currentTarget.clientWidth, dispH: e.currentTarget.clientHeight })} />
                {imgDims && (item.candidates ?? []).map((c) => {
                  const box = scaleBox(c.bbox); if (!box) return null;
                  const mk = markOf(c.candidate_id);
                  const isCursor = c.candidate_id === cursorId;
                  const isSug = !mk && c.candidate_id === sug?.candidate_id;
                  const m = mk ? MARK[mk] : null;
                  const color = m ? m.color : isSug ? C.amber : "rgba(99,102,241,0.4)";
                  return (
                    <div key={c.candidate_id} onClick={() => { setCursorId(c.candidate_id); setGolden(c.candidate_id); }} title={`${c.role}: ${c.name}`}
                      style={{ position: "absolute", ...box, border: `2px solid ${color}`,
                        background: m ? m.fill : "transparent", borderRadius: 4, cursor: "pointer",
                        zIndex: mk ? 3 : isSug ? 2 : 1,
                        boxShadow: isCursor ? `0 0 0 3px rgba(47,111,235,0.25)` : "none", transition: "background 120ms" }} />
                  );
                })}
              </div>
            ) : <div style={{ color: C.muted, fontSize: 13 }}>No screenshot for this capture.</div>}
            {flash ? (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 16,
                background: "rgba(255,255,255,0.6)", pointerEvents: "none" }}>
                <span style={{ fontSize: 22, fontWeight: 700, color: flash.color, background: "#fff", padding: "10px 20px", borderRadius: 999, boxShadow: "0 6px 20px rgba(15,23,42,0.15)" }}>
                  {flash.text} ✓
                </span>
              </div>
            ) : null}
          </div>

          {/* control column */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
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

            {/* transition */}
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <span style={{ color: C.muted }}>From</span>
              <StateSelect value={fromState} onChange={setFromState} options={pageStates} />
              <span style={{ color: C.faint }}>→</span>
              <span style={{ color: C.muted }}>To</span>
              <StateSelect value={toState} onChange={setToState} options={pageStates} />
            </div>

            {/* candidate list — tag each candidate's tier, then Save once */}
            <div style={{ fontSize: 11, color: C.muted, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>Tag each candidate, then save:</span>
              <span style={{ display: "inline-flex", gap: 8 }}>
                <span style={{ color: C.blue, fontWeight: 600 }}>{goldenId ? 1 : 0} golden</span>
                <span style={{ color: C.teal, fontWeight: 600 }}>{acceptCount} acc</span>
                <span style={{ color: C.red, fontWeight: 600 }}>{rejectCount} rej</span>
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 300, overflowY: "auto", paddingRight: 2 }}>
              {(item.candidates ?? []).map((c, i) => {
                const id = c.candidate_id;
                const mk = markOf(id);
                const m = mk ? MARK[mk] : null;
                const isCursor = id === cursorId;
                const isSug = id === sug?.candidate_id;
                return (
                  <div key={id} onClick={() => setCursorId(id)}
                    style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", borderRadius: 10, cursor: "pointer",
                      background: m ? m.fill : "#fff", border: `1px solid ${m ? m.color : C.line}`,
                      outline: isCursor ? `2px solid rgba(47,111,235,0.40)` : "none", outlineOffset: -1, transition: "background 120ms" }}>
                    <span style={{ width: 17, height: 17, flexShrink: 0, borderRadius: 5, background: isCursor ? C.blue : C.surface,
                      color: isCursor ? "#fff" : C.faint, fontSize: 10, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>
                      {i < 9 ? i + 1 : "·"}
                    </span>
                    <span className="chip muted" style={{ flexShrink: 0 }}>{c.role}</span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13,
                      color: C.ink, textDecoration: mk === "rejected" ? "line-through" : "none", opacity: mk === "rejected" ? 0.6 : 1 }}>
                      {c.name || <span style={{ color: C.faint }}>—</span>}
                      {isSug ? <span title="model suggestion" style={{ color: C.amber, fontSize: 10, marginLeft: 6 }}>★sug</span> : null}
                    </span>
                    {/* explicit per-candidate tier toggles */}
                    <div style={{ display: "inline-flex", gap: 3, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                      <TierBtn active={mk === "golden"} color={C.blue} title="Golden (the preferred pick)" onClick={() => { setCursorId(id); setGolden(id); }}>G</TierBtn>
                      <TierBtn active={mk === "acceptable"} color={C.teal} title="Acceptable alternate" onClick={() => { setCursorId(id); toggleMark(id, "acceptable"); }}>A</TierBtn>
                      <TierBtn active={mk === "rejected"} color={C.red} title="Rejected (wrong)" onClick={() => { setCursorId(id); toggleMark(id, "rejected"); }}>X</TierBtn>
                    </div>
                  </div>
                );
              })}
            </div>

            <div style={{ fontSize: 11, color: C.faint, textAlign: "center" }}>
              click <b style={{ color: C.blue }}>G</b>/<b style={{ color: C.teal }}>A</b>/<b style={{ color: C.red }}>X</b> on any row · or cursor + <Kbd>G</Kbd><Kbd>A</Kbd><Kbd>X</Kbd> · tag as many as you want
            </div>

            <button className="primary-btn" onClick={commit} disabled={busy || !goldenId} style={{ width: "100%" }}>
              {goldenId ? `Save labels (${(goldenId ? 1 : 0) + acceptCount + rejectCount} marked)` : "Mark a golden pick first"} &nbsp;<Kbd dark>↵</Kbd>
            </button>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="ghost-btn" style={{ flex: 1 }} onClick={markNone} disabled={busy} title="No candidate is correct → flag for the vision layer">
                None&nbsp;<Kbd>N</Kbd>
              </button>
              <button className="ghost-btn" style={{ flex: 1 }} onClick={advance} disabled={busy} title="Skip without labeling">
                Skip&nbsp;<Kbd>→</Kbd>
              </button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center", fontSize: 11, color: C.faint }}>
              <span><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
              <span><Kbd>1</Kbd>–<Kbd>9</Kbd> jump</span>
              <span><Kbd>↵</Kbd> commit</span>
              <span><Kbd>N</Kbd> needs-vision</span>
              <span><Kbd>→</Kbd> skip</span>
            </div>
          </div>
        </div>
        </>
      ) : null}

      {!loading && items.length === 0 && !error && !done ? (
        <div className="empty-state">Queue empty — capture some states (with AX sidecars) first.</div>
      ) : null}
    </section>
  );
}

function prettify(id) {
  return id ? String(id).replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()) : "";
}

function TrajNode({ label, node, current, target, shotUrl }) {
  const empty = !node;
  const url = node?.url || "";
  const state = node?.observed_page_state;
  const thumb = shotUrl(node?.screenshot_filename);
  const accent = current ? "#2f6feb" : target ? "#b45309" : "#94a3b8";
  return (
    <div style={{ flex: 1, minWidth: 0, border: `1px solid ${current ? accent : "#e5edf6"}`, borderRadius: 12, overflow: "hidden",
      background: "#fff", boxShadow: current ? "0 0 0 3px rgba(47,111,235,0.12)" : "none", opacity: empty ? 0.55 : 1 }}>
      <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", color: accent, padding: "5px 8px 3px" }}>{label}</div>
      <div style={{ height: 56, background: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
        {empty ? <span style={{ fontSize: 11, color: "#9ca3af" }}>{target ? "— end —" : "— start —"}</span>
          : thumb ? <img src={thumb} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top" }} />
          : <span style={{ fontSize: 11, color: "#9ca3af" }}>no shot</span>}
      </div>
      <div style={{ padding: "5px 8px" }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: state ? "#24344d" : "#9ca3af", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {state ? prettify(state) : "unlabeled state"}
        </div>
        <div style={{ fontSize: 10, color: "#9ca3af", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{url.replace("https://", "") || "—"}</div>
      </div>
    </div>
  );
}

function TrajArrow({ verb }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#9ca3af", minWidth: 34 }}>
      {verb ? <span style={{ fontSize: 9, fontWeight: 600, color: "#6b7280" }}>{verb}</span> : null}
      <span style={{ fontSize: 18, lineHeight: 1 }}>→</span>
    </div>
  );
}

function StateSelect({ value, onChange, options }) {
  return (
    <select value={value || ""} onChange={(e) => onChange(e.target.value)} className="form-select"
      style={{ fontSize: 12, padding: "4px 6px", maxWidth: 150 }}>
      <option value="">— unset —</option>
      {options.map((s) => <option key={s.state_id} value={s.state_id}>{s.display_name}</option>)}
    </select>
  );
}

function candName(item, id) {
  const c = (item.candidates ?? []).find((x) => x.candidate_id === id);
  return c ? `${c.role}: ${c.name || "—"}` : (id || "—");
}

// A small per-candidate tier toggle (G / A / X). Filled in its color when active.
function TierBtn({ active, color, title, onClick, children }) {
  return (
    <button title={title} onClick={onClick}
      style={{ width: 24, height: 24, borderRadius: 7, fontSize: 11, fontWeight: 700, cursor: "pointer",
        border: `1px solid ${active ? color : "#d4deeb"}`,
        background: active ? color : "#fff", color: active ? "#fff" : "#94a3b8", lineHeight: 1, padding: 0 }}>
      {children}
    </button>
  );
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
