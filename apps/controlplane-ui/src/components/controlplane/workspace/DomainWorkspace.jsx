import { useCallback, useEffect, useState } from "react";
import { getJSON, putJSON } from "./api";
import { StatusCard } from "./StatusCard";
import { AutomationMode } from "./AutomationMode";
import { GoalsPanel } from "./GoalsPanel";
import { TasksPanel } from "./TasksPanel";
import { AttentionInbox } from "./AttentionInbox";
import { LiveDrivePanel } from "./LiveDrivePanel";
import { SessionControlPanel } from "./SessionControlPanel";
import { ActivityFeed } from "./ActivityFeed";
import { TrainingReadiness } from "./TrainingReadiness";
import { AccountsPanel } from "./AccountsPanel";
import { FacebookMarketplaceSection } from "../FacebookMarketplaceSection";
import { JobsWorkspaceSection } from "../JobsWorkspaceSection";
import { AccountsSection } from "../AccountsSection";
import { EventsConsole } from "../EventsConsole";
import { DOMAINS_BY_ID } from "./domains";
import { AppIcon, DomainIcon } from "../../../ui/Icon";

// The shared five-layer workspace shell. Every domain gets the SAME operating pattern — a
// computed Status + one action, an Automation mode, and an Overview cockpit (Attention · Goals ·
// Tasks · Activity) — while the extra tabs render that domain's own rich data views (inventory
// tables, jobs hub) untouched.

// Which existing section component + `section` prop each data tab maps to. The Overview tab is
// the shared cockpit and is handled here directly. Every jobs aggregator shares ONE mapping —
// the tabs are the same questions, only the domain they're scoped to differs.
const JOBS_TABS = { jobs: "jobs-dashboard", profile: "application-answers", "apply-state": "apply-state" };
const TAB_TO_SECTION = {
  facebook_marketplace: { inventory: "inventory", queue: "queue", listings: "listings", messages: "messages" },
};

function DataTab({ domain, tab, onOpenTraining }) {
  if (tab === "control") return <SessionControlPanel domain={domain} />;
  if (tab === "live") return <LiveDrivePanel domain={domain} />;
  if (tab === "training") return <TrainingReadiness domain={domain} onOpenTraining={onOpenTraining} />;
  if (tab === "accounts") {
    // WHICH accounts a domain has is a property OF the domain, declared in the catalog — an ATS
    // sub-domain has one login per employer (`accounts: "ats"`), an aggregator or channel has a
    // single login of its own. Inferring it from `parent === "career_search"` sent Indeed to the
    // company-first ATS panel filtered on an ats_id no account ever carries, so the engine that
    // most needs a sign-in had nowhere to type one.
    if (domain.accounts === "ats") return <AccountsSection atsFilter={domain.id} />;
    return <AccountsPanel domain={domain} />;
  }
  if (domain.kind === "jobs") return <JobsWorkspaceSection domain={domain} section={JOBS_TABS[tab]} />;
  const section = TAB_TO_SECTION[domain.id]?.[tab];
  if (domain.id === "facebook_marketplace") return <FacebookMarketplaceSection section={section} />;
  return <div className="empty-hint">Nothing here yet.</div>;
}

function ComingSoon({ domain }) {
  return (
    <div className="layer">
      <div className="layer__head"><div className="layer__title layer__title--with-icon"><DomainIcon id={domain.id} size={17} /> {domain.label}</div></div>
      <p className="status-card__reason" style={{ maxWidth: "70ch" }}>{domain.responsibility}</p>
      <p className="mode-hint" style={{ marginTop: 10 }}>
        This domain is scaffolded in the cockpit so the operating pattern is consistent, but it isn't wired
        to a backend yet. It reuses the same Status · Goals · Tasks · Attention · Activity layers once connected.
      </p>
    </div>
  );
}

function Overview({ domain, mode, goalState, onToggleGoal }) {
  const [activity, setActivity] = useState([]);

  const loadActivity = useCallback(() => {
    if (domain.kind === "selling") {
      getJSON("/api/inventory/log?limit=20")
        .then((d) => setActivity((d.log || []).map((e) => ({ ...e, ts: e.timestamp }))))
        .catch(() => {});
    } else if (domain.kind === "jobs") {
      getJSON("/api/runtime/handoffs?limit=20")
        .then((d) => setActivity((d.handoffs || []).map((h) => ({
          ts: h.ts, message: h.why || h.detail, status: h.status === "resolved" ? "ok" : "error", kind: "handoff",
        }))))
        .catch(() => {});
    }
  }, [domain.kind]);
  useEffect(() => { loadActivity(); const t = setInterval(loadActivity, 10000); return () => clearInterval(t); }, [loadActivity]);

  if (domain.kind === "coming_soon") return <ComingSoon domain={domain} />;

  return (
    <div className="cockpit">
      <AttentionInbox host={domain.host} />
      <div className="cockpit-grid">
        <GoalsPanel domain={domain} mode={mode} goalState={goalState} onToggleGoal={onToggleGoal} />
        <TasksPanel domain={domain} mode={mode} />
      </div>
      <ActivityFeed items={activity} title="Recent activity" />
    </div>
  );
}

// A group/parent domain (Career Search): no driven surface of its own — it lists its sub-domains and
// holds the shared Accounts. Rendered instead of the Status/Automation/cockpit shell.
function GroupWorkspace({ domain, activeTab, onChangeTab, onOpenDomain }) {
  const tabs = domain.tabs || [{ id: "overview", label: "Sub-domains" }];
  const tab = tabs.find((t) => t.id === activeTab) ? activeTab : "overview";
  const children = (domain.children || []).map((id) => DOMAINS_BY_ID[id]).filter(Boolean);
  return (
    <div className="section-body">
      {tabs.length > 1 && (
        <div className="workspace-tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`workspace-tab ${t.id === tab ? "is-active" : ""}`} onClick={() => onChangeTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      )}
      {tab === "accounts" ? <AccountsSection /> : tab === "activity" ? <EventsConsole /> : (
        <div className="domain-tiles">
          {children.map((c) => {
            const soon = c.kind === "coming_soon";
            return (
              <button key={c.id} className={`domain-tile ${soon ? "domain-tile--soon" : ""}`}
                disabled={soon} onClick={() => !soon && onOpenDomain?.(c.id)}>
                <div className="domain-tile__head">
                  <span className="domain-tile__icon"><DomainIcon id={c.id} size={20} /></span>
                  <div style={{ minWidth: 0 }}>
                    <div className="domain-tile__title">{c.label}</div>
                    <div className="domain-tile__blurb">{c.blurb}</div>
                  </div>
                </div>
                {soon && <div style={{ marginTop: 4 }}><span className="badge badge--muted">Coming soon</span></div>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function DomainWorkspace({ domain, activeTab, onChangeTab, onOpenTraining, onOpenDomain }) {
  const [settings, setSettings] = useState({ automation_mode: "manual", goals: {} });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getJSON(`/api/domains/${domain.id}/settings`).then(setSettings).catch(() => {});
  }, [domain.id]);

  const patch = useCallback(async (body) => {
    setSaving(true);
    try {
      const next = await putJSON(`/api/domains/${domain.id}/settings`, body);
      setSettings(next);
    } catch {
      /* best-effort */
    } finally {
      setSaving(false);
    }
  }, [domain.id]);

  const onChangeMode = (mode) => patch({ automation_mode: mode });
  const onToggleGoal = (goalId, enabled) => patch({ goals: { [goalId]: enabled } });

  // Group/parent domains (Career Search) render their own sub-domain + Accounts view (hooks above
  // still run unconditionally — this branch is after them, so rules-of-hooks holds).
  if (domain.kind === "group") {
    return <GroupWorkspace domain={domain} activeTab={activeTab} onChangeTab={onChangeTab} onOpenDomain={onOpenDomain} />;
  }

  const tabs = domain.tabs || [{ id: "overview", label: "Overview" }];
  const tab = tabs.find((t) => t.id === activeTab) ? activeTab : "overview";

  return (
    <div className="section-body">
      <div className="workspace-head">
        <StatusCard domain={domain} />
        {domain.kind !== "coming_soon" && (
          <div className="layer" style={{ padding: "14px 18px" }}>
            <div className="layer__head" style={{ marginBottom: 8 }}>
              <div className="layer__title layer__title--with-icon"><AppIcon name="sliders" size={17} /> Automation mode</div>
              <span className="layer__sub">How autonomous this domain is</span>
            </div>
            <AutomationMode mode={settings.automation_mode} onChange={onChangeMode} saving={saving} />
          </div>
        )}
      </div>

      {tabs.length > 1 && (
        <div className="workspace-tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`workspace-tab ${t.id === tab ? "is-active" : ""}`} onClick={() => onChangeTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      )}

      {tab === "overview"
        ? <Overview domain={domain} mode={settings.automation_mode} goalState={settings.goals || {}} onToggleGoal={onToggleGoal} />
        : <DataTab domain={domain} tab={tab} onOpenTraining={onOpenTraining} />}
    </div>
  );
}
