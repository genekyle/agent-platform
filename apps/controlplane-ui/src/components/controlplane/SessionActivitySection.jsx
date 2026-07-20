import { useEffect, useMemo, useState } from "react";
import { AppIcon } from "../../ui/Icon";
import { getJSON, fmtTime } from "./workspace/api";

const KIND = {
  reasoning: { label: "Reasoning", tone: "reasoning" },
  action: { label: "Action", tone: "accent" },
  event: { label: "Event", tone: "neutral" },
  escalation: { label: "Handoff", tone: "warning" },
  error: { label: "Error", tone: "danger" },
  api: { label: "API", tone: "neutral" },
};
const FEED_KINDS = ["reasoning", "action", "escalation", "error", "event"];

function Entry({ entry, selected, onSelect }) {
  const kind = KIND[entry.kind] || KIND.event;
  const meta = entry.meta || {};
  const chips = [
    meta.rung ? `rung ${meta.rung}` : null,
    meta.state,
    meta.ats,
    meta.outcome && meta.outcome !== "ok" ? meta.outcome : null,
    Array.isArray(meta.evidence) && meta.evidence.length ? `${meta.evidence.length} citations` : null,
    entry.session ? `session ${entry.session}` : null,
  ].filter(Boolean);

  return (
    <button type="button" className={`console-entry console-entry--${kind.tone} ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="console-entry__rail" />
      <span className="console-entry__body">
        <span className="console-entry__topline">
          <span className="console-kind">{kind.label}</span>
          <strong>{entry.title || "Untitled event"}</strong>
          <time>{fmtTime(entry.ts)}</time>
        </span>
        {entry.detail ? <span className={`console-entry__detail ${entry.kind === "reasoning" ? "is-reasoning" : ""}`}>{entry.detail}</span> : null}
        {chips.length ? <span className="console-entry__meta">{chips.map((chip) => <span key={chip}>{chip}</span>)}</span> : null}
      </span>
      <AppIcon name="chevronRight" size={15} className="console-entry__chevron" />
    </button>
  );
}

function Inspector({ entry }) {
  if (!entry) {
    return (
      <div className="console-inspector__empty">
        <AppIcon name="inspect" size={24} />
        <strong>Select an entry</strong>
        <span>Its context, evidence, and raw metadata will appear here.</span>
      </div>
    );
  }
  const kind = KIND[entry.kind] || KIND.event;
  return (
    <div className="console-inspector__content">
      <div className="console-inspector__head">
        <span className={`console-tone console-tone--${kind.tone}`} />
        <span>{kind.label}</span>
        <time>{fmtTime(entry.ts)}</time>
      </div>
      <h3>{entry.title || "Untitled event"}</h3>
      {entry.detail ? <p>{entry.detail}</p> : <p className="muted-copy">No additional detail was recorded.</p>}
      <dl className="console-inspector__facts">
        {entry.source ? <><dt>Source</dt><dd>{entry.source}</dd></> : null}
        {entry.session ? <><dt>Session</dt><dd>{entry.session}</dd></> : null}
        {Object.entries(entry.meta || {}).map(([key, value]) => (
          <div className="console-inspector__fact" key={key}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function SessionActivitySection() {
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [paused, setPaused] = useState(false);
  const [active, setActive] = useState(new Set(FEED_KINDS));
  const [includeApi, setIncludeApi] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const data = await getJSON(`/api/activity?domain=career_search&limit=250${includeApi ? "&include_api=true" : ""}`);
        if (alive) {
          const next = data.entries || [];
          setEntries(next);
          setSelected((current) => current || next[0] || null);
          setError(null);
        }
      } catch (caught) {
        if (alive) setError(String(caught));
      }
    };
    tick();
    const timer = paused ? null : setInterval(tick, 5000);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [includeApi, paused, reloadKey]);

  const toggle = (kind) => setActive((previous) => {
    const next = new Set(previous);
    if (next.has(kind)) next.delete(kind); else next.add(kind);
    return next;
  });

  const shown = useMemo(() => entries.filter((entry) => {
    const matchesKind = active.has(entry.kind);
    const haystack = `${entry.title || ""} ${entry.detail || ""}`.toLowerCase();
    return matchesKind && (!query || haystack.includes(query.toLowerCase()));
  }), [entries, active, query]);

  const counts = useMemo(() => ({
    reasoning: entries.filter((entry) => entry.kind === "reasoning").length,
    actions: entries.filter((entry) => entry.kind === "action").length,
    handoffs: entries.filter((entry) => entry.kind === "escalation").length,
    errors: entries.filter((entry) => entry.kind === "error").length,
  }), [entries]);

  return (
    <div className="activity-console">
      <section className="console-summary">
        <div>
          <span className="ops-eyebrow">Career Search scope</span>
          <h2>Agent console</h2>
          <p>See what happened, why the agent chose it, and where human judgment entered the loop.</p>
        </div>
        <div className="console-summary__metrics">
          <span><strong>{counts.actions}</strong> actions</span>
          <span><strong>{counts.reasoning}</strong> reasoning</span>
          <span><strong>{counts.handoffs}</strong> handoffs</span>
          <span className={counts.errors ? "has-errors" : ""}><strong>{counts.errors}</strong> errors</span>
        </div>
      </section>

      <section className="console-toolbar" aria-label="Activity filters">
        <div className="console-filters">
          {FEED_KINDS.map((kind) => (
            <button key={kind} type="button" className={`console-filter console-filter--${KIND[kind].tone} ${active.has(kind) ? "is-active" : ""}`} onClick={() => toggle(kind)}>
              <span />{KIND[kind].label}
            </button>
          ))}
          <label className="console-api-toggle">
            <input
              type="checkbox"
              checked={includeApi}
              onChange={(event) => {
                setIncludeApi(event.target.checked);
                setActive((previous) => {
                  const next = new Set(previous);
                  if (event.target.checked) next.add("api"); else next.delete("api");
                  return next;
                });
              }}
            />
            API
          </label>
        </div>
        <label className="console-search">
          <AppIcon name="search" size={15} />
          <input aria-label="Filter activity" placeholder="Filter activity" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <div className="console-controls">
          <button type="button" className="icon-btn" aria-label={paused ? "Resume live updates" : "Pause live updates"} onClick={() => setPaused((value) => !value)}>
            <AppIcon name={paused ? "play" : "pause"} size={16} />
          </button>
          <button type="button" className="icon-btn" aria-label="Refresh activity" onClick={() => setReloadKey((key) => key + 1)}>
            <AppIcon name="refresh" size={16} />
          </button>
        </div>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      <section className="console-frame">
        <div className="console-stream">
          <div className="console-stream__status">
            <span><span className={`status-dot ${paused ? "" : "is-ok"}`} />{paused ? "Paused" : "Live · refreshes every 5 seconds"}</span>
            <span>{shown.length} of {entries.length}</span>
          </div>
          <div className="console-entry-list">
            {shown.length ? shown.map((entry, index) => (
              <Entry key={entry.id || `${entry.ts}-${index}`} entry={entry} selected={selected === entry} onSelect={() => setSelected(entry)} />
            )) : (
              <div className="console-empty">No events match these filters.</div>
            )}
          </div>
        </div>
        <aside className="console-inspector" aria-label="Selected activity detail"><Inspector entry={selected} /></aside>
      </section>
    </div>
  );
}
