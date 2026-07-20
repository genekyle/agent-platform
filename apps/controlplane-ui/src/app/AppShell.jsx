import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { AppIcon, DomainIcon } from "../ui/Icon";

const GLOBAL_NAV = [
  { to: "/overview", label: "Overview", icon: "overview", match: (p) => p === "/" || p.startsWith("/overview") },
  { to: "/domains", label: "Domains", icon: "domains", match: (p) => p.startsWith("/domains") },
  { to: "/activity", label: "Activity", icon: "activity", match: (p) => p.startsWith("/activity") },
  { to: "/learning/overview", label: "Learning", icon: "learning", match: (p) => p.startsWith("/learning") },
  { to: "/system/status", label: "System", icon: "settings", match: (p) => p.startsWith("/system") },
];

function GlobalNav({ onNavigate }) {
  const location = useLocation();
  return (
    <nav className="global-nav" aria-label="Global navigation">
      <div className="global-nav__label">Workspace</div>
      {GLOBAL_NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={`global-nav__item ${item.match(location.pathname) ? "is-active" : ""}`}
          onClick={onNavigate}
        >
          <AppIcon name={item.icon} />
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell({
  title,
  subtitle,
  domain,
  health,
  driveLock,
  onRefresh,
  onReleaseDriveLock,
  children,
}) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className={`app-shell ai-ops-shell ${mobileOpen ? "nav-open" : ""}`}>
      <button
        type="button"
        className="nav-scrim"
        aria-label="Close navigation"
        onClick={() => setMobileOpen(false)}
      />

      <aside className="global-sidebar">
        <div className="ai-brand">
          <span className="ai-brand__mark"><AppIcon name="waypoints" size={20} /></span>
          <span>
            <span className="ai-brand__title">AI Ops</span>
            <span className="ai-brand__subtitle">Your agents, coordinated</span>
          </span>
        </div>

        <GlobalNav onNavigate={() => setMobileOpen(false)} />

        <div className="global-sidebar__footer">
          <span className={`status-dot ${health.loading ? "is-loading" : health.ok ? "is-ok" : "is-bad"}`} />
          <span>{health.loading ? "Checking system" : health.ok ? "System connected" : "System unavailable"}</span>
        </div>
      </aside>

      <main className="main-panel ai-main-panel">
        <header className="ai-topbar">
          <button
            type="button"
            className="icon-btn mobile-nav-toggle"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
          >
            <AppIcon name="menu" />
          </button>

          <div className="ai-page-heading">
            {domain ? (
              <div className="ai-breadcrumbs">
                <NavLink to="/domains">Domains</NavLink>
                <AppIcon name="chevronRight" size={14} />
                <span>{domain.label}</span>
              </div>
            ) : null}
            <div className="ai-page-title-row">
              {domain ? <span className="page-domain-icon"><DomainIcon id={domain.id} size={22} /></span> : null}
              <h1 className="page-title">{title}</h1>
            </div>
            {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
          </div>

          <div className="ai-topbar__actions">
            <button type="button" className="icon-btn" aria-label="Refresh page data" title="Refresh" onClick={onRefresh}>
              <AppIcon name="refresh" className={health.loading ? "spin" : ""} />
            </button>
            <div className={`global-health ${health.loading ? "is-loading" : health.ok ? "is-ok" : "is-bad"}`}>
              <span className="status-dot" />
              <span>{health.loading ? "Checking" : health.ok ? "Connected" : "Offline"}</span>
            </div>
          </div>
        </header>

        {driveLock.locked ? (
          <div className="drive-lock-banner" role="status" aria-live="polite">
            <AppIcon name="lock" />
            <span className="drive-lock-banner__msg">
              <strong>Agent is using the browser</strong>
              {driveLock.holder ? <span> · {driveLock.holder}</span> : null}
              {driveLock.reason ? <span> · {driveLock.reason}</span> : null}
              <span className="drive-lock-banner__hint"> Type here until the browser is released.</span>
            </span>
            <button className="drive-lock-banner__release" onClick={onReleaseDriveLock}>Release browser</button>
          </div>
        ) : null}

        <div className="workspace-content ai-workspace-content">{children}</div>
      </main>
    </div>
  );
}
