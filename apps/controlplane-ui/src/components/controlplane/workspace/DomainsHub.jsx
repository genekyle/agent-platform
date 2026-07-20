import { useCallback, useEffect, useState } from "react";
import { AppIcon, DomainIcon } from "../../../ui/Icon";
import { getJSON } from "./api";
import { DOMAIN_CATALOG, PROVIDER_GROUPS } from "./domains";

const STATUS_TEXT = { ready: "Ready", attention: "Needs attention", idle: "Idle" };

function LiveTile({ domain, tile, onOpen }) {
  const status = tile?.status || "idle";
  const attention = tile?.attention_count || 0;
  const primary = tile?.primary;
  const training = tile?.training;

  return (
    <button className={`domain-card domain-card--${status}`} type="button" onClick={() => onOpen(domain.id)}>
      <span className="domain-card__head">
        <span className="domain-card__icon"><DomainIcon id={domain.id} size={21} /></span>
        <span className="domain-card__identity">
          <strong>{domain.label}</strong>
          <small>{domain.blurb}</small>
        </span>
        <AppIcon name="arrowRight" size={17} className="domain-card__arrow" />
      </span>

      <span className="domain-card__summary">
        <span className="domain-card__primary">
          <strong>{primary?.value ?? 0}</strong>
          <small>{primary?.label || "active items"}</small>
        </span>
        <span className="domain-card__signals">
          {(tile?.chips || []).slice(0, 2).map((chip) => <span key={chip.label}><strong>{chip.value}</strong> {chip.label}</span>)}
          {training ? <span><strong>{training.to_label || 0}</strong> to review</span> : null}
        </span>
      </span>

      <span className="domain-card__foot">
        <span className="status-line"><span className={`status-dot ${status === "ready" ? "is-ok" : status === "attention" ? "is-bad" : ""}`} /> {STATUS_TEXT[status]}</span>
        {attention ? <span className="domain-card__attention">{attention} handoff{attention === 1 ? "" : "s"}</span> : <span>No handoffs</span>}
      </span>
    </button>
  );
}

function PlannedItem({ domain }) {
  return (
    <div className="planned-domain">
      <span className="planned-domain__icon"><DomainIcon id={domain.id} size={17} /></span>
      <span><strong>{domain.label}</strong><small>{domain.blurb}</small></span>
      <span className="badge badge--muted">Planned</span>
    </div>
  );
}

export function DomainsHub({ onOpenDomain, tilesById, compact = false }) {
  const [fetched, setFetched] = useState({});

  const load = useCallback(() => {
    if (tilesById) return;
    getJSON("/api/command-center/summary")
      .then((data) => setFetched(Object.fromEntries((data.domains || []).map((tile) => [tile.id, tile]))))
      .catch(() => {});
  }, [tilesById]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [load]);

  const byId = tilesById || fetched;
  const live = DOMAIN_CATALOG.filter((domain) => !domain.provider && !domain.parent && domain.kind !== "coming_soon");
  const planned = DOMAIN_CATALOG.filter((domain) => !domain.provider && !domain.parent && domain.kind === "coming_soon");
  const grouped = PROVIDER_GROUPS.map((group) => ({
    group,
    domains: DOMAIN_CATALOG.filter((domain) => domain.provider === group.id),
  })).filter(({ domains }) => domains.length);

  return (
    <div className={`domains-dashboard ${compact ? "is-compact" : ""}`}>
      <div className="domain-card-grid">
        {live.map((domain) => <LiveTile key={domain.id} domain={domain} tile={byId[domain.id]} onOpen={onOpenDomain} />)}
      </div>

      {grouped.map(({ group, domains }) => (
        <section className="provider-card" key={group.id}>
          <div className="provider-card__identity">
            <span className="provider-card__icon"><DomainIcon id={group.id} size={20} /></span>
            <span><strong>{group.label}</strong><small>{group.blurb}</small></span>
          </div>
          <div className="provider-card__surfaces">
            {domains.map((domain) => <PlannedItem key={domain.id} domain={domain} />)}
            {(group.planned || []).map((label) => <span className="provider-surface" key={label}>{label}<small>planned</small></span>)}
          </div>
        </section>
      ))}

      {planned.length ? (
        <section className="planned-domains">
          <div className="planned-domains__head"><strong>On the horizon</strong><span>New coordinators will inherit the same clear operating pattern.</span></div>
          <div className="planned-domains__list">{planned.map((domain) => <PlannedItem key={domain.id} domain={domain} />)}</div>
        </section>
      ) : null}
    </div>
  );
}
