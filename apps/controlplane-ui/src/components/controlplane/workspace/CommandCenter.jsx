import { useCallback, useEffect, useMemo, useState } from "react";
import { AppIcon } from "../../../ui/Icon";
import { getJSON } from "./api";
import { AttentionInbox } from "./AttentionInbox";
import { ActivityFeed } from "./ActivityFeed";
import { DomainsHub } from "./DomainsHub";

function Metric({ label, value, detail, tone = "neutral" }) {
  return (
    <article className={`overview-metric overview-metric--${tone}`}>
      <span className="overview-metric__label">{label}</span>
      <strong>{value}</strong>
      <span>{detail}</span>
    </article>
  );
}

function OverviewHero({ summary, health, onOpenLabeler }) {
  const domains = summary?.domains || [];
  const ready = domains.filter((domain) => domain.status === "ready").length;
  const attention = summary?.attention_open_count ?? 0;
  const toLabel = summary?.flywheel?.to_label_total ?? 0;
  const active = domains.filter((domain) => domain.status && domain.status !== "idle").length;

  const message = attention
    ? `${attention} handoff${attention === 1 ? "" : "s"} need your judgment. Everything else can keep moving.`
    : active
      ? "Your agents are moving. Nothing needs your judgment right now."
      : "Your workspace is quiet. Start with a domain when you are ready.";

  return (
    <>
      <section className="overview-hero">
        <div className="overview-hero__copy">
          <span className="ops-eyebrow">Today</span>
          <h2>{attention ? "A few things need you" : "Your day is in good hands"}</h2>
          <p>{message}</p>
          <div className="overview-hero__actions">
            {attention ? <a className="primary-btn" href="#attention">Review handoffs</a> : null}
            {toLabel ? (
              <button className="secondary-btn" type="button" onClick={onOpenLabeler}>
                Review {toLabel} learning example{toLabel === 1 ? "" : "s"}
              </button>
            ) : null}
          </div>
        </div>
        <div className="overview-presence" aria-label="Agent status summary">
          <span className={`overview-presence__pulse ${health?.ok ? "is-live" : "is-offline"}`}>
            <AppIcon name="waypoints" size={24} />
          </span>
          <div>
            <strong>{health?.ok ? `${active} domain${active === 1 ? "" : "s"} in motion` : "System connection interrupted"}</strong>
            <span>{health?.ok ? "Coordination continues in the background" : "Actions are paused until the connection returns"}</span>
          </div>
        </div>
      </section>

      <section className="overview-metrics" aria-label="Workspace summary">
        <Metric label="Needs you" value={attention} detail={attention ? "open handoffs" : "all clear"} tone={attention ? "warning" : "success"} />
        <Metric label="Ready" value={`${ready}/${domains.length || 0}`} detail="connected domains" tone="success" />
        <Metric label="Learning queue" value={toLabel} detail={toLabel ? "waiting for review" : "nothing waiting"} />
        <Metric label="System" value={health?.ok ? "Online" : "Offline"} detail={health?.ok ? "services reachable" : "check connection"} tone={health?.ok ? "success" : "danger"} />
      </section>
    </>
  );
}

export function CommandCenter({ health, onOpenDomain, onOpenLabeler }) {
  const [summary, setSummary] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/command-center/summary").then(setSummary).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [load]);

  const tilesById = useMemo(
    () => summary ? Object.fromEntries((summary.domains || []).map((tile) => [tile.id, tile])) : undefined,
    [summary],
  );

  return (
    <div className="section-body overview-dashboard">
      <OverviewHero summary={summary} health={health} onOpenLabeler={onOpenLabeler} />

      <div className="overview-focus-grid">
        <div id="attention"><AttentionInbox title="Needs your attention" showDomainTag /></div>
        <ActivityFeed items={summary?.activity || []} title="Latest outcomes" showDomain limit={8} />
      </div>

      <section className="overview-domains">
        <div className="section-heading-row">
          <div>
            <span className="ops-eyebrow">Daily life</span>
            <h2>Domains</h2>
            <p>Each space holds the goals, context, and history for one part of your life.</p>
          </div>
        </div>
        <DomainsHub onOpenDomain={onOpenDomain} tilesById={tilesById} compact />
      </section>
    </div>
  );
}
