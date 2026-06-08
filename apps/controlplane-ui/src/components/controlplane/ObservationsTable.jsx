import { useEffect, useMemo, useRef, useState } from "react";

// Persists the dataset-browser view (expanded folders, group-by, search, filter,
// scroll) across the open-artifact → back round-trip. The component unmounts when an
// artifact opens, so without this the tree would collapse back to default on return.
// Module scope = survives remount within a session; resets on full page reload.
const VIEW_CACHE = { expanded: [], groupBy: "states", search: "", status: "", scrollTop: 0 };

// Minimal outline icons (stroke = currentColor) — no emoji, modern line style.
function Chevron({ open }) {
  return (
    <svg className={`ds-ico ds-chevron${open ? " open" : ""}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}
function DomainIcon() {
  return (<svg className="ds-ico ds-ico-folder" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 21V9" /></svg>);
}
function StageIcon() {
  return (<svg className="ds-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l3 7h7l-5.5 4 2 7-6.5-4.5L5.5 22l2-7L2 9h7z" /></svg>);
}
function ObjectiveIcon() {
  return (<svg className="ds-ico ds-ico-folder" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /></svg>);
}
function GroupIcon() {
  return (<svg className="ds-ico ds-ico-layers" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l9 5-9 5-9-5 9-5z" /><path d="M3 12l9 5 9-5" /></svg>);
}

// Short date like "Jun 2" — shown on each row instead of baking time into the name.
function fmtDate(ts) {
  if (!ts) return "";
  try { return new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" }); }
  catch { return ""; }
}

function humanizeId(id) {
  if (!id) return "";
  return String(id).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
function stateLabel(stateId, stateMeta) {
  if (!stateId) return "Unlabeled";
  return stateMeta?.[stateId]?.display_name || humanizeId(stateId);
}
// Leaf title — full context, NO timestamp. Prefer the human step instruction.
function leafTitle(obs) {
  const q = (obs.element_query || "").trim();
  if (q) return q.length > 72 ? `${q.slice(0, 70)}…` : q;
  const parts = [];
  if (obs.action_type && obs.action_type !== "any") parts.push(obs.action_type);
  if (obs.action_text) parts.push(obs.action_text);
  return parts.filter(Boolean).join(" · ") || obs.filename;
}

const STATUS_OPTIONS = ["draft", "reviewed", "approved", "rejected", "archived"];
const GROUP_BY_OPTIONS = [
  { id: "states", label: "State" },   // nested Category ▸ State (mirrors the labeler)
  { id: "session", label: "Session" },
  { id: "action", label: "Action" },
];
// Stage display + ordering (the agent lifecycle phase, above objectives).
const STAGE_ORDER = ["unauthenticated", "authenticated", "neutral"];
const STAGE_LABEL = { unauthenticated: "Unauthenticated", authenticated: "Authenticated", neutral: "Unstaged" };

export function ObservationsTable({
  observations,
  stateMeta = {},
  domainMeta = {},
  goalMeta = {},
  title,
  subtitle,
  loading,
  error,
  justCapturedFilename,
  loadObservations,
  onOpenObservation,
  updateObsMeta,
  deleteObservation,
  bulkDeleteObservations,
  resetAllTrainingData,
  emptyMessage,
}) {
  const [obsSelection, setObsSelection] = useState(new Set());
  const [obsStatusFilter, setObsStatusFilter] = useState(VIEW_CACHE.status);
  const [obsSearch, setObsSearch] = useState(VIEW_CACHE.search);
  const [groupBy, setGroupBy] = useState(VIEW_CACHE.groupBy);
  const [expanded, setExpanded] = useState(() => new Set(VIEW_CACHE.expanded)); // single set keyed by node path
  const treeScrollRef = useRef(null);
  const [editingTitle, setEditingTitle] = useState(null);
  const [titleDraft, setTitleDraft] = useState("");

  const statusCounts = useMemo(() => {
    const counts = {};
    observations.forEach((item) => { const s = item.status || "new"; counts[s] = (counts[s] || 0) + 1; });
    return counts;
  }, [observations]);

  const domainLabel = (id) => domainMeta?.[id]?.display_name || humanizeId(id) || "Unassigned domain";
  const goalLabel = (id) => goalMeta?.[id]?.display_name || humanizeId(id) || "Unassigned objective";
  // Stage of a CAPTURE comes from its observed page state (a single objective like
  // log_in spans both stages: the login flow is unauthenticated, but its success
  // landing — facebook_home_logged_in — is authenticated). Fall back to the goal's
  // stage when the state has none, then neutral.
  const stageOfCapture = (o) =>
    stateMeta?.[o.observed_page_state]?.stage
    || goalMeta?.[o.goal_id]?.stage
    || "neutral";

  // Domain ▸ Stage ▸ Objective(goal) ▸ Group(by) ▸ Captures.
  const tree = useMemo(() => {
    const q = obsSearch.trim().toLowerCase();
    const matches = (o) => {
      if (obsStatusFilter && (o.status || "new") !== obsStatusFilter) return false;
      if (!q) return true;
      const hay = [o.filename, o.domain_id, o.goal_id, o.scenario_id, o.scenario, o.page_url, o.page_title,
        o.title, o.task_id, o.observed_page_state, stateLabel(o.observed_page_state, stateMeta),
        o.action_type, o.action_text, o.status, goalLabel(o.goal_id), domainLabel(o.domain_id)]
        .filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    };

    const reviewedOf = (caps) => caps.filter((c) => c.status === "reviewed" || c.status === "approved").length;
    const byTime = (caps) => [...caps].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

    // Per-objective bucketing. "states" mode mirrors the labeler: Category ▸ State ▸ captures.
    // "session"/"action" are flat single-level buckets.
    const buildBuckets = (caps) => {
      if (groupBy === "states") {
        const byCat = new Map();
        for (const c of caps) {
          const cat = (c.observed_page_state && stateMeta?.[c.observed_page_state]?.category) || "Uncategorized";
          const stKey = stateLabel(c.observed_page_state, stateMeta);
          if (!byCat.has(cat)) byCat.set(cat, new Map());
          const m = byCat.get(cat);
          if (!m.has(stKey)) m.set(stKey, []);
          m.get(stKey).push(c);
        }
        return [...byCat.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([cat, m]) => {
          const sub = [...m.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([stKey, cs]) => {
            const sorted = byTime(cs);
            return { key: stKey, label: stKey, captures: sorted, count: sorted.length, reviewed: reviewedOf(sorted) };
          });
          return { key: cat, label: cat, sub, count: sub.reduce((n, s) => n + s.count, 0), reviewed: sub.reduce((n, s) => n + s.reviewed, 0) };
        });
      }
      const byKey = new Map();
      for (const c of caps) {
        const k = groupBy === "session"
          ? `Session ${c.training_session_id ?? "—"}`
          : (c.action_type && c.action_type !== "any" ? c.action_type : "unspecified");
        if (!byKey.has(k)) byKey.set(k, []);
        byKey.get(k).push(c);
      }
      return [...byKey.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([k, cs]) => {
        const sorted = byTime(cs);
        return { key: k, label: k, captures: sorted, count: sorted.length, reviewed: reviewedOf(sorted) };
      });
    };

    // Collect captures into Domain ▸ Stage ▸ Objective, then bucket per objective.
    const domains = new Map();
    for (const o of observations) {
      if (!matches(o)) continue;
      const dId = o.domain_id || "unassigned";
      const st = stageOfCapture(o);
      const gId = o.goal_id || "unassigned";
      if (!domains.has(dId)) domains.set(dId, new Map());
      const stages = domains.get(dId);
      if (!stages.has(st)) stages.set(st, new Map());
      const goals = stages.get(st);
      if (!goals.has(gId)) goals.set(gId, []);
      goals.get(gId).push(o);
    }

    const out = [];
    for (const [dId, stages] of domains) {
      const stageList = [];
      let dCount = 0, dRev = 0;
      const sortedStages = [...stages.entries()].sort((a, b) => STAGE_ORDER.indexOf(a[0]) - STAGE_ORDER.indexOf(b[0]));
      for (const [stage, goals] of sortedStages) {
        const objList = [];
        let sCount = 0, sRev = 0;
        const sortedGoals = [...goals.entries()].sort((a, b) => goalLabel(a[0]).localeCompare(goalLabel(b[0])));
        for (const [gId, caps] of sortedGoals) {
          const buckets = buildBuckets(caps);
          const gCount = caps.length;
          const gRev = reviewedOf(caps);
          objList.push({ goalId: gId, label: goalLabel(gId), buckets, count: gCount, reviewed: gRev });
          sCount += gCount; sRev += gRev;
        }
        stageList.push({ stage, label: STAGE_LABEL[stage] || stage, objectives: objList, count: sCount, reviewed: sRev });
        dCount += sCount; dRev += sRev;
      }
      out.push({ domainId: dId, label: domainLabel(dId), stages: stageList, count: dCount, reviewed: dRev });
    }
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }, [observations, obsSearch, obsStatusFilter, groupBy, stateMeta, domainMeta, goalMeta]);

  // Collect every node path for expand-all (handles the optional Category ▸ State sub-level).
  const allPaths = useMemo(() => {
    const paths = [];
    for (const d of tree) {
      paths.push(d.domainId);
      for (const s of d.stages) {
        paths.push(`${d.domainId}/${s.stage}`);
        for (const o of s.objectives) {
          const oPath = `${d.domainId}/${s.stage}/${o.goalId}`;
          paths.push(oPath);
          for (const b of o.buckets) {
            const bPath = `${oPath}/${b.key}`;
            paths.push(bPath);
            for (const sub of (b.sub || [])) paths.push(`${bPath}/${sub.key}`);
          }
        }
      }
    }
    return paths;
  }, [tree]);

  const expandAll = () => setExpanded(new Set(allPaths));
  const collapseAll = () => setExpanded(new Set());
  useEffect(() => { if (obsSearch.trim()) setExpanded(new Set(allPaths)); /* eslint-disable-line */ }, [obsSearch, allPaths]);

  const toggle = (path) => setExpanded((prev) => { const n = new Set(prev); n.has(path) ? n.delete(path) : n.add(path); return n; });

  // Persist the view to the module cache so opening an artifact and clicking the
  // breadcrumb back restores the same expanded tree, filters, and scroll position.
  useEffect(() => {
    VIEW_CACHE.expanded = [...expanded];
    VIEW_CACHE.groupBy = groupBy;
    VIEW_CACHE.search = obsSearch;
    VIEW_CACHE.status = obsStatusFilter;
  }, [expanded, groupBy, obsSearch, obsStatusFilter]);

  // Restore scroll on mount and capture it as the user scrolls. The tree itself
  // doesn't scroll — its nearest scrollable ancestor (panel or page) does — so find
  // that at runtime and bind to it.
  useEffect(() => {
    let el = treeScrollRef.current;
    let scroller = null;
    while (el && el !== document.body) {
      const oy = getComputedStyle(el).overflowY;
      if (oy === "auto" || oy === "scroll") { scroller = el; break; }
      el = el.parentElement;
    }
    const win = !scroller;
    const target = scroller || window;
    if (VIEW_CACHE.scrollTop) {
      requestAnimationFrame(() => {
        if (win) window.scrollTo(0, VIEW_CACHE.scrollTop);
        else scroller.scrollTop = VIEW_CACHE.scrollTop;
      });
    }
    const onScroll = () => { VIEW_CACHE.scrollTop = win ? window.scrollY : scroller.scrollTop; };
    target.addEventListener("scroll", onScroll, { passive: true });
    return () => target.removeEventListener("scroll", onScroll);
  }, []);
  const startRename = (obs, fallback) => { setEditingTitle(obs.filename); setTitleDraft(obs.title || fallback); };
  const commitRename = (obs) => { updateObsMeta(obs.filename, { title: titleDraft.trim() || "" }); setEditingTitle(null); setTitleDraft(""); };

  const totalShown = tree.reduce((n, d) => n + d.count, 0);
  const meta = (count, reviewed) => (
    <span className="ds-folder-meta">{count} capture{count !== 1 ? "s" : ""} · <span className="ds-reviewed">{reviewed} reviewed</span></span>
  );

  // Reusable capture-list (used under both nested State leaves and flat buckets).
  const renderCaptures = (caps) => (
    <div className="ds-captures">
      {caps.map((obs) => {
        const fallback = leafTitle(obs);
        const display = obs.title || fallback;
        const isEditing = editingTitle === obs.filename;
        return (
          <div key={obs.filename}
            className={`ds-capture ${justCapturedFilename === obs.filename ? "just-captured" : ""} ${obsSelection.has(obs.filename) ? "multi-selected" : ""}`}>
            <input className="table-checkbox ds-capture-check" type="checkbox"
              checked={obsSelection.has(obs.filename)}
              onChange={(e) => setObsSelection((prev) => {
                const n = new Set(prev); e.target.checked ? n.add(obs.filename) : n.delete(obs.filename); return n;
              })} />
            <span className={`ds-dot status-${obs.status || "new"}`} title={obs.status} />
            {isEditing ? (
              <input className="form-input ds-title-input" autoFocus value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename(obs);
                  if (e.key === "Escape") { setEditingTitle(null); setTitleDraft(""); }
                }}
                onBlur={() => commitRename(obs)} />
            ) : (
              <button className="ds-capture-title" onClick={() => onOpenObservation(obs.filename)} title="Open for review">{display}</button>
            )}
            <span className="ds-capture-badges">
              {obs.approved_bbox ? <span className="ds-mini-badge bbox" title="Has approved bbox">▣</span> : null}
              {obs.has_screenshot ? <span className="ds-mini-badge shot" title="Has screenshot">img</span> : null}
            </span>
            <span className="ds-capture-date" title={obs.timestamp ? new Date(obs.timestamp).toLocaleString() : ""}>{fmtDate(obs.timestamp)}</span>
            <select className={`status-select form-select status-${obs.status || "new"} ds-capture-status`}
              value={obs.status || "new"} onChange={(e) => updateObsMeta(obs.filename, { status: e.target.value })}>
              {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button className="ghost-btn ds-mini-action" title="Rename" onClick={() => startRename(obs, fallback)}>✎</button>
            <button className="table-delete-btn ds-mini-action" title="Delete"
              onClick={() => { if (confirm("Delete this artifact?")) deleteObservation(obs.filename); }}>✕</button>
          </div>
        );
      })}
    </div>
  );

  return (
    <section className="panel obs-list-view">
      <div className="panel-header">
        <div><h2>{title}</h2><p>{subtitle}</p></div>
        {resetAllTrainingData ? (
          <button className="danger-btn" onClick={resetAllTrainingData}
            title="Wipe all sessions and captures. Registry (domains/goals/etc.) is preserved.">
            Reset all training data
          </button>
        ) : null}
      </div>

      <div className="obs-toolbar">
        <input className="obs-search-input form-input" type="text"
          placeholder="Search domain, objective, state, title, url..."
          value={obsSearch} onChange={(e) => setObsSearch(e.target.value)} />
        <div className="obs-filter-row">
          <div className="obs-filter-chips">
            <button className={`filter-chip ${obsStatusFilter === "" ? "active" : ""}`} onClick={() => setObsStatusFilter("")}>
              All ({observations.length})
            </button>
            {Object.entries(statusCounts).map(([status, count]) => (
              <button key={status} className={`filter-chip status-${status} ${obsStatusFilter === status ? "active" : ""}`}
                onClick={() => setObsStatusFilter(obsStatusFilter === status ? "" : status)}>
                {status} ({count})
              </button>
            ))}
          </div>
          <div className="obs-toolbar-actions">
            <span className="ds-groupby-label">Group by</span>
            <div className="ds-groupby">
              {GROUP_BY_OPTIONS.map((o) => (
                <button key={o.id} className={`ds-groupby-btn${groupBy === o.id ? " active" : ""}`} onClick={() => setGroupBy(o.id)}>{o.label}</button>
              ))}
            </div>
            {obsSelection.size > 0 && (
              <button className="danger-btn small-btn" onClick={() => {
                if (confirm(`Delete ${obsSelection.size} artifact(s)?`)) { bulkDeleteObservations([...obsSelection]); setObsSelection(new Set()); }
              }}>Delete {obsSelection.size}</button>
            )}
            <button className="ghost-btn small-btn" onClick={expandAll}>Expand all</button>
            <button className="ghost-btn small-btn" onClick={collapseAll}>Collapse all</button>
            <button className="ghost-btn small-btn" onClick={loadObservations}>Refresh</button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : error ? (
        <div className="empty-state error">Error: {error}</div>
      ) : totalShown === 0 ? (
        <div className="empty-state">{observations.length === 0 ? emptyMessage : "No artifacts match your filters."}</div>
      ) : (
        <div className="ds-tree" ref={treeScrollRef}>
          {tree.map((domain) => {
            const dOpen = expanded.has(domain.domainId);
            return (
              <div key={domain.domainId} className="ds-scenario">
                <button className="ds-folder ds-scenario-header" onClick={() => toggle(domain.domainId)}>
                  <Chevron open={dOpen} /><DomainIcon />
                  <span className="ds-folder-name">{domain.label}</span>
                  {meta(domain.count, domain.reviewed)}
                </button>

                {dOpen && domain.stages.map((stage) => {
                  const sPath = `${domain.domainId}/${stage.stage}`;
                  const sOpen = expanded.has(sPath);
                  return (
                    <div key={sPath} className="ds-stage">
                      <button className={`ds-folder ds-stage-header stage-${stage.stage}`} onClick={() => toggle(sPath)}>
                        <Chevron open={sOpen} /><StageIcon />
                        <span className="ds-folder-name">{stage.label}</span>
                        {meta(stage.count, stage.reviewed)}
                      </button>

                      {sOpen && stage.objectives.map((obj) => {
                        const oPath = `${sPath}/${obj.goalId}`;
                        const oOpen = expanded.has(oPath);
                        return (
                          <div key={oPath} className="ds-objective">
                            <button className="ds-folder ds-objective-header" onClick={() => toggle(oPath)}>
                              <Chevron open={oOpen} /><ObjectiveIcon />
                              <span className="ds-folder-name">{obj.label}</span>
                              {meta(obj.count, obj.reviewed)}
                            </button>

                            {oOpen && obj.buckets.map((bucket) => {
                              const bPath = `${oPath}/${bucket.key}`;
                              const bOpen = expanded.has(bPath);
                              return (
                                <div key={bPath} className="ds-group">
                                  <button className="ds-folder ds-group-header" onClick={() => toggle(bPath)}>
                                    <Chevron open={bOpen} /><GroupIcon />
                                    <span className="ds-group-name">{bucket.label}</span>
                                    {meta(bucket.count, bucket.reviewed)}
                                  </button>

                                  {bOpen && (bucket.sub
                                    ? bucket.sub.map((sub) => {
                                        const subPath = `${bPath}/${sub.key}`;
                                        const subOpen = expanded.has(subPath);
                                        return (
                                          <div key={subPath} className="ds-statelevel">
                                            <button className="ds-folder ds-statelevel-header" onClick={() => toggle(subPath)}>
                                              <Chevron open={subOpen} /><GroupIcon />
                                              <span className="ds-group-name">{sub.label}</span>
                                              {meta(sub.count, sub.reviewed)}
                                            </button>
                                            {subOpen && renderCaptures(sub.captures)}
                                          </div>
                                        );
                                      })
                                    : renderCaptures(bucket.captures))}
                                </div>
                              );
                            })}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
