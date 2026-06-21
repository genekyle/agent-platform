import { useCallback, useMemo, useState } from "react";

// Shared organized page-state selector — folder/breadcrumb/search over the taxonomy
// (Domain ▸ Stage ▸ Objective ▸ states) with inline new-state creation. Ported out of
// ObservationDetail so the vision labeler and the CDP-AX Training Space use ONE picker.
//
// Props:
//   value        selected page-state id ("" = none)
//   onChange     (id) => void   — fires on select/clear
//   onCreate     async (name, { category }) => createdState   — must return the new state
//   options      page states [{ page_state_id|state_id, display_name, scope, domain_id, goal_id, stage, category }]
//   goals        [{ goal_id, display_name, stage, domain_id }]
//   domains      [{ domain_id|id, display_name|label }]
//   captureDomainId / captureGoalId   — center the folder view + flag the home domain
//   title, helper

const STAGE_ORDER = ["unauthenticated", "authenticated", "neutral"];
const STAGE_LABEL = { unauthenticated: "Unauthenticated", authenticated: "Authenticated", neutral: "Unstaged" };
const sid = (s) => (s ? (s.page_state_id ?? s.state_id) : undefined);

export function PageStatePicker({
  value, onChange, onCreate, options = [], goals = [], domains = [],
  captureDomainId = null, captureGoalId = null, title, helper,
}) {
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState("Login");
  const [newDesc, setNewDesc] = useState("");
  const [error, setError] = useState(null);

  const goalById = useMemo(() => {
    const m = new Map();
    for (const g of goals || []) m.set(g.goal_id, g);
    return m;
  }, [goals]);
  const domainLabelOf = useCallback((id) => {
    if (id === "_unscoped") return "Generic (all domains)";
    const d = (domains || []).find((x) => (x.domain_id || x.id) === id);
    return d?.display_name || d?.label || id;
  }, [domains]);

  const sortCat = (m) =>
    [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([cat, list]) => [cat, list.sort((a, b) => a.display_name.localeCompare(b.display_name))]);

  const folderIndex = useMemo(() => {
    const stageOf = (s) => s.stage || ((s.scope === "goal" || s.scope === "scenario") && goalById.get(s.goal_id)?.stage) || "neutral";
    const domainsMap = new Map();
    const globalByCat = new Map();
    const push = (m, cat, s) => { if (!m.has(cat)) m.set(cat, []); m.get(cat).push(s); };
    const ensureStage = (domId, stage) => {
      if (!domainsMap.has(domId)) domainsMap.set(domId, new Map());
      const stages = domainsMap.get(domId);
      if (!stages.has(stage)) stages.set(stage, { objectives: new Map(), stageWide: new Map() });
      return stages.get(stage);
    };
    for (const s of options) {
      const cat = s.category || "general";
      if (s.scope === "global" || (!s.domain_id && !s.goal_id)) { push(globalByCat, cat, s); continue; }
      const domId = s.domain_id || goalById.get(s.goal_id)?.domain_id || "_unscoped";
      const st = ensureStage(domId, stageOf(s));
      if ((s.scope === "goal" || s.scope === "scenario") && s.goal_id) {
        if (!st.objectives.has(s.goal_id)) st.objectives.set(s.goal_id, { label: goalById.get(s.goal_id)?.display_name || s.goal_id, byCat: new Map() });
        push(st.objectives.get(s.goal_id).byCat, cat, s);
      } else push(st.stageWide, cat, s);
    }
    return { domainsMap, globalByCat };
  }, [options, goalById]);

  const countStage = (st) =>
    [...st.objectives.values()].reduce((n, o) => n + [...o.byCat.values()].reduce((k, l) => k + l.length, 0), 0)
    + [...st.stageWide.values()].reduce((n, l) => n + l.length, 0);
  const countDomain = (stages) => [...stages.values()].reduce((n, st) => n + countStage(st), 0);

  const knownCategories = useMemo(() => {
    const set = new Set(["Login", "Navigation", "Content", "Error", "Checkout", "Terminal", "General"]);
    for (const s of options) if (s.category) set.add(s.category);
    return [...set];
  }, [options]);

  const homeStage = captureGoalId ? (goalById.get(captureGoalId)?.stage || "neutral") : null;
  const homeNav = captureGoalId
    ? { level: "objective", domainId: captureDomainId, stage: homeStage, goalId: captureGoalId }
    : captureDomainId ? { level: "domain", domainId: captureDomainId } : { level: "root" };
  const [nav, setNav] = useState(homeNav);

  const selected = value ?? "";
  const selectedState = options.find((s) => sid(s) === selected) || null;
  const { domainsMap, globalByCat } = folderIndex;
  const q = search.trim().toLowerCase();
  const searchMatches = q
    ? options.filter((s) => [s.display_name, sid(s), s.category].filter(Boolean).join(" ").toLowerCase().includes(q))
    : [];

  const pick = (state) => { onChange(sid(state)); setSearch(""); };

  const create = async () => {
    if (!newName.trim() || !onCreate) return;
    setError(null);
    try {
      const created = await onCreate(newName.trim(), { category: newCategory, description: newDesc.trim() || null });
      if (created && sid(created)) { onChange(sid(created)); setNewName(""); setNewDesc(""); setAdding(false); }
    } catch (e) { setError(e.message || String(e)); }
  };

  const chip = (state) => (
    <button key={sid(state)} type="button"
      className={`dd-state-chip scope-${state.scope || "global"}${selected === sid(state) ? " selected" : ""}`}
      onClick={() => pick(state)} title={`${sid(state)} · ${state.scope}`}>
      {state.display_name}
      <span className="dd-state-scope-tag">{(state.scope || "global")[0].toUpperCase()}</span>
    </button>
  );
  const catBlocks = (byCat) => sortCat(byCat).map(([cat, list]) => (
    <div key={`cat-${cat}`} className="dd-state-cat-block">
      <div className="dd-state-cat-label">{cat}</div>
      <div className="dd-state-chip-row">{list.map(chip)}</div>
    </div>
  ));
  const folder = (key, cls, icon, name, count, onClick, badge) => (
    <button key={key} type="button" className={`dd-state-folder ${cls}`} onClick={onClick}>
      <span className="dd-state-folder-icon">{icon}</span>
      <span className="dd-state-folder-name">{name}</span>
      {badge ? <span className="dd-state-home-badge">{badge}</span> : null}
      <span className="dd-state-folder-count">{count}</span>
    </button>
  );

  const crumbs = [{ label: "All domains", nav: { level: "root" } }];
  if (["domain", "stage", "objective"].includes(nav.level) && nav.domainId)
    crumbs.push({ label: domainLabelOf(nav.domainId), nav: { level: "domain", domainId: nav.domainId } });
  if (["stage", "objective"].includes(nav.level) && nav.stage)
    crumbs.push({ label: STAGE_LABEL[nav.stage] || nav.stage, nav: { level: "stage", domainId: nav.domainId, stage: nav.stage } });
  if (nav.level === "objective" && nav.goalId)
    crumbs.push({ label: goalById.get(nav.goalId)?.display_name || nav.goalId, nav });

  const renderBody = () => {
    if (nav.level === "objective") {
      const obj = domainsMap.get(nav.domainId)?.get(nav.stage)?.objectives.get(nav.goalId);
      const blocks = obj ? catBlocks(obj.byCat) : [];
      return blocks.length ? <div className="dd-state-folder-body">{blocks}</div>
        : <div className="dd-state-empty">No states for this objective yet — add one below, or pop up a level.</div>;
    }
    if (nav.level === "stage") {
      const st = domainsMap.get(nav.domainId)?.get(nav.stage);
      const objEntries = st ? [...st.objectives.entries()].sort((a, b) => a[1].label.localeCompare(b[1].label)) : [];
      return (
        <div className="dd-state-folder-body">
          {objEntries.length ? (
            <div className="dd-state-folder-grid">
              {objEntries.map(([gid, o]) => folder(`objf-${gid}`, `stage-${nav.stage}`, "📁", o.label,
                [...o.byCat.values()].reduce((n, l) => n + l.length, 0),
                () => setNav({ level: "objective", domainId: nav.domainId, stage: nav.stage, goalId: gid })))}
            </div>
          ) : null}
          {st && st.stageWide.size ? (
            <div className="dd-state-domainwide">
              <div className="dd-state-section-label">{STAGE_LABEL[nav.stage]} · domain-wide states</div>
              {catBlocks(st.stageWide)}
            </div>
          ) : null}
          {!objEntries.length && !(st && st.stageWide.size) ? <div className="dd-state-empty">No states in this stage yet.</div> : null}
        </div>
      );
    }
    if (nav.level === "domain") {
      const stages = domainsMap.get(nav.domainId);
      const stageEntries = stages ? [...stages.entries()].sort((a, b) => STAGE_ORDER.indexOf(a[0]) - STAGE_ORDER.indexOf(b[0])) : [];
      return stageEntries.length ? (
        <div className="dd-state-folder-body">
          <div className="dd-state-folder-grid">
            {stageEntries.map(([stage, st]) => folder(`stagef-${stage}`, `stage-${stage}`, "🗂",
              STAGE_LABEL[stage] || stage, countStage(st), () => setNav({ level: "stage", domainId: nav.domainId, stage })))}
          </div>
        </div>
      ) : <div className="dd-state-empty">No states in this domain yet.</div>;
    }
    const domEntries = [...domainsMap.entries()].sort((a, b) => domainLabelOf(a[0]).localeCompare(domainLabelOf(b[0])));
    return (
      <div className="dd-state-folder-body">
        <div className="dd-state-folder-grid">
          {domEntries.map(([did, stages]) => folder(`domf-${did}`, did === captureDomainId ? "is-home" : "", "📂",
            domainLabelOf(did), countDomain(stages), () => setNav({ level: "domain", domainId: did }),
            did === captureDomainId ? "home" : null))}
        </div>
        {globalByCat.size ? (
          <div className="dd-state-domainwide">
            <div className="dd-state-section-label">Global states (every domain)</div>
            {catBlocks(globalByCat)}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="dd-state-picker">
      <div className="dd-state-picker-header">
        <div>
          {title ? <span className="dd-action-label">{title}</span> : null}
          {helper ? <span className="dd-state-helper">{helper}</span> : null}
        </div>
        <button type="button" className="ghost-btn dd-mini-btn" onClick={() => { setAdding((v) => !v); setError(null); }}>
          {adding ? "Cancel" : "+ New state"}
        </button>
      </div>

      <div className="dd-state-current">
        {selectedState ? (
          <span className={`dd-state-chip selected scope-${selectedState.scope || "global"}`}>
            {selectedState.display_name}
            <span className="dd-state-scope-tag">{(selectedState.scope || "global")[0].toUpperCase()}</span>
          </span>
        ) : <span className="dd-state-current-empty">Not set</span>}
        {selected ? <button type="button" className="ghost-btn dd-mini-btn" onClick={() => onChange("")}>Clear</button> : null}
      </div>

      <input className="form-input dd-state-search" placeholder="Search all states…"
        value={search} onChange={(e) => setSearch(e.target.value)} />

      {q ? (
        <div className="dd-state-chip-row dd-state-results">
          {searchMatches.length === 0 ? <div className="dd-state-empty">No states match “{search}”.</div> : searchMatches.map(chip)}
        </div>
      ) : (
        <>
          <nav className="dd-state-crumbs" aria-label="State folders">
            {crumbs.map((c, i) => (
              <span key={`crumb-${i}`} className="dd-state-crumb-wrap">
                {i > 0 ? <span className="dd-state-crumb-sep">›</span> : null}
                {i < crumbs.length - 1
                  ? <button type="button" className="dd-state-crumb-link" onClick={() => setNav(c.nav)}>{c.label}</button>
                  : <span className="dd-state-crumb-current">{c.label}</span>}
              </span>
            ))}
          </nav>
          {renderBody()}
        </>
      )}

      {adding ? (
        <div className="dd-state-add-row" style={{ flexWrap: "wrap" }}>
          <input className="form-input" value={newName} placeholder="New state name (e.g. Email Recognized — SSO or Code)"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); create(); } }} autoFocus />
          <input className="form-input" list="dd-known-categories" value={newCategory} placeholder="Category"
            onChange={(e) => setNewCategory(e.target.value)} />
          <datalist id="dd-known-categories">{knownCategories.map((c) => <option key={c} value={c} />)}</datalist>
          <button className="primary-btn" type="button" onClick={create} disabled={!newName.trim()}>Add &amp; select</button>
          <input className="form-input" value={newDesc} placeholder="Description — how to recognize this state (recommended)"
            onChange={(e) => setNewDesc(e.target.value)} style={{ flexBasis: "100%" }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); create(); } }} />
        </div>
      ) : null}
      {error ? <div className="dd-state-empty" style={{ color: "#dc2626" }}>{error}</div> : null}
    </div>
  );
}
