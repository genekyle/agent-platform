import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { postJSON } from "../api";
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

export function CockpitSessionBar({ session, siblings, onChooseSession, onStartFresh,
                                   startingFresh, onProtectedChange }) {
  // THE PROTECT SWITCH — the badge said "protected" and nothing could act on it (2026-08-18).
  // `protected` means human-owned, and every disruptive verb honours it: close-out closes the
  // searches and keeps the work but reports "Chrome NOT stopped — refusing without force=true".
  // That refusal is correct and deliberately not forceable from the close-out; releasing the
  // session is a SEPARATE, named decision. But the endpoint existed with no press, so the only
  // way through was a curl — the parity rule's exact failure. The switch belongs here, beside
  // the badge that states the condition.
  const [protectBusy, setProtectBusy] = useState(false);
  const [protectErr, setProtectErr] = useState("");
  const toggleProtected = async () => {
    setProtectBusy(true);
    setProtectErr("");
    try {
      await postJSON(`/api/sessions/${session.id}/protect`, { protected: !session.protected });
      await onProtectedChange?.();
    } catch (e) {
      setProtectErr(e.message || "could not change protection");
    } finally {
      setProtectBusy(false);
    }
  };

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
      <button type="button" className="badge badge--muted cockpit-session-bar__protect"
              disabled={protectBusy}
              aria-pressed={!!session.protected}
              aria-label={session.protected
                ? `Release session ${session.id} — allow it to be stopped`
                : `Protect session ${session.id} — refuse automated stop and reap`}
              title={session.protected
                ? "Human-owned: stop, reap and reset refuse while this is on. Release it to close the session down."
                : "Mark human-owned so automated stop / reap / reset refuse to touch this session."}
              onClick={toggleProtected}>
        {protectBusy ? "…" : session.protected ? "protected" : "unprotected"}
      </button>
      {protectErr && <span className="badge badge--danger">{protectErr}</span>}
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

      {/* START FRESH, always. The switcher can only ever offer sessions that already exist, so
          while one was live the operator's entire menu was "keep working this one" or "open a
          dead one" — and a session that has drifted is exactly when a new one is the cheap
          answer. Starting fresh is a first-class verb of the cockpit, not a fallback screen it
          shows when nothing is running. */}
      {onStartFresh && (
        <button type="button" className={`btn btn-sm ${startingFresh ? "" : "btn-ghost"}`}
                aria-expanded={!!startingFresh} aria-label="Start a fresh session"
                title="Provision a new browser on a domain's saved sign-in. A live session holding that domain is retired first — its work is kept."
                onClick={onStartFresh}>
          {startingFresh ? "Cancel" : "Start fresh"}
        </button>
      )}
    </div>
  );
}
