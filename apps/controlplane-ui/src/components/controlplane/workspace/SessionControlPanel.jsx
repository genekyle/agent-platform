import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getJSON, postJSON } from "./api";
import { AppIcon } from "../../../ui/Icon";
import { useOrderedPicks } from "./useOrderedPicks";
import { deriveCockpit } from "./cockpit/lifecycle";
import { SessionRail } from "./cockpit/SessionRail";
import { WorkSurface } from "./cockpit/WorkSurface";
import { DecisionInspector } from "./cockpit/DecisionInspector";
import "./cockpit/cockpit.css";

// THE SESSION COCKPIT — the composition root, and nothing else.
//
// Three panes, one authoritative workflow state:
//
//   rail                work surface                     inspector
//   where we are        what needs attention NOW         why this action, why this state
//
// This file owns DATA and SELECTION only. It does not decide what a phase is, which control belongs
// on screen, or which action is primary — all of that is derived once, in `cockpit/lifecycle.js`,
// and read by all three panes. That separation is the whole redesign: the panel it replaces was
// 1,289 lines in which twelve conditional cards each answered "is this my moment?" for themselves,
// so six surfaces could claim the operator's attention at once and none of them was labelled as the
// real one (operator, 2026-08-05: "too many buttons to press and too much information and to a
// point where we don't even know what's going on").
//
// The rules that keep it from growing back, in the order they are most likely to be broken:
//   1. ONE primary action on screen. Asserted at runtime in dev (see below), not just intended.
//   2. A capability becomes a FOCUS KIND or an INSPECTOR ROW. Never a new top-level card.
//   3. The rail never acts. Selection only — a second place that can act is a second question.
//   4. Say a fact once. If it is in the session bar or the inspector, it is not in the work surface.
//
// Load-bearing behaviour carried over unchanged:
//   * The QUERY is spent once. A session runs ONE query — re-searching is what makes the board
//     collapse results — so the field locks once held and the server refuses a different one.
//   * A CONSUMING rung that lapsed RECOVERS, never repeats.
//   * Picks carry an ORDER, and they are not saved until taken.

//: How often the panel re-reads the session. Every call is a local CDP socket (tabs + auth state),
//: so this costs no bandwidth — the number is about how quickly the operator should see the world
//: change, not about load.
const PING_MS = 5000;
//: How often the interval wakes, and how long a press keeps it reading at that speed. The panel
//: reads at PING_MS at rest and at SETTLE_MS while the page is settling from something we did.
const SETTLE_MS = 1000;
const SETTLE_WINDOW_MS = 12000;

export function SessionControlPanel({ domain }) {
  const [sessionId, setSessionId] = useState(null);
  const [panel, setPanel] = useState(null);
  const [form, setForm] = useState({ query: "", location: "", radius_miles: 50 });
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // What the inspector is explaining. `null` follows the focus, which is what the operator sees
  // first and returns to; a rail click pins it to a phase, a rung or an application.
  const [selection, setSelection] = useState(null);
  // Which phase's controls the work surface is showing. `null` follows the session. Set only by an
  // explicit detour, and always labelled as one.
  const [viewPhase, setViewPhase] = useState(null);

  // Picks carry an ORDER, not just a tick — that order is the order the applications run. Scoped to
  // (session, page): the draft survives navigating away from the picker, and turning the page
  // starts a fresh one rather than inheriting the last page's choices.
  const { picks, armed, pick, clear: clearPicks, retain: retainPicks } =
    useOrderedPicks(sessionId ? `${sessionId}.${panel?.page ?? 1}` : null);

  // A ref, not the state, because the interval closes over its first render.
  const busyRef = useRef(false);
  busyRef.current = busy;
  const lastLoadRef = useRef(0);
  const settleUntilRef = useRef(0);

  // Find this domain's live session. Everything below hangs off one session id.
  useEffect(() => {
    getJSON("/api/sessions")
      .then((d) => {
        const s = (d.sessions || []).find((x) => x.domain_id === domain.id && x.live)
          || (d.sessions || []).find((x) => x.domain_id === domain.id);
        setSessionId(s?.id ?? null);
      })
      .catch(() => setSessionId(null));
  }, [domain.id]);

  // The panel PINGS the session rather than only reading it when you press something. The world
  // moves underneath us — you sign in by hand, a challenge appears, a tab closes — and a cockpit
  // that only refreshes on click shows a past that has stopped being true. The read model is all
  // local CDP sockets, so this is free even in low-data mode.
  const load = useCallback(() => {
    if (!sessionId) return;
    lastLoadRef.current = Date.now();
    getJSON(`/api/session_control/${sessionId}`)
      .then((d) => {
        // A poll carries no last_step; keep the one the operator is still reading rather than
        // blanking the result of the action they just took.
        setPanel((prev) => ({ ...d, last_step: d.last_step ?? prev?.last_step ?? null }));
        // Sync the form ONCE, when a declared query first arrives. Re-syncing on every ping would
        // overwrite what the operator is mid-way through typing.
        setForm((f) => (d.query && !f.query
          ? { query: d.query, location: d.location || "", radius_miles: d.radius_miles || 50 }
          : f));
        setError("");
      })
      .catch((e) => setError(e.message || "could not read the session"));
  }, [sessionId]);

  useEffect(() => {
    load();
    // Two cadences, because the world moves at two speeds. An action's own response is a snapshot
    // taken WHILE it was still happening — the drive returns and the browser is often still
    // navigating, still raising a dialog, still landing — so the panel renders a world that has
    // already moved on. Normal cadence at rest, a fast one for a short window after any press.
    const tick = () => {
      if (busyRef.current) return;            // an in-flight action's response is fresher
      const now = Date.now();
      const settling = now < settleUntilRef.current;
      if (settling || now - lastLoadRef.current >= PING_MS) load();
    };
    const t = setInterval(tick, SETTLE_MS);
    return () => clearInterval(t);
  }, [load]);

  // Drop picks for jobs that are no longer on the page. The results list is re-read every ping, and
  // a pick for a vanished job would ride into /choose and come back 422 ("Not on the page under
  // review") — an error the operator did nothing to cause. Above the early return, so the hook runs
  // on every render (Rules of Hooks).
  useEffect(() => {
    retainPicks((panel?.results || []).map((r) => r.job_id));
  }, [panel, retainPicks]);

  // A new page is a new decision: drop any detour or pinned selection from the last one.
  const pageSeen = useRef(panel?.page);
  useEffect(() => {
    if (pageSeen.current !== panel?.page) {
      pageSeen.current = panel?.page;
      setSelection(null);
      setViewPhase(null);
    }
  }, [panel?.page]);

  const cockpit = useMemo(() => deriveCockpit(panel, { picks }), [panel, picks]);

  // THE INVARIANT, ASSERTED RATHER THAN INTENDED. "One primary action on screen" is the rule the
  // old panel broke silently and repeatedly — every local fix added a card, and each card brought a
  // button that looked like the thing to press. A rule with no enforcement point is a comment, so
  // this counts them in dev and says so in the console when the count is wrong. Dev-only: it costs
  // a DOM query per render and nothing in a build.
  const rootRef = useRef(null);
  useEffect(() => {
    if (!import.meta.env.DEV || !rootRef.current) return;
    const n = rootRef.current.querySelectorAll(".btn-primary").length;
    // Two is legitimate in exactly one place: the teacher's proposal, where Correct is a deliberate
    // PEER of Go — a surface whose easy path is always "yes" produces agreement and no signal.
    const allowed = cockpit.focus?.kind === "proposal" ? 2 : 1;
    if (n > allowed) {
      console.warn(`[cockpit] ${n} primary actions on screen (max ${allowed} for focus `
        + `"${cockpit.focus?.kind}"). A capability became a card again — see the rules in `
        + "SessionControlPanel.jsx.");
    }
  });

  // One place adds the initiator, so no call site can forget it. §3 of the session-control plan:
  // the crank is an API call either way, and it carries who turned it.
  const call = async (path, body) => {
    setBusy(true);
    setError("");
    try {
      const d = await postJSON(`/api/session_control/${sessionId}${path}`,
                               { ...(body || {}), initiator: "operator" });
      setPanel(d);
      return d;
    } catch (e) {
      setError(e.message || "the call failed");
      return null;
    } finally {
      setBusy(false);
      // WATCH CLOSELY FOR A MOMENT. The response above was taken while the action was still
      // happening, so it is a true picture of a world that has already moved. The next few seconds
      // are the ones worth reading.
      settleUntilRef.current = Date.now() + SETTLE_WINDOW_MS;
    }
  };

  if (!sessionId) {
    return (
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="sliders" size={17} /> Session cockpit
          </div>
        </div>
        <p className="empty-hint">
          No {domain.label} session yet. Start one from Sessions, then come back — a session is one
          focused browser working one query.
        </p>
      </div>
    );
  }

  const p = panel || {};

  // `picks` goes over the wire IN ORDER — /choose enqueues in the order it receives, and the queue
  // is sequential, so element 0 is the application that runs first.
  const callChoose = async (path, body) => {
    const d = await call(path, { ...body, picks, note });
    if (d) { clearPicks(); setNote(""); }
    return d;
  };

  return (
    <div ref={rootRef}>
      {/* --- the session bar: identity and freshness, said ONCE ------------------------- */}
      <div className="cockpit-bar">
        <AppIcon name="sliders" size={16} />
        <span className="cockpit-bar__id">{p.query ? `"${p.query}"` : "No query yet"}</span>
        {/* Said ONCE. The query, where, how far and which page were previously repeated across a
            setup form, a detail header and two badges; here they have one home, and the work
            surface never restates them. */}
        <span className="cockpit-bar__sub">
          {[p.location, p.radius_miles ? `${p.radius_miles}mi` : null, p.engine,
            `page ${p.page ?? 1}`].filter(Boolean).join(" · ")}
        </span>
        {p.staleness && p.staleness.level !== "fresh" && (
          <span className={`badge badge--${p.staleness.level === "red" ? "danger" : "warn"}`}
                title={p.staleness.why}>
            {p.staleness.level} · stale
          </span>
        )}
        <span className="cockpit-bar__spacer" />
        <button type="button" className="cockpit-live" data-busy={busy} disabled={busy}
                onClick={() => { settleUntilRef.current = 0; load(); }}
                title={`Reads every ${PING_MS / 1000}s, and every ${SETTLE_MS / 1000}s for `
                  + `${SETTLE_WINDOW_MS / 1000}s after anything is pressed. Click to read now.`}>
          <span className="cockpit-live__dot" />
          {busy ? "working" : "live"}
        </button>
      </div>

      <div className="cockpit">
        <SessionRail cockpit={cockpit} selection={selection}
                     onSelect={(sel) => {
                       setSelection(sel);
                       // Selecting a PHASE offers to work it; selecting a step only explains it.
                       setViewPhase(sel.kind === "phase" ? sel.id : null);
                     }} />

        <WorkSurface
          panel={p}
          cockpit={cockpit}
          viewPhase={viewPhase}
          onExitDetour={() => { setViewPhase(null); setSelection(null); }}
          busy={busy}
          error={error}
          call={(path, body) => (path === "/choose" ? callChoose(path, body) : call(path, body))}
          decide={(body) => call("/apply_decide", body)}
          onFlag={(flag, detail) => call("/apply_flag", {
            job_id: cockpit.cycle.application?.job_id, flag, detail,
          })}
          picks={picks} armed={armed} onPick={pick} onClear={clearPicks}
          note={note} setNote={setNote}
          form={form} setForm={setForm}
        />

        <DecisionInspector panel={p} cockpit={cockpit} selection={selection} />
      </div>
    </div>
  );
}
