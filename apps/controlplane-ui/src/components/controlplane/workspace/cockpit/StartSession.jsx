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

const DOMAINS = [
  { id: "indeed_jobs", label: "Indeed" },
  { id: "linkedin_jobs", label: "LinkedIn" },
];

export default function StartSession({ onStarted }) {
  const [domain, setDomain] = useState("indeed_jobs");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [stage, setStage] = useState("");

  const start = async () => {
    setBusy(true);
    setErr("");
    try {
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
        <button className="btn btn-primary" disabled={busy} aria-label="Start a session"
                title="Provisions a fresh Chrome on this domain's persistent profile — the saved sign-in comes with it"
                onClick={start}>
          {busy ? (stage || "…") : "Start"}
        </button>
      </div>
      {err && <div className="coaching-error">{err}</div>}
    </div>
  );
}
