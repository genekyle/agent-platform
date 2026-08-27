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

function OverviewHero({ summary, health, onOpenLabeler, onOpenQueue }) {
  const domains = summary?.domains || [];
  const ready = domains.filter((domain) => domain.status === "ready").length;
  const attention = summary?.attention_open_count ?? 0;
  const toLabel = summary?.flywheel?.to_label_total ?? 0;
  const active = domains.filter((domain) => domain.status && domain.status !== "idle").length;
  // The teacher's OWN queues — distinct from runtime handoffs (the inbox below reads those).
  // Parked drives are the loudest: a drive is literally waiting on an answer.
  const teacherQueue = summary?.teacher?.transition_queue;
  const parksOpen = summary?.teacher?.parks_open;
  // Live sessions whose ladder is holding still for the operator — a pick, an answer, or a Run
  // press. This was the count the hero missed on 2026-08-27: session 34 sat at awaiting:choose
  // with 25 extracted results while this line read "Nothing needs your judgment right now".
  const waiting = summary?.sessions_awaiting || [];
  const needsAnswer = waiting.filter((w) => w.needs === "answer");

  const message = parksOpen
    ? `${parksOpen} drive${parksOpen === 1 ? " is" : "s are"} parked waiting on your answer — that comes first.`
    : needsAnswer.length
      ? `${needsAnswer.length} session${needsAnswer.length === 1 ? " is" : "s are"} waiting on you — ${needsAnswer[0].detail}.`
      : waiting.length
        ? `${waiting.length} session${waiting.length === 1 ? " is" : "s are"} idling mid-work — a Run press continues ${waiting.length === 1 ? "it" : "them"}.`
        : attention
          ? `${attention} handoff${attention === 1 ? "" : "s"} need your judgment. Everything else can keep moving.`
          : active
            ? "Your agents are moving. Nothing needs your judgment right now."
            : "Your workspace is quiet. Start with a domain when you are ready.";

  return (
    <>
      <section className="overview-hero">
        <div className="overview-hero__copy">
          <span className="ops-eyebrow">Today</span>
          <h2>{attention || needsAnswer.length ? "A few things need you" : "Your day is in good hands"}</h2>
          <p>{message}</p>
          <div className="overview-hero__actions">
            {attention ? <a className="primary-btn" href="#attention">Review handoffs</a> : null}
            {teacherQueue ? (
              <button className="secondary-btn" type="button" onClick={onOpenQueue}>
                Answer {teacherQueue} queued transition{teacherQueue === 1 ? "" : "s"}
              </button>
            ) : null}
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
        <Metric label="Needs you" value={attention + needsAnswer.length}
          detail={needsAnswer.length ? needsAnswer[0].detail
                  : attention ? "open handoffs" : "all clear"}
          tone={attention + needsAnswer.length ? "warning" : "success"} />
        <Metric label="Sessions waiting" value={waiting.length}
          detail={waiting.length ? (needsAnswer.length ? "a pick or answer is due" : "resumable — press Run")
                                 : "none holding still"}
          tone={needsAnswer.length ? "warning" : waiting.length ? "neutral" : "success"} />
        {/* A null here is "the read failed", not "all clear" — the API sends None for a
            broken read on purpose; painting it success-green would be the tile lying. */}
        <Metric label="Parked drives" value={parksOpen ?? "—"}
          detail={parksOpen == null ? "could not read" : parksOpen ? "waiting on a teacher answer" : "no drive is waiting"}
          tone={parksOpen == null ? "neutral" : parksOpen ? "warning" : "success"} />
        <Metric label="Teacher queue" value={teacherQueue ?? "—"}
          detail={teacherQueue == null ? "could not read" : teacherQueue ? "transitions to label" : "queue is clear"}
          tone={teacherQueue == null ? "neutral" : teacherQueue ? "warning" : "success"} />
        <Metric label="Ready" value={`${ready}/${domains.length || 0}`} detail="connected domains" tone="success" />
        <Metric label="Learning queue" value={toLabel} detail={toLabel ? "waiting for review" : "nothing waiting"} />
        <Metric label="System" value={health?.ok ? "Online" : "Offline"} detail={health?.ok ? "services reachable" : "check connection"} tone={health?.ok ? "success" : "danger"} />
      </section>
    </>
  );
}

export function CommandCenter({ health, onOpenDomain, onOpenLabeler, onOpenQueue }) {
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
      <OverviewHero summary={summary} health={health} onOpenLabeler={onOpenLabeler} onOpenQueue={onOpenQueue} />

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
