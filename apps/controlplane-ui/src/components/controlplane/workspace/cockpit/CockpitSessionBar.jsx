import { AppIcon } from "../../../../ui/Icon";
import { DOMAINS_BY_ID } from "../domains";

const STATE_COPY = {
  live: { label: "Live", tone: "ready" },
  starting: { label: "Starting", tone: "warn" },
  degraded: { label: "Browser degraded", tone: "warn" },
  stale: { label: "Not answering", tone: "warn" },
  closed: { label: "Closed", tone: "muted" },
  orphaned: { label: "Orphaned browser", tone: "danger" },
};

function stateOf(session) {
  if (session?.operational_state) return session.operational_state;
  if (session?.live && session?.status !== "active" && session?.status !== "starting") return "orphaned";
  if (session?.live) return "live";
  if (session?.status === "starting") return "starting";
  if (session?.status === "active") return "stale";
  return "closed";
}

function shortDomain(domainId) {
  return DOMAINS_BY_ID[domainId]?.short || DOMAINS_BY_ID[domainId]?.label || domainId || "Session";
}

function optionLabel(session) {
  const state = stateOf(session);
  return `#${session.id} · ${shortDomain(session.domain_id)} · ${STATE_COPY[state]?.label || state}`;
}

export function CockpitSessionBar({ session, siblings, onChooseSession }) {
  const state = stateOf(session);
  const stateCopy = STATE_COPY[state] || { label: state, tone: "muted" };
  const live = siblings.filter((s) => stateOf(s) === "live");
  const unavailable = siblings.filter((s) => stateOf(s) !== "live" && stateOf(s) !== "closed");
  const history = siblings.filter((s) => stateOf(s) === "closed");

  const options = (rows) => rows.map((s) => (
    <option key={s.id} value={s.id}>{optionLabel(s)}</option>
  ));

  return (
    <div className="cockpit-session-bar">
      <span className="cockpit-session-bar__mark"><AppIcon name="sliders" size={16} /></span>
      <div className="cockpit-session-bar__identity">
        <strong>Session #{session.id}</strong>
        <span>{session.account_label || shortDomain(session.domain_id)}</span>
      </div>

      <span className={`badge badge--${stateCopy.tone}`}>{stateCopy.label}</span>
      {session.protected && <span className="badge badge--muted">protected</span>}
      <span className="cockpit-session-bar__spacer" />

      <span className="cockpit-session-bar__count">
        {live.length} live · {history.length} closed
      </span>
      {siblings.length > 1 && (
        <label className="cockpit-session-bar__picker">
          <span>Switch session</span>
          <select aria-label="session" value={session.id}
                  onChange={(e) => onChooseSession(Number(e.target.value))}>
            {live.length > 0 && <optgroup label="Live sessions">{options(live)}</optgroup>}
            {unavailable.length > 0 && <optgroup label="Needs attention">{options(unavailable)}</optgroup>}
            {history.length > 0 && <optgroup label="History">{options(history)}</optgroup>}
          </select>
        </label>
      )}
    </div>
  );
}
