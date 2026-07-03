import { useCallback, useEffect, useState } from "react";
import { getJSON } from "./api";
import { AttentionInbox } from "./AttentionInbox";
import { ActivityFeed } from "./ActivityFeed";
import { DomainsHub } from "./DomainsHub";

// The Command Center — the platform's home. A cockpit, not a settings page: it answers "what
// needs me across everything, are my domains healthy, and what just happened" at a glance, then
// gets out of the way. The cross-domain Attention inbox is the primary surface; you interact by
// clearing exceptions, not by hunting for buttons.

function Hero({ summary, health }) {
  const domains = summary?.domains || [];
  const ready = domains.filter((d) => d.status === "ready").length;
  const attention = summary?.attention_open_count ?? 0;
  const cards = [
    { label: "Needs attention", value: attention, tone: attention ? "attention" : "ready", foot: attention ? "Open handoffs across domains" : "You're all caught up" },
    { label: "Domains ready", value: `${ready}/${domains.length}`, tone: "ready", foot: "Connected and signed in" },
    { label: "Control plane", value: health?.ok ? "Healthy" : "Issue", tone: health?.ok ? "ready" : "attention", foot: health?.ok ? "API reachable" : "API not reachable" },
  ];
  return (
    <section className="stats-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
      {cards.map((c) => (
        <div className="stat-card" key={c.label} style={{ borderTop: `3px solid ${c.tone === "attention" ? "#ea580c" : "#16a34a"}` }}>
          <div className="stat-label">{c.label}</div>
          <div className="stat-value">{c.value}</div>
          <div className="stat-footnote">{c.foot}</div>
        </div>
      ))}
    </section>
  );
}

export function CommandCenter({ health, onOpenDomain }) {
  const [summary, setSummary] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/command-center/summary").then(setSummary).catch(() => {});
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const tilesById = summary ? Object.fromEntries(summary.domains.map((t) => [t.id, t])) : undefined;

  return (
    <div className="section-body">
      <Hero summary={summary} health={health} />

      <AttentionInbox title="Needs your attention — across all domains" showDomainTag />

      <div>
        <div className="layer__title" style={{ margin: "4px 2px 12px" }}>🗂️ Domains</div>
        <DomainsHub onOpenDomain={onOpenDomain} tilesById={tilesById} />
      </div>

      <ActivityFeed items={summary?.activity || []} title="Recent activity — across all domains" showDomain />
    </div>
  );
}
