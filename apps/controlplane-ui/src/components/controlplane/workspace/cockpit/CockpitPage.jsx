import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { getJSON } from "../api";
import { AppIcon } from "../../../../ui/Icon";
import { SessionCockpit } from "./SessionCockpit";
import { CockpitSessionBar } from "./CockpitSessionBar";
import { ObserverLens } from "./ObserverLens";
import { SessionTrace } from "./SessionTrace";
import StartSession from "./StartSession";

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
// them in one picker, and gives the session its own focused views. The URL is the choice:
//
//   /cockpit                → follow the live session, whoever's it is
//   /cockpit?domain=x       → follow domain x's live session (the domain workspaces link here)
//   /cockpit/:id            → pinned to one session
//   /cockpit/:id/lens       → the local observer's view
//   /cockpit/:id/trace      → that session's decision/action/window record
//
// The first useful cockpit is intentionally small:
//   Now    — operate the current moment, with compact path/lens/reason previews
//   Lens   — what local perception actually received and concluded
//   Trace  — observation → decision → action, plus the browser/window record

//: How often the page re-checks which sessions exist. Sessions are provisioned and retired on
//: human timescales; this is not the live loop's fast eye.
const SESSIONS_MS = 10000;

const COCKPIT_TABS = [
  { id: "now", label: "Now" },
  { id: "lens", label: "Lens" },
  { id: "trace", label: "Trace" },
];

const TAB_ALIASES = { live: "now", journal: "trace" };

export function CockpitPage({ routeSessionId, routeTab }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const domainHint = searchParams.get("domain");
  const [sessions, setSessions] = useState(null); // null = not loaded yet; [] = loaded, none
  // Open teacher parks, globally — each names its session, so the chooser below can say
  // "something is waiting for you HERE" instead of making the operator open cockpits to find out.
  const [parks, setParks] = useState([]);

  useEffect(() => {
    const poll = () => {
      getJSON("/api/sessions")
        .then((d) => setSessions(d.sessions || []))
        .catch(() => setSessions((prev) => prev ?? []));
      getJSON("/api/controller/teacher/pending")
        .then((d) => setParks(d.pending || []))
        .catch(() => {});
    };
    poll();
    const t = setInterval(poll, SESSIONS_MS);
    return () => clearInterval(t);
  }, []);

  const requestedTab = TAB_ALIASES[routeTab] || routeTab;
  const tab = COCKPIT_TABS.some((t) => t.id === requestedTab) ? requestedTab : "now";
  const pathFor = (id, tabId = tab) =>
    `/cockpit/${id}${tabId !== "now" ? `/${tabId}` : ""}`;

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

  // THE ARRIVAL RULE: with several live sessions and no explicit choice, don't guess — the old
  // "follow the live session, whoever's it is" resolved to the NEWEST live session, which is how
  // the sidebar link kept landing on an idle LinkedIn browser while the Indeed session held a
  // parked application (2026-08-10). One live session follows silently; more than one asks.
  const liveSessions = sessions.filter((x) => x.status === "active" && x.live);
  if (!pinned && !hinted && liveSessions.length > 1) {
    const parksBySession = new Map();
    for (const pk of parks) {
      const k = String(pk.session_id ?? "");
      parksBySession.set(k, (parksBySession.get(k) || 0) + 1);
    }
    return (
      <div className="cockpit-page">
        <div className="cockpit-sessions">
          <div className="cockpit-sessions__head">
            <AppIcon name="sliders" size={17} /> <strong>Live sessions</strong>
            <span>{liveSessions.length} browsers are open — pick the one to operate.</span>
          </div>
          {liveSessions.map((s) => {
            const waiting = parksBySession.get(String(s.id)) || 0;
            return (
              <button key={s.id} type="button" className="cockpit-sessions__row"
                      onClick={() => navigate(pathFor(s.id))}>
                <strong>#{s.id}</strong>
                <span className="cockpit-sessions__label">{s.account_label || s.domain_id}</span>
                <span className="badge badge--ready">live</span>
                {waiting > 0 && (
                  <span className="badge badge--warn">
                    {waiting} waiting on you
                  </span>
                )}
                <span className="cockpit-sessions__meta">
                  {s.tab_count} tab{s.tab_count === 1 ? "" : "s"}
                  {s.started_at ? ` · since ${new Date(s.started_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // NO LIVE SESSION AND NO EXPLICIT CHOICE: the moment asks "start one", not "here is a dead
  // session's setup form". The old fallback picked pool[0] — an arbitrary CLOSED session — and
  // rendered its declare surface over a browser that no longer exists (2026-08-10, right after
  // the first full cleanup). Closed sessions stay reachable by pinning (/cockpit/:id).
  if (!pinned && !hinted && liveSessions.length === 0) {
    return (
      <div className="cockpit-page">
        <StartSession onStarted={(id) => navigate(pathFor(id))} />
        {sessions.length > 0 && (
          <p className="empty-hint">
            {sessions.length} closed session{sessions.length === 1 ? "" : "s"} on record — pick
            one from the switcher after starting, or open <code>/cockpit/&lt;id&gt;</code> to
            read its trace.
          </p>
        )}
      </div>
    );
  }

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

      <CockpitSessionBar session={active} siblings={sessions}
                         onChooseSession={(id) => navigate(pathFor(id))} />

      {tab === "trace" ? (
        <SessionTrace key={active.id} sessionId={active.id} />
      ) : tab === "lens" ? (
        <ObserverLens key={active.id} sessionId={active.id} />
      ) : (
        <SessionCockpit
          // THE KEY IS THE ANTI-CLOBBER. A different session is a different cockpit: React
          // unmounts the old one and every piece of per-session state dies with it instead of
          // leaking into the next session's story.
          key={active.id}
          sessionId={active.id}
          // The open teacher parks, answerable in place (TeacherParks) — a parked drive is the
          // current moment's real question, and counting questions is not a seat.
          parks={parks}
          onOpenLens={() => navigate(pathFor(active.id, "lens"))}
          onOpenTrace={() => navigate(pathFor(active.id, "trace"))}
        />
      )}
    </div>
  );
}
