import { useCallback, useEffect, useState } from "react";
import { getJSON } from "./api";
import { AttentionInbox } from "./AttentionInbox";
import { ActivityFeed } from "./ActivityFeed";
import { DomainsHub } from "./DomainsHub";

// The Command Center — the platform's home. A cockpit, not a settings page: it answers "what
// needs me across everything, are my domains healthy, and what just happened" at a glance, then
// gets out of the way. The cross-domain Attention inbox is the primary surface; you interact by
// clearing exceptions, not by hunting for buttons.

const TONE_COLOR = { attention: "#ea580c", ready: "#16a34a", flywheel: "#d97706" };

function Hero({ summary, health, onOpenLabeler }) {
  const domains = summary?.domains || [];
  const ready = domains.filter((d) => d.status === "ready").length;
  const attention = summary?.attention_open_count ?? 0;
  const fw = summary?.flywheel;
  const toLabel = fw?.to_label_total ?? 0;
  const acc = fw?.grounding_accuracy;
  const accStr = acc == null ? "not trained yet" : `model ${Math.round(acc * 100)}%`;
  const cards = [
    { label: "Needs attention", value: attention, tone: attention ? "attention" : "ready", foot: attention ? "Open handoffs across domains" : "You're all caught up" },
    { label: "Domains ready", value: `${ready}/${domains.length}`, tone: "ready", foot: "Connected and signed in" },
    // The flywheel's headline number — brought up to KPI altitude and made one-click into the
    // queue labeler (#1 + #2 training-UI overhaul).
    { label: "🏷️ To label", value: toLabel, tone: toLabel ? "flywheel" : "ready", foot: toLabel ? "Label now →" : `${accStr} · ${fw?.labeled_total ?? 0} labeled`, onClick: toLabel ? onOpenLabeler : null },
    { label: "Control plane", value: health?.ok ? "Healthy" : "Issue", tone: health?.ok ? "ready" : "attention", foot: health?.ok ? "API reachable" : "API not reachable" },
  ];
  return (
    <section className="stats-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
      {cards.map((c) => (
        <div
          className="stat-card"
          key={c.label}
          onClick={c.onClick || undefined}
          role={c.onClick ? "button" : undefined}
          tabIndex={c.onClick ? 0 : undefined}
          style={{ borderTop: `3px solid ${TONE_COLOR[c.tone] || TONE_COLOR.ready}`, cursor: c.onClick ? "pointer" : "default" }}
        >
          <div className="stat-label">{c.label}</div>
          <div className="stat-value">{c.value}</div>
          <div className="stat-footnote">{c.foot}</div>
        </div>
      ))}
    </section>
  );
}

export function CommandCenter({ health, onOpenDomain, onOpenLabeler }) {
  const [summary, setSummary] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/command-center/summary").then(setSummary).catch(() => {});
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const tilesById = summary ? Object.fromEntries(summary.domains.map((t) => [t.id, t])) : undefined;

  return (
    <div className="section-body">
      <Hero summary={summary} health={health} onOpenLabeler={onOpenLabeler} />

      <AttentionInbox title="Needs your attention — across all domains" showDomainTag />

      <div>
        <div className="layer__title" style={{ margin: "4px 2px 12px" }}>🗂️ Domains</div>
        <DomainsHub onOpenDomain={onOpenDomain} tilesById={tilesById} />
      </div>

      <ActivityFeed items={summary?.activity || []} title="Recent activity — across all domains" showDomain />
    </div>
  );
}
