import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getJSON } from "../api";
import { AppIcon } from "../../../../ui/Icon";
import { SessionCockpit } from "./SessionCockpit";
import { SessionJournal } from "./SessionJournal";

// THE COCKPIT PAGE — the session cockpit as a first-class destination.
//
// The cockpit used to live as one tab inside a domain workspace, which was wrong twice over. The
// practical fault: it rendered below a breadcrumb, a hero header, a sign-in card, an automation
// card and a ten-tab row, so the operating surface for a LIVE DRIVE started a full screen down and
// read as a "lite" embed of itself (operator, 2026-08-05). The conceptual fault is older and
// documented: a session is one focused Chrome working ONE TASK, not a property of a domain — a
// Career-Search session takes a Gmail errand INSIDE itself, which is exactly why domain cannot be
// the boundary (PLAN_session_control_panel §1). Reaching a session THROUGH a domain was the wrong
// door even when it worked.
//
// So: /cockpit is session-first. It resolves the active session across EVERY domain, offers all of
// them in one picker, and gives the three panes the full page. The URL is the choice:
//
//   /cockpit                → follow the live session, whoever's it is
//   /cockpit?domain=x       → follow domain x's live session (the domain workspaces link here)
//   /cockpit/:id            → pinned to one session
//   /cockpit/:id/journal    → that session's window record
//
// The page carries the cockpit's OWN tabs — room the domain tab row never had:
//   Live     — the three panes (rail · work surface · inspector)
//   Journal  — the window census + timeline: what the session's browser actually did

//: How often the page re-checks which sessions exist. Sessions are provisioned and retired on
//: human timescales; this is not the live loop's fast eye.
const SESSIONS_MS = 10000;

const COCKPIT_TABS = [
  { id: "live", label: "Live" },
  { id: "journal", label: "Journal" },
];

export function CockpitPage({ routeSessionId, routeTab }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const domainHint = searchParams.get("domain");
  const [sessions, setSessions] = useState(null); // null = not loaded yet; [] = loaded, none

  useEffect(() => {
    const poll = () => getJSON("/api/sessions")
      .then((d) => setSessions(d.sessions || []))
      .catch(() => setSessions((prev) => prev ?? []));
    poll();
    const t = setInterval(poll, SESSIONS_MS);
    return () => clearInterval(t);
  }, []);

  const tab = COCKPIT_TABS.some((t) => t.id === routeTab) ? routeTab : "live";
  const pathFor = (id, tabId = tab) =>
    `/cockpit/${id}${tabId !== "live" ? `/${tabId}` : ""}`;

  if (sessions === null) {
    return <p className="empty-hint">Finding this machine's sessions…</p>;
  }

  // WHICH session the page narrates, in order of explicitness:
  //   1. the URL's session id — the operator pinned it;
  //   2. the ?domain hint's active session — a domain workspace sent us here;
  //   3. the active live session, whoever's it is.
  // "Active and answering" beats merely answering: stopped sessions share the active one's debug
  // port, so the port probe alone claims live for all of them.
  const activeLive = (pool) => pool.find((x) => x.status === "active" && x.live)
    || pool.find((x) => x.status === "active") || pool.find((x) => x.live) || pool[0] || null;

  const pinned = routeSessionId ? sessions.find((x) => x.id === routeSessionId) : null;
  const hinted = !pinned && domainHint
    ? activeLive(sessions.filter((x) => x.domain_id === domainHint)) : null;
  const active = pinned || hinted || activeLive(sessions);

  if (!active) {
    return (
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="sliders" size={17} /> Cockpit
          </div>
        </div>
        <p className="empty-hint">
          {routeSessionId
            ? `Session #${routeSessionId} isn't in the session list — it may have been removed.`
            : "No sessions on this machine yet. A session is one focused browser working one task."}
          {" "}
          <Link to="/learning/session-capture">Start one from Sessions</Link>, then come back.
        </p>
      </div>
    );
  }

  return (
    <div className="cockpit-page">
      {/* The cockpit's own tabs. This is the room the domain tab row could not give it: the page
          belongs to the session, so its tabs can be the session's own views. */}
      <div className="workspace-tabs cockpit-page__tabs">
        {COCKPIT_TABS.map((t) => (
          <button key={t.id} type="button"
                  className={`workspace-tab ${t.id === tab ? "is-active" : ""}`}
                  onClick={() => navigate(pathFor(active.id, t.id))}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "journal" ? (
        <>
          {/* The journal still says WHOSE record it is; the full bar belongs to Live. */}
          <div className="cockpit-bar">
            <AppIcon name="sliders" size={16} />
            <span className="badge badge--muted">#{active.id}</span>
            <span className="cockpit-bar__sub">{active.account_label || active.domain_id}</span>
            {active.status !== "active" && <span className="badge badge--warn">{active.status}</span>}
          </div>
          <SessionJournal key={active.id} sessionId={active.id} />
        </>
      ) : (
        <SessionCockpit
          // THE KEY IS THE ANTI-CLOBBER. A different session is a different cockpit: React
          // unmounts the old one and every piece of per-session state dies with it instead of
          // leaking into the next session's story.
          key={active.id}
          sessionId={active.id}
          sessionMeta={active}
          siblings={sessions}
          // Choosing PINS: the URL is the choice, so a picked session survives reload and can be
          // linked. /cockpit with no id keeps meaning "follow the live one".
          onChooseSession={(id) => navigate(pathFor(id))}
        />
      )}
    </div>
  );
}
