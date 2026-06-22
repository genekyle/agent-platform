import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageStatePicker } from "./PageStatePicker";

const API = import.meta.env.VITE_API_BASE_URL;
const VERBS = ["click", "type", "select"];

const C = {
  blue: "#2f6feb", blueSoft: "#eef4fc", ink: "#24344d", indigo: "#1e3a8a",
  muted: "#6b7280", faint: "#9ca3af", line: "#e5edf6", surface: "#f1f5f9",
  amber: "#b45309", amberBg: "#fef6e7", amberLine: "#f5d9a8",
  teal: "#0d9488", green: "#15803d", red: "#dc2626",
};
const MARK = {
  golden: { color: C.blue, glyph: "★", fill: "rgba(47,111,235,0.16)", label: "golden" },
  acceptable: { color: C.teal, glyph: "✓", fill: "rgba(13,148,136,0.14)", label: "acceptable" },
  rejected: { color: C.red, glyph: "✕", fill: "rgba(220,38,38,0.10)", label: "rejected" },
};

const boxArea = (b) => (b ? (b.width || 0) * (b.height || 0) : 0);
const contains = (o, i) => !!o && !!i &&
  o.x - 2 <= i.x && o.y - 2 <= i.y &&
  o.x + o.width + 2 >= i.x + i.width && o.y + o.height + 2 >= i.y + i.height &&
  boxArea(o) > boxArea(i);

/**
 * Training Space — keyboard + mouse AX confirm/correct with three candidate tiers.
 * Image-dominant: tag golden/acceptable/rejected on the big screenshot or the list,
 * then Save once. Per-candidate learning, so labeling a container does NOT label its
 * children — they are separate candidates.
 */
export function TrainingSpaceSection() {
  const [queue, setQueue] = useState(null);
  const [idx, setIdx] = useState(0);
  const [item, setItem] = useState(null);
  const [goldenId, setGoldenId] = useState(null);
  const [cursorId, setCursorId] = useState(null);
  const [hoveredId, setHoveredId] = useState(null);
  const [marks, setMarks] = useState({});
  const [verb, setVerb] = useState("click");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ confirmed: 0, corrected: 0, none: 0, done: 0 });
  const [imgDims, setImgDims] = useState(null);
  const [flash, setFlash] = useState(null);
  const [pageStates, setPageStates] = useState([]);
  const [fromState, setFromState] = useState("");
  const [toState, setToState] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [domains, setDomains] = useState([]);
  const [goals, setGoals] = useState([]);
  const [activeStateField, setActiveStateField] = useState("observed"); // observed | post
  const imgRef = useRef(null);

  const items = queue?.items ?? [];
  const current = items[idx] ?? null;
  const shotUrl = (fn) => (fn ? `${API}/api/observations/screenshots/${encodeURIComponent(fn)}` : null);
  const markOf = (id) => (id === goldenId ? "golden" : marks[id] || null);

  // off-screen / zero-size candidates are hidden by default (Facebook dumps tons);
  // toggle reveals them. Client-side filter on the `visible` flag → no refetch, no mark loss.
  const cands = useMemo(
    () => (item?.candidates ?? []).filter((c) => showAll || c.visible),
    [item, showAll],
  );

  // nesting depth (containment) + z-rank (smaller area on top → click into nested)
  const geom = useMemo(() => {
    const depth = {}, z = {};
    cands.forEach((a) => { depth[a.candidate_id] = cands.reduce((n, b) => n + (a.candidate_id !== b.candidate_id && contains(b.bbox, a.bbox) ? 1 : 0), 0); });
    [...cands].sort((x, y) => boxArea(y.bbox) - boxArea(x.bbox)).forEach((a, i) => { z[a.candidate_id] = i + 1; });
    return { depth, z };
  }, [cands]);

  // list in reading order (top → left), indented by nesting depth
  const ordered = useMemo(
    () => [...cands].sort((a, b) => (a.bbox?.y ?? 0) - (b.bbox?.y ?? 0) || (a.bbox?.x ?? 0) - (b.bbox?.x ?? 0)),
    [cands],
  );

  const loadQueue = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(`${API}/api/training/label_queue?limit=80`);
      if (!r.ok) throw new Error(`Queue failed: ${r.status}`);
      setQueue(await r.json()); setIdx(0);
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { loadQueue(); }, [loadQueue]);

  // registry for the picker's folder labels (domains + goals)
  useEffect(() => {
    fetch(`${API}/api/training/domains`).then((r) => (r.ok ? r.json() : [])).then(setDomains).catch(() => {});
    fetch(`${API}/api/training/goals`).then((r) => (r.ok ? r.json() : [])).then(setGoals).catch(() => {});
  }, []);

  const loadItem = useCallback(async (filename) => {
    setImgDims(null); setHoveredId(null);
    const r = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}/candidate_suggestion`);
    if (!r.ok) { setError(`Load failed: ${r.status}`); return; }
    const d = await r.json();
    setItem(d);
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
    // EXPECTED next = the intended happy-path outcome. Deliberately NOT inferred from the
    // actual next capture — the actual (which may be a captcha/interruption) is shown in
    // the trajectory strip; expected is what SHOULD happen if the right action succeeds.
    setToState(ctx.post_action_state || "");
    setActiveStateField("observed");
    const qs = new URLSearchParams({ domain_id: ctx.domain_id || "", goal_id: ctx.goal_id || "", scenario_id: ctx.scenario_id || "" });
    fetch(`${API}/api/training/page-states?${qs}`).then((x) => (x.ok ? x.json() : [])).then(setPageStates).catch(() => setPageStates([]));
  }, []);
  useEffect(() => { if (current) loadItem(current.filename); }, [current, loadItem]);

  // keep bbox scaling correct on ANY window/layout resize (fixes drift-until-resize-back)
  const recomputeDims = useCallback(() => {
    const el = imgRef.current;
    if (!el || !el.naturalWidth) return;
    setImgDims({ natW: el.naturalWidth, natH: el.naturalHeight, dispW: el.clientWidth, dispH: el.clientHeight });
  }, []);
  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const ro = new ResizeObserver(recomputeDims);
    ro.observe(el);
    window.addEventListener("resize", recomputeDims);
    return () => { ro.disconnect(); window.removeEventListener("resize", recomputeDims); };
  }, [recomputeDims, item]);

  // Create a page-state (goal-scoped to this capture's goal); returns the created state
  // so the shared picker can auto-select it. Appended to options so it shows immediately.
  const createPageState = useCallback(async (name, { category, description, stage } = {}) => {
    const r = await fetch(`${API}/api/training/page-states`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: name, description: description || null,
        // pin to THIS capture's domain (no cross-domain leak) + chosen lifecycle stage
        scope: "goal", goal_id: item?.context?.goal_id || "", domain_id: item?.context?.domain_id || null,
        category: category || "general", stage: stage || "neutral",
      }),
    });
    if (!r.ok) throw new Error(`Create state failed: ${r.status}`);
    const created = await r.json();
    setPageStates((prev) => [...prev.filter((s) => s.state_id !== created.state_id), created]);
    return created;
  }, [item]);

  const showFlash = (text, color) => { setFlash({ text, color }); setTimeout(() => setFlash(null), 750); };
  const advance = useCallback(() => setIdx((i) => Math.min(i + 1, items.length)), [items.length]);
  const setGolden = useCallback((id) => { setGoldenId(id); setMarks((m) => { const n = { ...m }; delete n[id]; return n; }); }, []);
  const toggleMark = useCallback((id, kind) => {
    setMarks((m) => { const n = { ...m }; if (n[id] === kind) delete n[id]; else n[id] = kind; return n; });
    setGoldenId((g) => (g === id ? null : g));
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

  // Terminal: the task/scenario is already complete here (e.g. logged-in home) — no
  // action to pick. Records it as a STATE example (observed_page_state), not a selection.
  const markDone = useCallback(async () => {
    if (!item || busy) return;
    setBusy(true);
    try {
      await fetch(`${API}/api/observations/${encodeURIComponent(item.filename)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ observed_page_state: fromState || "task_complete", label: "terminal", status: "reviewed" }),
      });
      setStats((s) => ({ ...s, done: s.done + 1 }));
      showFlash("Terminal", C.indigo);
      advance();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [item, busy, advance, fromState]);

  useEffect(() => {
    const onKey = (e) => {
      if (!item || ["SELECT", "INPUT", "TEXTAREA"].includes(e.target.tagName) || e.target.isContentEditable) return;
      const pos = ordered.findIndex((c) => c.candidate_id === cursorId);
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); setCursorId(ordered[Math.min(pos + 1, ordered.length - 1)]?.candidate_id ?? cursorId); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); setCursorId(ordered[Math.max(pos - 1, 0)]?.candidate_id ?? cursorId); }
      else if (e.key >= "1" && e.key <= "9") { const n = Number(e.key) - 1; if (ordered[n]) setCursorId(ordered[n].candidate_id); }
      else if (e.key === "g" || e.key === "G") { if (cursorId) setGolden(cursorId); }
      else if (e.key === "a" || e.key === "A") { if (cursorId) toggleMark(cursorId, "acceptable"); }
      else if (e.key === "x" || e.key === "X") { if (cursorId) toggleMark(cursorId, "rejected"); }
      else if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "n" || e.key === "N") { markNone(); }
      else if (e.key === "d" || e.key === "D") { markDone(); }
      else if (e.key === "ArrowRight" || e.key === "s") { advance(); }
      else if (e.key === "t" || e.key === "T") { setVerb((v) => VERBS[(VERBS.indexOf(v) + 1) % VERBS.length]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, cursorId, ordered, commit, markNone, markDone, advance, setGolden, toggleMark]);

  const scaleBox = (bbox) => {
    if (!bbox || !imgDims) return null;
    const sx = imgDims.dispW / imgDims.natW, sy = imgDims.dispH / imgDims.natH;
    return { left: bbox.x * sx, top: bbox.y * sy, width: bbox.width * sx, height: bbox.height * sy };
  };

  const done = !loading && items.length > 0 && idx >= items.length;
  const labeled = stats.confirmed + stats.corrected + stats.none;
  const totalQ = queue?.unlabeled ?? 0;
  const progressPct = totalQ ? Math.min(100, Math.round((100 * labeled) / totalQ)) : 0;
  const agreePct = stats.confirmed + stats.corrected > 0 ? Math.round((100 * stats.confirmed) / (stats.confirmed + stats.corrected)) : null;
  const sug = item?.suggestion;
  const acceptCount = Object.values(marks).filter((v) => v === "acceptable").length;
  const rejectCount = Object.values(marks).filter((v) => v === "rejected").length;

  return (
    <section className="panel" style={{ padding: 22 }}>
      {/* header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, color: C.ink }}>Training Space</h2>
          <p style={{ margin: "6px 0 0", color: C.muted, fontSize: 13, maxWidth: 580 }}>
            Mark the <b style={{ color: C.blue }}>golden</b> pick (+ any <b style={{ color: C.teal }}>acceptable</b> alternates), then Save. <b>Everything you don't mark is a negative automatically</b> — only use <b style={{ color: C.red }}>✕</b> for a tempting wrong element. Each candidate is labeled on its own; tagging a container never labels what's inside it.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Stat value={stats.confirmed} label="confirmed" color={C.green} />
          <Stat value={stats.corrected} label="corrected" color={C.amber} />
          <Stat value={stats.none} label="needs vision" color={C.red} />
          <Stat value={stats.done} label="terminal" color={C.indigo} />
          {agreePct !== null ? <Stat value={`${agreePct}%`} label="agreement" color={C.blue} /> : null}
          <button className="ghost-btn small-btn" onClick={loadQueue} disabled={loading} title="Reload queue">↻</button>
        </div>
      </div>

      {/* progress */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12, color: C.muted }}>
          <span>{labeled} labeled this session{totalQ ? ` · ${Math.max(0, totalQ - labeled)} left` : ""}</span>
          {current ? (
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              <PriorityChip reason={current.priority_reason} conf={current.suggestion_confidence} />
              <span style={{ color: C.faint }}>{(current.url || "").replace("https://", "").slice(0, 56)}</span>
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
        {/* mission + trajectory (compact, gives the image room) */}
        <div style={{ display: "flex", gap: 10, alignItems: "stretch", marginBottom: 12, flexWrap: "wrap" }}>
          {item.context ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", padding: "8px 12px", background: C.blueSoft, borderRadius: 12, flex: 1, minWidth: 280 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: C.indigo, textTransform: "uppercase", letterSpacing: 0.5 }}>Mission</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: C.ink }}>{prettify(item.context.goal_id) || item.goal || "—"}</span>
              <span className="chip">{item.context.domain_id}</span>
              {item.context.scenario_id ? <span className="chip muted">{item.context.scenario_id}</span> : null}
              {item.context.element_query ? (
                <span style={{ fontSize: 12, color: C.muted, flex: 1, minWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>intent: “{item.context.element_query}”</span>
              ) : null}
            </div>
          ) : null}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <MiniNode label="from" node={item.trajectory?.prev} shotUrl={shotUrl} />
            <span style={{ color: C.faint }}>→</span>
            <MiniNode label="now" node={{ url: item.url, screenshot_filename: item.screenshot_filename, observed_page_state: fromState }} current shotUrl={shotUrl} />
            <span style={{ color: C.faint }}>→</span>
            <MiniNode label="actually led to" node={item.trajectory?.next} target shotUrl={shotUrl} />
          </div>
        </div>

        {/* BIG screenshot stage */}
        <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "center",
          background: C.surface, border: `1px solid ${C.line}`, borderRadius: 16, padding: 14, marginBottom: 12 }}>
          {shotUrl(item.screenshot_filename) ? (
            <div style={{ position: "relative", display: "inline-block", lineHeight: 0, borderRadius: 8, overflow: "hidden", boxShadow: "0 4px 16px rgba(15,23,42,0.10)" }}>
              <img ref={imgRef} src={shotUrl(item.screenshot_filename)} alt="capture"
                style={{ maxHeight: "64vh", maxWidth: "100%", width: "auto", height: "auto", display: "block" }} onLoad={recomputeDims} />
              {imgDims && cands.map((c) => {
                const box = scaleBox(c.bbox); if (!box) return null;
                const id = c.candidate_id;
                const mk = markOf(id); const m = mk ? MARK[mk] : null;
                const isCursor = id === cursorId, isHover = id === hoveredId;
                const isSug = !m && id === sug?.candidate_id;
                const active = !!m || isCursor || isHover;
                const color = m ? m.color : isCursor ? C.blue : isHover ? C.indigo : isSug ? C.amber : "rgba(99,102,241,0.5)";
                return (
                  <div key={id} title={`${c.role}: ${c.name}`}
                    onMouseEnter={() => setHoveredId(id)} onMouseLeave={() => setHoveredId((h) => (h === id ? null : h))}
                    onClick={() => { setCursorId(id); setGolden(id); }}
                    style={{ position: "absolute", ...box, borderRadius: 3, cursor: "pointer",
                      border: active ? `2px solid ${color}` : `1px solid rgba(148,163,184,0.4)`,
                      background: m ? m.fill : isHover ? "rgba(47,111,235,0.08)" : "transparent",
                      opacity: active || isSug ? 1 : 0.4,
                      zIndex: (active ? 1000 : 0) + (geom.z[id] || 0), transition: "opacity 100ms" }} />
                );
              })}
            </div>
          ) : <div style={{ color: C.muted, fontSize: 13, padding: 40 }}>No screenshot for this capture.</div>}
          {flash ? (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 16, background: "rgba(255,255,255,0.55)", pointerEvents: "none" }}>
              <span style={{ fontSize: 22, fontWeight: 700, color: flash.color, background: "#fff", padding: "10px 20px", borderRadius: 999, boxShadow: "0 6px 20px rgba(15,23,42,0.15)" }}>{flash.text} ✓</span>
            </div>
          ) : null}
        </div>

        {/* control bar */}
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          {sug?.candidate_id ? (
            <span style={{ fontSize: 12, color: C.amber, background: C.amberBg, border: `1px solid ${C.amberLine}`, borderRadius: 999, padding: "4px 10px" }}>
              ★ suggests <b>{candName(item, sug.candidate_id)}</b> · {sug.confidence}
            </span>
          ) : <span style={{ fontSize: 12, color: C.muted }}>cold state — no suggestion</span>}
          <div style={{ display: "inline-flex", background: C.surface, borderRadius: 10, padding: 3, gap: 2 }}>
            {VERBS.map((v) => (
              <button key={v} onClick={() => setVerb(v)} style={{ border: "none", cursor: "pointer", borderRadius: 8, padding: "5px 12px", fontSize: 12, fontWeight: 600,
                background: v === verb ? "#fff" : "transparent", color: v === verb ? C.blue : C.muted, boxShadow: v === verb ? "0 1px 3px rgba(15,23,42,0.12)" : "none" }}>{v}</button>
            ))}
          </div>
          <span style={{ fontSize: 12, marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <span style={{ color: C.blue, fontWeight: 600 }}>{goldenId ? 1 : 0} golden</span>
            <span style={{ color: C.teal, fontWeight: 600 }}>{acceptCount} acc</span>
            <span style={{ color: C.red, fontWeight: 600 }}>{rejectCount} rej</span>
            <span style={{ color: C.faint }}>· rest neg</span>
          </span>
          <button className="primary-btn" onClick={commit} disabled={busy || !goldenId}>
            {goldenId ? `Save (${(goldenId ? 1 : 0) + acceptCount + rejectCount})` : "Pick a golden first"} <Kbd dark>↵</Kbd>
          </button>
          <button className="ghost-btn" onClick={markDone} disabled={busy} title="Task already complete here (e.g. logged-in home) — record as a terminal state, no pick needed">Done <Kbd>D</Kbd></button>
          <button className="ghost-btn" onClick={markNone} disabled={busy} title="No candidate is correct / AX-blind → flag for the vision layer">None <Kbd>N</Kbd></button>
          <button className="ghost-btn" onClick={advance} disabled={busy}>Skip <Kbd>→</Kbd></button>
        </div>

        {/* stepped state picker — Current state → Expected next, organized folder/search
            selector reused from the vision labeler */}
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 12, padding: 12, marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            {[
              { f: "observed", n: 1, label: "Current state", val: fromState },
              { f: "post", n: 2, label: "Expected next", val: toState },
            ].map((s, i) => (
              <div key={s.f} style={{ display: "flex", alignItems: "center", gap: 8, flex: 1 }}>
                {i > 0 ? <span style={{ color: C.faint }}>→</span> : null}
                <button onClick={() => setActiveStateField(s.f)}
                  style={{ flex: 1, textAlign: "left", cursor: "pointer", borderRadius: 10, padding: "6px 10px",
                    border: `1px solid ${activeStateField === s.f ? C.blue : C.line}`,
                    background: activeStateField === s.f ? "rgba(47,111,235,0.08)" : "#fff" }}>
                  <div style={{ fontSize: 10, color: C.muted }}>{s.n}. {s.label}</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: s.val ? C.ink : C.faint }}>
                    {s.val ? prettify(pageStates.find((p) => p.state_id === s.val)?.display_name || s.val) : "choose…"}
                  </div>
                </button>
              </div>
            ))}
          </div>
          <PageStatePicker
            value={activeStateField === "observed" ? fromState : toState}
            onChange={(id) => {
              if (activeStateField === "observed") { setFromState(id); if (id) setActiveStateField("post"); }
              else setToState(id);
            }}
            onCreate={createPageState}
            options={pageStates} goals={goals} domains={domains}
            captureDomainId={item.context?.domain_id} captureGoalId={item.context?.goal_id}
            title={activeStateField === "observed" ? "Current Page State" : "Expected Next State"}
            helper={activeStateField === "observed" ? "What is visible before the action?" : "Where should the agent land if the action succeeds? (intended — not necessarily what actually happened)"}
          />
          {(() => {
            const sel = activeStateField === "observed" ? fromState : toState;
            const sd = pageStates.find((s) => s.state_id === sel)?.description;
            return sd ? (
              <div style={{ fontSize: 11, color: C.muted, background: C.surface, borderRadius: 8, padding: "6px 10px", marginTop: 8 }}>
                <b style={{ color: C.ink }}>{prettify(sel)}:</b> {sd}
              </div>
            ) : null;
          })()}
        </div>

        {/* candidate list — bottom, scrollable, indented by nesting */}
        <div style={{ border: `1px solid ${C.line}`, borderRadius: 12, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px", background: C.surface, fontSize: 11, color: C.muted }}>
            <span>{ordered.length} reachable · indented by nesting · hover to locate · mark <b style={{ color: C.blue }}>G</b>(olden){" "}<b style={{ color: C.teal }}>A</b>(ccept) · unmarked = negative</span>
            <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
              {item.hidden_count ? (
                <button className="ghost-btn" style={{ minHeight: 0, padding: "2px 8px", fontSize: 11 }} onClick={() => setShowAll((s) => !s)}>
                  {showAll ? "hide off-screen" : `show ${item.hidden_count} off-screen`}
                </button>
              ) : null}
              <span>cursor + <Kbd>G</Kbd><Kbd>A</Kbd><Kbd>X</Kbd></span>
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", maxHeight: 220, overflowY: "auto" }}>
            {ordered.map((c, i) => {
              const id = c.candidate_id;
              const mk = markOf(id); const m = mk ? MARK[mk] : null;
              const isCursor = id === cursorId, isHover = id === hoveredId, isSug = id === sug?.candidate_id;
              return (
                <div key={id} onClick={() => setCursorId(id)} onMouseEnter={() => setHoveredId(id)} onMouseLeave={() => setHoveredId((h) => (h === id ? null : h))}
                  style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", cursor: "pointer",
                    background: m ? m.fill : isHover ? "rgba(47,111,235,0.06)" : "#fff",
                    borderLeft: `3px solid ${m ? m.color : isCursor ? C.blue : "transparent"}`,
                    borderBottom: `1px solid ${C.line}` }}>
                  <span style={{ width: 16, color: C.faint, fontSize: 10, fontWeight: 700, textAlign: "right", flexShrink: 0 }}>{i < 9 ? i + 1 : ""}</span>
                  <span style={{ width: (geom.depth[id] || 0) * 16, flexShrink: 0 }} />
                  {geom.depth[id] ? <span style={{ color: C.faint, fontSize: 11, flexShrink: 0 }}>↳</span> : null}
                  <span className="chip muted" style={{ flexShrink: 0 }}>{c.role}</span>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13, color: C.ink,
                    textDecoration: mk === "rejected" ? "line-through" : "none", opacity: mk === "rejected" ? 0.6 : 1 }}>
                    {c.name || <span style={{ color: C.faint }}>—</span>}
                    {isSug ? <span title="model suggestion" style={{ color: C.amber, fontSize: 10, marginLeft: 6 }}>★sug</span> : null}
                  </span>
                  <div style={{ display: "inline-flex", gap: 3, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                    <TierBtn active={mk === "golden"} color={C.blue} title="Golden" onClick={() => { setCursorId(id); setGolden(id); }}>G</TierBtn>
                    <TierBtn active={mk === "acceptable"} color={C.teal} title="Acceptable" onClick={() => { setCursorId(id); toggleMark(id, "acceptable"); }}>A</TierBtn>
                    <TierBtn active={mk === "rejected"} color={C.red} title="Rejected" onClick={() => { setCursorId(id); toggleMark(id, "rejected"); }}>X</TierBtn>
                  </div>
                </div>
              );
            })}
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

// compact trajectory node for the top strip
function MiniNode({ label, node, current, target, shotUrl }) {
  const thumb = shotUrl(node?.screenshot_filename);
  const accent = current ? "#2f6feb" : target ? "#b45309" : "#94a3b8";
  return (
    <div style={{ width: 96, border: `1px solid ${current ? accent : "#e5edf6"}`, borderRadius: 10, overflow: "hidden", background: "#fff", opacity: node ? 1 : 0.5 }}>
      <div style={{ fontSize: 8, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", color: accent, padding: "3px 6px 1px" }}>{label}</div>
      <div style={{ height: 34, background: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
        {thumb ? <img src={thumb} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top" }} /> : <span style={{ fontSize: 9, color: "#9ca3af" }}>{target ? "end" : node ? "" : "start"}</span>}
      </div>
      <div style={{ fontSize: 9, color: "#6b7280", padding: "2px 6px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {node?.observed_page_state ? prettify(node.observed_page_state) : (node ? "—" : "")}
      </div>
    </div>
  );
}

function candName(item, id) {
  const c = (item.candidates ?? []).find((x) => x.candidate_id === id);
  return c ? `${c.role}: ${c.name || "—"}` : (id || "—");
}

function TierBtn({ active, color, title, onClick, children }) {
  return (
    <button title={title} onClick={onClick}
      style={{ width: 22, height: 22, borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer", padding: 0, lineHeight: 1,
        border: `1px solid ${active ? color : "#d4deeb"}`, background: active ? color : "#fff", color: active ? "#fff" : "#94a3b8" }}>
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
    <kbd style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", minWidth: 18, height: 18, padding: "0 4px", borderRadius: 5,
      fontSize: 11, fontWeight: 600, fontFamily: "inherit", background: dark ? "rgba(255,255,255,0.22)" : "#fff",
      color: dark ? "#fff" : "#475569", border: dark ? "1px solid rgba(255,255,255,0.35)" : "1px solid #d4deeb", boxShadow: dark ? "none" : "0 1px 0 #d4deeb" }}>{children}</kbd>
  );
}
