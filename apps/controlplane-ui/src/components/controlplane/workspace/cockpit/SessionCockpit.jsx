import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getJSON, postJSON } from "../api";
import { AppIcon } from "../../../../ui/Icon";
import { useOrderedPicks } from "../useOrderedPicks";
import { DOMAINS_BY_ID } from "../domains";
import { deriveCockpit } from "./lifecycle";
import { SessionRail } from "./SessionRail";
import { WorkSurface } from "./WorkSurface";
import { DecisionInspector } from "./DecisionInspector";
import "./cockpit.css";

// ONE SESSION'S COCKPIT — the keyed composition root.
//
// Three panes, one authoritative workflow state:
//
//   rail                work surface                     inspector
//   where we are        what needs attention NOW         why this action, why this state
//
// This file owns DATA and SELECTION only. It does not decide what a group is, which control belongs
// on screen, or which action is primary — all of that is derived once, in `lifecycle.js`, and read
// by all three panes.
//
// SESSION-AWARE BY CONSTRUCTION: this component must be mounted with `key={sessionId}` (the
// CockpitPage does). A session change UNMOUNTS it, so everything about one session — the panel read
// model, the `last_step` carry, the note draft, the pinned selection, the picker detour, the settle
// clock — dies with the session it described rather than surviving into the next one's story.
// Picks were already safe (`useOrderedPicks` scopes to session+page in localStorage); the rest is
// safe because of the key.
//
// The rules that keep this screen from growing back into card soup:
//   1. ONE primary action on screen. Asserted at runtime in dev, not just intended.
//   2. A capability becomes a FOCUS KIND or an INSPECTOR ROW. Never a new top-level card.
//   3. The rail never acts. Selection only.
//   4. Say a fact once. The session bar owns identity; the panes never restate it.

//: How often the panel re-reads the session. Every call is a local CDP socket (tabs + auth state),
//: so this costs no bandwidth — the number is about how quickly the operator should see the world
//: change, not about load.
const PING_MS = 5000;
//: How often the interval wakes, and how long a press keeps it reading at that speed. The panel
//: reads at PING_MS at rest and at SETTLE_MS while the page is settling from something we did.
const SETTLE_MS = 1000;
const SETTLE_WINDOW_MS = 12000;

//: The session's domain, in a word — for the cross-domain picker.
function domainShort(domainId) {
  return DOMAINS_BY_ID[domainId]?.short || DOMAINS_BY_ID[domainId]?.label || domainId || "?";
}

export function SessionCockpit({ sessionId, sessionMeta, siblings, onChooseSession }) {
  const [panel, setPanel] = useState(null);
  const [form, setForm] = useState({ query: "", location: "", radius_miles: 50 });
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // What the inspector is explaining. `null` follows the focus, which is what the operator sees
  // first and returns to; a rail click pins it to a group, a rung or an application.
  const [selection, setSelection] = useState(null);
  // The one work-surface detour: "choose" re-opens the current page's picker. See WorkSurface.
  const [viewMoment, setViewMoment] = useState(null);

  // Picks carry an ORDER, not just a tick — that order is the order the applications run. Scoped to
  // (session, page): the draft survives navigating away, and turning the page starts a fresh one.
  const { picks, armed, pick, clear: clearPicks, retain: retainPicks } =
    useOrderedPicks(`${sessionId}.${panel?.page ?? 1}`);

  // A ref, not the state, because the interval closes over its first render.
  const busyRef = useRef(false);
  busyRef.current = busy;
  const lastLoadRef = useRef(0);
  const settleUntilRef = useRef(0);

  // The panel PINGS the session rather than only reading it when you press something. The world
  // moves underneath us — you sign in by hand, a challenge appears, a tab closes — and a cockpit
  // that only refreshes on click shows a past that has stopped being true. The read model is all
  // local CDP sockets, so this is free even in low-data mode.
  const load = useCallback(() => {
    lastLoadRef.current = Date.now();
    getJSON(`/api/session_control/${sessionId}`)
      .then((d) => {
        // A poll carries no last_step; keep the one the operator is still reading rather than
        // blanking the result of the action they just took. Safe ONLY because this component is
        // keyed by session id — the carry can never cross into another session's story.
        setPanel((prev) => ({ ...d, last_step: d.last_step ?? prev?.last_step ?? null }));
        // Sync the form ONCE, when a declared query first arrives. Re-syncing on every ping would
        // overwrite what the operator is mid-way through typing. (A query cannot change within a
        // session — the server refuses — so once is enough.)
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
  // review") — an error the operator did nothing to cause.
  useEffect(() => {
    retainPicks((panel?.results || []).map((r) => r.job_id));
  }, [panel, retainPicks]);

  // A new page is a new decision: drop the detour, the pinned selection AND the note draft from
  // the last one. The note is scoped to a page's record — another initiator advancing the page
  // must not leave page 1's rationale riding into page 2's /choose.
  const pageSeen = useRef(panel?.page);
  useEffect(() => {
    if (pageSeen.current !== panel?.page) {
      pageSeen.current = panel?.page;
      setSelection(null);
      setViewMoment(null);
      setNote("");
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
        + "SessionCockpit.jsx.");
    }
  });

  // One place adds the initiator, so no call site can forget it. The crank is an API call either
  // way, and it carries who turned it.
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

  const p = panel || {};

  // `picks` goes over the wire IN ORDER — /choose enqueues in the order it receives, and the queue
  // is sequential, so element 0 is the application that runs first.
  const callChoose = async (path, body) => {
    const d = await call(path, { ...body, picks, note });
    if (d) { clearPicks(); setNote(""); setViewMoment(null); }
    return d;
  };

  // What a rail click means. Everything selects for the inspector; ONE selection also carries a
  // work-surface meaning — the current page's picks rung re-opens the picker (the STANDING select
  // rung allows adding picks). Centralised here so the rail itself never decides behaviour.
  const onSelect = (sel) => {
    setSelection(sel);
    setViewMoment(sel.kind === "rung" && sel.id === `select:${p.page ?? 1}` ? "choose" : null);
  };

  return (
    <div ref={rootRef}>
      {/* --- the session bar: identity and freshness, said ONCE ------------------------- */}
      <div className="cockpit-bar">
        <AppIcon name="sliders" size={16} />
        {/* WHICH session, visibly. Contextual history belongs to a session id, and the id the
            cockpit is narrating should never be something the operator has to infer. */}
        <span className="badge badge--muted" title={`Session ${sessionId}`}>#{sessionId}</span>
        <span className="cockpit-bar__id">{p.query ? `"${p.query}"` : "No query yet"}</span>
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
        {/* The cockpit is SESSION-first, so the picker spans every domain's sessions — a session
            is one focused Chrome working one task, not a property of a domain, and a Gmail errand
            inside a Career-Search session is exactly why domain can't be the boundary. Labelled by
            STATUS, the control plane's own record, not the port probe: stopped sessions share the
            active one's debug port, so `live` alone read as nine live sessions with eight of them
            stopped. Active first; stopped history stays reachable because a left-open session is a
            fixture worth looking at. */}
        {siblings.length > 1 && (
          <label className="cockpit-bar__sessions">
            <span>session</span>
            <select value={sessionId}
                    onChange={(e) => onChooseSession(Number(e.target.value))}>
              {[...siblings].sort((a, b) =>
                (a.status === "active" ? 0 : 1) - (b.status === "active" ? 0 : 1) || b.id - a.id)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} · {domainShort(s.domain_id)} · {s.status}
                  </option>
                ))}
            </select>
          </label>
        )}
        {sessionMeta.status !== "active" && (
          <span className="badge badge--warn" title="This session is not active — its record is readable, but drive its browser only if you know why.">
            {sessionMeta.status}
          </span>
        )}
        <button type="button" className="cockpit-live" data-busy={busy} disabled={busy}
                onClick={() => { settleUntilRef.current = 0; load(); }}
                title={`Reads every ${PING_MS / 1000}s, and every ${SETTLE_MS / 1000}s for `
                  + `${SETTLE_WINDOW_MS / 1000}s after anything is pressed. Click to read now.`}>
          <span className="cockpit-live__dot" />
          {busy ? "working" : "live"}
        </button>
      </div>

      <div className="cockpit">
        <SessionRail cockpit={cockpit} selection={selection} onSelect={onSelect} />

        <WorkSurface
          panel={p}
          cockpit={cockpit}
          viewMoment={viewMoment}
          onExitDetour={() => { setViewMoment(null); setSelection(null); }}
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
