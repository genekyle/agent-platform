import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { getJSON, postJSON } from "../api";

// START A SESSION — the cockpit's own door to a fresh browser.
//
// Found 2026-08-10 arriving at /cockpit with every session closed: the page fell back to an
// arbitrary CLOSED session's declare form (a dead browser's setup screen), and the only route to
// a new session was a link into the Learning area that renders solely when the session list is
// EMPTY — which it never is once one session has ever existed. The verb the moment asks for is
// "start one", here. Provisioning reuses the training-session seams as-is: create (which binds
// the domain's default account and therefore its persistent signed-in profile) then start
// (which launches the session Chrome on its own debug port).
//
// 2026-08-13 — THE HANDOFF. This rendered only when NOTHING was live, which made it useless in
// the one case that matters: a stale session already holding the domain. The operator's whole
// menu was then "keep working the stale session" or "switch to a dead one", so every session
// began by fighting the last one's leftovers instead of starting clean (operator: "we crash too
// hard when we are trying to fix the stale sessions when those sessions are mainly outliers").
// A persistent profile dir backs only ONE Chrome, so starting fresh genuinely requires retiring
// the incumbent — the backend says so with a 409. A truthful 409 the operator cannot act on is
// still a dead end, so the handoff happens HERE, and it names what the incumbent is carrying
// before it touches it.
//
// The two exits are kept DISTINCT because conflating them is how applications get lost:
//   * RETIRE  — stop the browser, leave the ledger whole. The work is resumable; nothing is
//               flagged. This is the handoff default.
//   * CLOSE OUT — the cleanup protocol (`CloseOut`), which flags unfinished applications
//               `abandoned:operator` with a reason. It lives in the incumbent's own cockpit,
//               where it can list exactly what dies; we link there rather than reimplement a
//               confirm that must never be a shortcut.

const DOMAINS = [
  { id: "indeed_jobs", label: "Indeed" },
  { id: "linkedin_jobs", label: "LinkedIn" },
];

export default function StartSession({ onStarted, sessions = [] }) {
  const [domain, setDomain] = useState("indeed_jobs");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [stage, setStage] = useState("");

  // THE INCUMBENT: the live session already holding this domain's persistent profile. Chrome
  // locks a user-data-dir, so this session is not a rival — it is the thing in the doorway.
  const incumbent = sessions.find(
    (s) => s.domain_id === domain && s.status === "active" && s.live);
  const holding = incumbent?.holding || { unfinished: 0, submitted: 0, titles: [] };

  const start = async ({ retire = false } = {}) => {
    setBusy(true);
    setErr("");
    try {
      if (retire && incumbent) {
        setStage(`retiring #${incumbent.id}…`);
        // The plain stop: Chrome dies, the ledger does not. Protected sessions refuse here, and
        // that refusal is the operator's to resolve — we surface it rather than force past it.
        await postJSON(`/api/training/sessions/${incumbent.id}/stop`, {});
      }

      setStage("choosing the scenario…");
      const scenarios = await getJSON("/api/training/scenarios");
      const mine = scenarios.filter((s) => s.domain_id === domain && s.status === "active");
      // The search-results scenario is the session's natural start for a job hunt; any active
      // scenario for the domain is an acceptable fallback — the scenario seeds capture context,
      // the DECLARE step still names the actual query.
      const scenario = mine.find((s) => s.scenario_id.includes("search_results"))
        || mine.find((s) => s.goal_id === "open_job_posting") || mine[0];
      if (!scenario) throw new Error(`no active scenario registered for ${domain}`);

      setStage("binding the account…");
      const accounts = await getJSON("/api/accounts").catch(() => ({ accounts: [] }));
      const mineAccts = (accounts.accounts || [])
        .filter((a) => a.domain_id === domain && a.status === "active");
      // The domain's default account carries the persistent signed-in profile — the whole point
      // of starting here instead of a bare Chrome.
      const acct = mineAccts.find((a) => a.account_id.endsWith("_default")) || mineAccts[0];

      setStage("creating the session…");
      const created = await postJSON("/api/training/sessions", {
        domain_id: domain, scenario_id: scenario.scenario_id,
        ...(acct ? { account_id: acct.account_id } : {}),
        notes: "started from the cockpit",
      });

      setStage("launching Chrome…");
      await postJSON(`/api/training/sessions/${created.id}/start`, {});
      onStarted?.(created.id);
    } catch (e) {
      setErr(e.message || "could not start a session");
    } finally {
      setBusy(false);
      setStage("");
    }
  };

  return (
    <div className="start-session">
      <div className="start-session__head">
        <AppIcon name="play" size={14} /> <strong>Start a session</strong>
        <span>one focused browser, its saved sign-in, ready to declare a search</span>
      </div>
      <div className="work__actions">
        <select value={domain} disabled={busy} aria-label="Domain"
                onChange={(e) => setDomain(e.target.value)}>
          {DOMAINS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
        </select>
        {incumbent ? (
          <button className="btn btn-primary" disabled={busy}
                  aria-label={`Retire session ${incumbent.id} and start fresh`}
                  title={`Stops session #${incumbent.id}'s Chrome so this domain's profile is free, then launches a fresh one. Its ledger is kept — nothing is flagged abandoned.`}
                  onClick={() => start({ retire: true })}>
            {busy ? (stage || "…") : `Retire #${incumbent.id} & start fresh`}
          </button>
        ) : (
          <button className="btn btn-primary" disabled={busy} aria-label="Start a session"
                  title="Provisions a fresh Chrome on this domain's persistent profile — the saved sign-in comes with it"
                  onClick={() => start()}>
            {busy ? (stage || "…") : "Start"}
          </button>
        )}
      </div>

      {incumbent && (
        <p className="rung__meta">
          Session <strong>#{incumbent.id}</strong> is live on this domain and holds its profile —
          only one Chrome can. Retiring stops its browser and <strong>keeps its work</strong>:{" "}
          {holding.unfinished > 0 ? (
            <>
              {holding.unfinished} unfinished application{holding.unfinished === 1 ? "" : "s"}
              {holding.titles.length > 0 && <> ({holding.titles.join(" · ")})</>} stay on its
              ledger, resumable. To end it <em>on the record</em> instead — flagging those with a
              reason — close it out from its own cockpit first.
            </>
          ) : (
            <>nothing is half-finished there{holding.submitted > 0
              && <>, and its {holding.submitted} submitted application
                  {holding.submitted === 1 ? "" : "s"} are already banked</>}.</>
          )}
        </p>
      )}
      {err && <div className="coaching-error">{err}</div>}
    </div>
  );
}
