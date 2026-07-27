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

// A provider member that is actually LIVE. Every member used to render as PlannedItem — a
// non-clickable div badged "Planned" — which was true while Gmail had no workspace and became a
// lie the moment it got one: the domain other domains now depend on was the one you could not open.
// A live member is a button; only the not-yet-built ones stay flat.
function ProviderSurface({ domain, tile, onOpen }) {
  if (domain.kind === "coming_soon") return <PlannedItem domain={domain} />;
  return (
    <button className="planned-domain planned-domain--live" onClick={() => onOpen?.(domain.id)}>
      <span className="planned-domain__icon"><DomainIcon id={domain.id} size={17} /></span>
      <span><strong>{domain.label}</strong><small>{domain.blurb}</small></span>
      {tile?.attention_count
        ? <span className="badge badge--danger">{tile.attention_count} need you</span>
        : <span className="badge badge--muted">Open</span>}
    </button>
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

  const rawById = tilesById || fetched;

  // A GROUP CARD HAS NO TILE OF ITS OWN. The backend deliberately keeps its rollup small and
  // emits one tile per real workspace (`indeed_jobs`), while parent/child nesting is a UI-catalog
  // concern — so "Career Search" matched nothing and rendered the empty-state defaults: "0 active
  // items · Idle", while a live session underneath it had a two-application queue mid-flight
  // (2026-07-25). A card that says Idle over running work is the exact stale surface the cockpit
  // exists to remove. Roll the children up into the parent instead.
  const byId = { ...rawById };
  for (const domain of DOMAIN_CATALOG) {
    if (domain.kind !== "group" || byId[domain.id]) continue;
    const kids = (domain.children || []).map((id) => rawById[id]).filter(Boolean);
    if (!kids.length) continue;
    // Worst status wins: a group is only Idle when every surface under it is.
    const status = kids.some((k) => k.status === "attention") ? "attention"
                 : kids.some((k) => k.status === "ready") ? "ready" : "idle";
    const lead = kids.find((k) => k.primary?.value) || kids[0];
    byId[domain.id] = {
      ...lead,
      id: domain.id,
      status,
      attention_count: kids.reduce((n, k) => n + (k.attention_count || 0), 0),
      training: { to_label: kids.reduce((n, k) => n + (k.training?.to_label || 0), 0),
                  labeled: kids.reduce((n, k) => n + (k.training?.labeled || 0), 0) },
    };
  }

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
            {domains.map((domain) => (
              <ProviderSurface key={domain.id} domain={domain} tile={byId[domain.id]}
                               onOpen={onOpenDomain} />
            ))}
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
