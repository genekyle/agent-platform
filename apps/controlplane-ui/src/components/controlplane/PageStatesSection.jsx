import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

// Starter category suggestions. Categories are free-text — type a new one (e.g.
// "Login") and it becomes part of the taxonomy. These just seed the combobox.
const DEFAULT_CATEGORIES = ["Login", "Navigation", "Content", "Error", "Checkout", "General"];

function Chevron({ open }) {
  return (
    <svg className={`ds-ico ds-chevron${open ? " open" : ""}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}
function GlobeIcon() {
  return (<svg className="ds-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.5 2.5 15 0 18M12 3c-2.5 2.5-2.5 15 0 18" /></svg>);
}
function FolderIcon() {
  return (<svg className="ds-ico ds-ico-folder" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h3.5l2 2H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" /></svg>);
}
function TagIcon() {
  return (<svg className="ds-ico ds-ico-layers" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.59 13.41 11 3.83A2 2 0 0 0 9.59 3H5a2 2 0 0 0-2 2v4.59A2 2 0 0 0 3.83 11l9.58 9.59a2 2 0 0 0 2.83 0l4.35-4.35a2 2 0 0 0 0-2.83z" /><circle cx="7.5" cy="7.5" r="0.5" /></svg>);
}
function ObjectiveIcon() {
  return (<svg className="ds-ico ds-ico-folder" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /></svg>);
}

export function PageStatesSection({ registry }) {
  const domains = registry?.domains ?? [];
  const scenarios = registry?.scenarios ?? [];
  const goals = registry?.goals ?? [];

  const [states, setStates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(new Set(["global"]));
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({
    display_name: "", scope: "global", domain_id: "", goal_id: "", scenario_id: "", category: "Login",
  });
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(`${API}/api/training/page-states`);
      if (!r.ok) throw new Error(`Failed: ${r.status}`);
      setStates(await r.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const createState = useCallback(async () => {
    const name = form.display_name.trim();
    if (!name) { setFormError("Name is required."); return; }
    if (form.scope === "domain" && !form.domain_id) { setFormError("Pick a domain."); return; }
    if (form.scope === "goal" && !form.goal_id) { setFormError("Pick an objective."); return; }
    if (form.scope === "scenario" && !form.scenario_id) { setFormError("Pick a scenario."); return; }
    setSaving(true); setFormError(null);
    try {
      const r = await fetch(`${API}/api/training/page-states`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: name,
          scope: form.scope,
          domain_id: form.scope === "global" ? null : form.domain_id || null,
          goal_id: (form.scope === "goal" || form.scope === "scenario") ? form.goal_id || null : null,
          scenario_id: form.scope === "scenario" ? form.scenario_id || null : null,
          category: form.category || "general",
        }),
      });
      const payload = await r.json();
      if (!r.ok) throw new Error(payload.detail || `Create failed: ${r.status}`);
      setForm((f) => ({ ...f, display_name: "" }));
      await load();
    } catch (e) { setFormError(e.message); }
    finally { setSaving(false); }
  }, [form, load]);

  const deleteState = useCallback(async (stateId) => {
    if (!confirm(`Archive state "${stateId}"? Existing captures keep the value; it just won't be offered anymore.`)) return;
    await fetch(`${API}/api/training/page-states/${encodeURIComponent(stateId)}`, { method: "DELETE" });
    await load();
  }, [load]);

  // Build tree: Scope-group ▸ Category ▸ states. Scope groups: Global, each Domain, each Objective(goal), each Scenario.
  const tree = useMemo(() => {
    const q = search.trim().toLowerCase();
    const match = (s) => !q || [s.display_name, s.state_id, s.category, s.domain_id, s.goal_id, s.scenario_id].filter(Boolean).join(" ").toLowerCase().includes(q);

    const groups = [];
    const globals = states.filter((s) => s.scope === "global" && match(s));
    if (globals.length || !q) groups.push({ key: "global", icon: "globe", label: "Global", sub: "applies to every capture", states: globals });

    const byDomain = new Map();
    states.filter((s) => s.scope === "domain" && match(s)).forEach((s) => {
      if (!byDomain.has(s.domain_id)) byDomain.set(s.domain_id, []);
      byDomain.get(s.domain_id).push(s);
    });
    for (const [domainId, list] of byDomain) {
      const dn = domains.find((d) => d.domain_id === domainId)?.display_name || domainId;
      groups.push({ key: `domain:${domainId}`, icon: "folder", label: dn, sub: `domain · ${domainId}`, states: list });
    }

    const byGoal = new Map();
    states.filter((s) => s.scope === "goal" && match(s)).forEach((s) => {
      if (!byGoal.has(s.goal_id)) byGoal.set(s.goal_id, []);
      byGoal.get(s.goal_id).push(s);
    });
    for (const [goalId, list] of byGoal) {
      const gn = goals.find((g) => g.goal_id === goalId)?.display_name || goalId;
      groups.push({ key: `goal:${goalId}`, icon: "objective", label: gn, sub: `objective · ${goalId}`, states: list });
    }

    const byScenario = new Map();
    states.filter((s) => s.scope === "scenario" && match(s)).forEach((s) => {
      if (!byScenario.has(s.scenario_id)) byScenario.set(s.scenario_id, []);
      byScenario.get(s.scenario_id).push(s);
    });
    for (const [scenarioId, list] of byScenario) {
      const sn = scenarios.find((sc) => sc.scenario_id === scenarioId)?.display_name || scenarioId;
      groups.push({ key: `scenario:${scenarioId}`, icon: "tag", label: sn, sub: `scenario · ${scenarioId}`, states: list });
    }

    // sub-group each group's states by category
    return groups.map((g) => {
      const byCat = new Map();
      g.states.forEach((s) => {
        const c = s.category || "general";
        if (!byCat.has(c)) byCat.set(c, []);
        byCat.get(c).push(s);
      });
      const categories = [...byCat.entries()].sort((a, b) => a[0].localeCompare(b[0]))
        .map(([cat, list]) => ({ cat, states: list.sort((a, b) => a.display_name.localeCompare(b.display_name)) }));
      return { ...g, categories, count: g.states.length };
    });
  }, [states, search, domains, goals, scenarios]);

  const toggle = (key) => setExpanded((prev) => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });

  const iconFor = (kind) => kind === "globe" ? <GlobeIcon /> : kind === "tag" ? <TagIcon /> : kind === "objective" ? <ObjectiveIcon /> : <FolderIcon />;

  return (
    <section className="panel obs-list-view">
      <div className="panel-header">
        <div>
          <h2>Page States</h2>
          <p>The state taxonomy — global, per-domain, and per-scenario, grouped by category. Used everywhere captures are labeled.</p>
        </div>
        <button className="ghost-btn" onClick={load}>Refresh</button>
      </div>

      {/* Create form */}
      <div className="ps-create">
        <input className="form-input ps-create-name" placeholder="New state name (e.g. Login Email Entered)"
          value={form.display_name} onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
          onKeyDown={(e) => { if (e.key === "Enter") createState(); }} />
        <select className="form-select" value={form.scope} onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value }))}>
          <option value="global">Global</option>
          <option value="domain">Domain</option>
          <option value="goal">Objective</option>
          <option value="scenario">Scenario</option>
        </select>
        {form.scope === "domain" && (
          <select className="form-select" value={form.domain_id} onChange={(e) => setForm((f) => ({ ...f, domain_id: e.target.value }))}>
            <option value="">Pick domain…</option>
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.display_name || d.domain_id}</option>)}
          </select>
        )}
        {form.scope === "goal" && (
          <select className="form-select" value={form.goal_id} onChange={(e) => setForm((f) => ({ ...f, goal_id: e.target.value }))}>
            <option value="">Pick objective…</option>
            {goals.map((g) => <option key={g.goal_id} value={g.goal_id}>{(g.display_name || g.goal_id) + (g.stage && g.stage !== "neutral" ? ` · ${g.stage}` : "")}</option>)}
          </select>
        )}
        {form.scope === "scenario" && (
          <select className="form-select" value={form.scenario_id} onChange={(e) => setForm((f) => ({ ...f, scenario_id: e.target.value }))}>
            <option value="">Pick scenario…</option>
            {scenarios.map((s) => <option key={s.scenario_id} value={s.scenario_id}>{s.display_name || s.scenario_id}</option>)}
          </select>
        )}
        <input className="form-input" list="ps-categories" placeholder="Category (e.g. Login)"
          value={form.category} onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))} style={{ maxWidth: 160 }} />
        <datalist id="ps-categories">
          {[...new Set([...DEFAULT_CATEGORIES, ...states.map((s) => s.category).filter(Boolean)])].map((c) => <option key={c} value={c} />)}
        </datalist>
        <button className="primary-btn" onClick={createState} disabled={saving}>{saving ? "Adding…" : "Add State"}</button>
      </div>
      {formError ? <div className="annotation-message error" style={{ marginTop: 8 }}>{formError}</div> : null}

      <div className="obs-toolbar" style={{ marginTop: 12 }}>
        <input className="obs-search-input form-input" placeholder="Search states, categories, domains…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {loading ? <div className="empty-state">Loading…</div>
        : error ? <div className="empty-state error">Error: {error}</div>
        : tree.length === 0 ? <div className="empty-state">No states yet. Add one above.</div>
        : (
          <div className="ds-tree">
            {tree.map((group) => {
              const open = expanded.has(group.key);
              return (
                <div key={group.key} className="ds-scenario">
                  <button className="ds-folder ds-scenario-header" onClick={() => toggle(group.key)}>
                    <Chevron open={open} />
                    {iconFor(group.icon)}
                    <span className="ds-folder-name">{group.label}</span>
                    <span className="ds-folder-meta">{group.sub} · {group.count} state{group.count !== 1 ? "s" : ""}</span>
                  </button>
                  {open && group.categories.map(({ cat, states: catStates }) => (
                    <div key={cat} className="ds-session">
                      <div className="ds-folder ds-session-header" style={{ cursor: "default" }}>
                        <span className="ds-caret" />
                        <span className="ps-cat-badge">{cat}</span>
                        <span className="ds-folder-meta">{catStates.length}</span>
                      </div>
                      <div className="ds-captures">
                        {catStates.map((s) => (
                          <div key={s.state_id} className="ds-capture ps-state-row">
                            <span className="ps-state-name">{s.display_name}</span>
                            <span className="ps-state-id mono">{s.state_id}</span>
                            <button className="table-delete-btn ds-mini-action" title="Archive state" onClick={() => deleteState(s.state_id)}>✕</button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
    </section>
  );
}
