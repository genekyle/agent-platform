import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const STAGES = ["unauthenticated", "authenticated", "neutral"];
const STAGE_LABEL = { unauthenticated: "Unauthenticated", authenticated: "Authenticated", neutral: "Neutral / Terminal" };
const C = { blue: "#2f6feb", ink: "#24344d", muted: "#6b7280", faint: "#9ca3af", line: "#e5edf6", green: "#15803d", amber: "#b45309", surface: "#f1f5f9" };

const NODE_W = 188, NODE_H = 46, COL_GAP = 150, ROW_GAP = 20, PAD = 24, COL_W = NODE_W + COL_GAP;

/**
 * State Graph — the agent's map of the world. Nodes = page-states, edges = transitions
 * (solid blue = human-labeled intended "this action → that state"; dashed grey = observed
 * in a real session). Laid out left→right by lifecycle stage. Thin/ghost nodes show where
 * the L3 classifier still needs examples.  GET /api/training/state_graph
 */
export function StateGraphSection() {
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [domain, setDomain] = useState(null);
  const [hover, setHover] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(`${API}/api/training/state_graph`);
      if (!r.ok) throw new Error(`Graph failed: ${r.status}`);
      const d = await r.json();
      setGraph(d);
      setDomain((cur) => cur ?? d.domains.find((x) => x !== "global") ?? d.domains[0] ?? null);
    } catch (e) { setError(e.message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // nodes for the selected domain + any state connected to them by an edge (so a domain
  // flow that escapes to a global captcha still shows the captcha node).
  const view = useMemo(() => {
    if (!graph || !domain) return { nodes: [], edges: [], width: 800, height: 300 };
    const inDomain = new Set(graph.nodes.filter((n) => n.domain === domain).map((n) => n.state_id));
    const connected = new Set(inDomain);
    graph.edges.forEach((e) => {
      if (inDomain.has(e.from) || inDomain.has(e.to)) { connected.add(e.from); connected.add(e.to); }
    });
    const nodeById = new Map(graph.nodes.map((n) => [n.state_id, n]));
    const nodes = [...connected].map((id) => nodeById.get(id) || { state_id: id, display_name: id, stage: "neutral", count: 0, category: "general" });
    const edges = graph.edges.filter((e) => connected.has(e.from) && connected.has(e.to));

    // lay out by stage column, stack within column
    const cols = { unauthenticated: [], authenticated: [], neutral: [] };
    nodes.forEach((n) => (cols[STAGES.includes(n.stage) ? n.stage : "neutral"]).push(n));
    Object.values(cols).forEach((list) => list.sort((a, b) => b.count - a.count || a.display_name.localeCompare(b.display_name)));
    const pos = {};
    STAGES.forEach((st, ci) => cols[st].forEach((n, ri) => { pos[n.state_id] = { x: PAD + ci * COL_W, y: PAD + 34 + ri * (NODE_H + ROW_GAP) }; }));
    const rows = Math.max(1, ...STAGES.map((st) => cols[st].length));
    return { nodes, edges, pos, cols, width: PAD * 2 + STAGES.length * COL_W - COL_GAP + 40, height: PAD + 34 + rows * (NODE_H + ROW_GAP) + 10 };
  }, [graph, domain]);

  const center = (id) => { const p = view.pos[id]; return p ? { x: p.x + NODE_W / 2, y: p.y + NODE_H / 2 } : null; };

  return (
    <section className="panel" style={{ padding: 22 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, color: C.ink }}>State Graph</h2>
          <p style={{ margin: "6px 0 0", color: C.muted, fontSize: 13, maxWidth: 620 }}>
            The agent's map of the world. <b style={{ color: C.blue }}>Solid</b> = human-labeled intended transition · <span style={{ color: C.faint }}>dashed</span> = observed in a real session. Laid out by lifecycle stage; thin/ghost nodes need more examples.
          </p>
        </div>
        <button className="ghost-btn small-btn" onClick={load} disabled={loading}>↻</button>
      </div>

      {error ? <div className="empty-state error">{error}</div> : null}

      {graph ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {graph.domains.map((d) => (
            <button key={d} onClick={() => setDomain(d)} className="chip"
              style={{ cursor: "pointer", background: d === domain ? C.blue : undefined, color: d === domain ? "#fff" : undefined }}>
              {d} ({graph.nodes.filter((n) => n.domain === d).length})
            </button>
          ))}
          <span style={{ marginLeft: "auto", fontSize: 12, color: C.muted, alignSelf: "center" }}>
            {graph.nodes.length} states · {graph.edges.length} transitions total
          </span>
        </div>
      ) : null}

      {graph ? (
        <div style={{ overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 12, background: "#fff" }}>
          <svg viewBox={`0 0 ${view.width} ${view.height}`} width={view.width} height={view.height} style={{ display: "block", minWidth: "100%" }}>
            <defs>
              <marker id="ah-blue" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill={C.blue} /></marker>
              <marker id="ah-grey" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill={C.faint} /></marker>
            </defs>
            {/* stage column headers */}
            {STAGES.map((st, ci) => (view.cols?.[st]?.length ? (
              <text key={st} x={PAD + ci * COL_W + NODE_W / 2} y={16} textAnchor="middle" fontSize="11" fontWeight="700" fill={C.faint}>{STAGE_LABEL[st]}</text>
            ) : null))}
            {/* edges */}
            {view.edges.map((e, i) => {
              const a = center(e.from), b = center(e.to); if (!a || !b) return null;
              const intended = e.intended > 0;
              const dim = hover && hover !== e.from && hover !== e.to;
              const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - 16;
              return (
                <g key={i} opacity={dim ? 0.12 : 1}>
                  <path d={`M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`} fill="none"
                    stroke={intended ? C.blue : C.faint} strokeWidth={intended ? 1.8 : 1.2}
                    strokeDasharray={intended ? "0" : "5 4"} markerEnd={`url(#${intended ? "ah-blue" : "ah-grey"})`} />
                  {e.actions.length && intended ? (
                    <text x={mx} y={my} textAnchor="middle" fontSize="9" fill={C.blue} style={{ pointerEvents: "none" }}>{e.actions.join("/")}</text>
                  ) : null}
                </g>
              );
            })}
            {/* nodes */}
            {view.nodes.map((n) => {
              const p = view.pos[n.state_id]; if (!p) return null;
              const ghost = n.count === 0, thin = n.count > 0 && n.count < 3 && !n.terminal;
              const stroke = n.terminal ? C.green : ghost ? C.faint : thin ? C.amber : C.blue;
              const bg = n.terminal ? "rgba(21,128,61,0.06)" : thin ? "rgba(180,83,9,0.06)" : "#fff";
              return (
                <g key={n.state_id} transform={`translate(${p.x},${p.y})`} opacity={hover && hover !== n.state_id ? 0.35 : 1}
                  onMouseEnter={() => setHover(n.state_id)} onMouseLeave={() => setHover((h) => (h === n.state_id ? null : h))} style={{ cursor: "default" }}>
                  <rect width={NODE_W} height={NODE_H} rx="9" fill={bg} stroke={stroke} strokeWidth="1.6" strokeDasharray={ghost ? "5 4" : "0"} />
                  <text x="10" y="19" fontSize="12" fontWeight="600" fill={C.ink}>{(n.display_name || n.state_id).slice(0, 24)}</text>
                  <text x="10" y="35" fontSize="9.5" fill={C.muted}>{ghost ? "expected · 0 captures" : `${n.count} capture${n.count === 1 ? "" : "s"}`}</text>
                  {n.has_golden ? <circle cx={NODE_W - 12} cy="13" r="3.5" fill={C.blue} /> : null}
                  {n.terminal ? <text x={NODE_W - 12} y="38" fontSize="11" textAnchor="end">🏁</text> : null}
                </g>
              );
            })}
          </svg>
        </div>
      ) : null}

      {graph ? (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 10, fontSize: 11, color: C.muted }}>
          <span><span style={{ color: C.blue }}>━</span> intended (labeled)</span>
          <span><span style={{ color: C.faint }}>┄</span> observed (session)</span>
          <span><span style={{ color: C.amber }}>▢</span> thin (&lt;3 — needs L3 examples)</span>
          <span style={{ color: C.faint }}>▢ ghost (expected, never captured)</span>
          <span><span style={{ color: C.green }}>▢</span> 🏁 terminal</span>
          <span><span style={{ color: C.blue }}>●</span> has golden pick</span>
        </div>
      ) : null}
      {!loading && graph && view.nodes.length === 0 ? <div className="empty-state">No states for this domain yet.</div> : null}
    </section>
  );
}
