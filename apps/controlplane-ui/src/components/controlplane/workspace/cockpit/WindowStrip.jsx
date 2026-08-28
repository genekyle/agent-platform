import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { postJSON } from "../api";

// THE WINDOW, ON THE SURFACE — every tab the session holds, whose application each belongs to,
// and one guarded verb per tab.
//
// Operator, 2026-08-10: the cockpit must surface the tab manager, ESPECIALLY on out-of-Indeed
// applications — the apply stage is where the window changes under us (Indeed → employer landing
// → the ATS, each hop stranding the one before). The panel has served `tabs` with roles all
// along; nothing rendered them, so the operator flew a multi-tab window on a single-tab display.
//
// Close is guarded server-side (never the search tab; a tab claimed by an OPEN application
// requires explicit confirmation). This strip posts directly so a refusal's own words arm the
// confirm — the guard is a conversation, not a dead end.
//
// CLAIM is the other half (operator, 2026-08-28: "not just show what it has"). Ownership accrues
// only when a step's drift census watches its tab appear, so tabs opened any other way sit
// unclaimed — and the resolver's fallback for an unclaimed pair is tab-list ORDER, which live
// nearly put one job's fill on another job's form. An unclaimed tab here offers the queue's own
// steps; assigning posts to /claim_tab, whose guards (live tab, known job, never a silent
// reassign) answer in their own words.

const ROLE_BADGE = { search: "ready", apply: "warn", errand: "accent", control: "muted" };

export default function WindowStrip({ sessionId, panel, onActed }) {
  const [confirming, setConfirming] = useState(null);   // {tab, detail}
  const [claiming, setClaiming] = useState(null);       // tab_id whose picker is open
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const tabs = panel?.tabs || [];
  const steps = panel?.queue?.steps || [];
  if (tabs.length <= 1) return null;                    // a one-tab window explains itself

  const close = async (tab, confirmed) => {
    setBusy(true);
    setNote("");
    try {
      await postJSON(`/api/session_control/${sessionId}/close_tab`, {
        tab_id: tab.tab_id, initiator: "operator",
        ...(confirmed ? { confirm_discards_work: true,
          reason: "operator closed it from the window strip after the guard named the risk" } : {}),
      });
      setConfirming(null);
      setNote("closed");
      onActed?.();
    } catch (e) {
      const detail = e.message || "the close was refused";
      // The live-work guard answers 409 with its reason — that reason IS the confirm prompt.
      if (/still open|can lose filled-in work/i.test(detail)) {
        setConfirming({ tab, detail });
      } else {
        setNote(detail);
      }
    } finally {
      setBusy(false);
    }
  };

  const claim = async (tab, jobId) => {
    if (!jobId) return;
    setBusy(true);
    setNote("");
    try {
      await postJSON(`/api/session_control/${sessionId}/claim_tab`, {
        tab_id: tab.tab_id, job_id: jobId, initiator: "operator",
        reason: "assigned from the window strip",
      });
      setClaiming(null);
      setNote("claimed");
      onActed?.();
    } catch (e) {
      setNote(e.message || "the claim was refused");    // the guard's own words
    } finally {
      setBusy(false);
    }
  };

  // Unfinished work first — those are the tabs whose ownership actually steers the resolver —
  // but parked/done steps stay offered: a parked application's lingering tab is exactly the one
  // that needs its name on the door.
  const claimable = [...steps].sort((a, b) => (a.done === b.done ? 0 : a.done ? 1 : -1));

  return (
    <div className="window-strip">
      <span className="window-strip__label">
        <AppIcon name="boxes" size={12} /> Window · {tabs.length} tabs
      </span>
      {tabs.map((t) => {
        const host = (t.url || "").replace(/^https?:\/\/(www\.)?/, "").split("/")[0];
        const claimRec = t.claimed_by;
        return (
          <span key={t.tab_id} className="window-strip__tab"
                title={`${t.url}${claimRec ? `\nclaimed by ${claimRec.title || claimRec.job_id}` : ""}`}>
            <span className={`badge badge--${ROLE_BADGE[t.role] || "muted"}`}>{t.role}</span>
            <code>{host}</code>
            {claimRec && <em>· {claimRec.title || claimRec.job_id}</em>}
            {!t.is_search && !claimRec && claimable.length > 0 && (
              claiming === t.tab_id ? (
                <select className="window-strip__claim-pick" disabled={busy} autoFocus
                        aria-label={`Which application owns the ${host} tab`}
                        defaultValue=""
                        onChange={(e) => claim(t, e.target.value)}
                        onBlur={() => setClaiming(null)}>
                  <option value="" disabled>whose tab is this?</option>
                  {claimable.map((s) => (
                    <option key={s.job_id} value={s.job_id}>
                      {(s.title || s.job_id)}{s.done ? ` · ${s.terminal || "done"}` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <button className="btn btn-sm btn-ghost" disabled={busy}
                        aria-label={`Claim the ${host} tab for an application`}
                        title="Unowned tab — name which application it belongs to, so fills and cleanup land on the right window"
                        onClick={() => setClaiming(t.tab_id)}>
                  claim
                </button>
              )
            )}
            {!t.is_search && (
              <button className="btn btn-sm btn-ghost" disabled={busy}
                      aria-label={`Close the ${host} tab`}
                      title="Close this tab — refused if it holds an open application's work unless you confirm"
                      onClick={() => close(t, false)}>
                ×
              </button>
            )}
          </span>
        );
      })}
      {note && <span className="window-strip__note">{note}</span>}
      {confirming && (
        <span className="window-strip__confirm">
          <em>{confirming.detail}</em>
          <button className="btn btn-sm" disabled={busy} aria-label="Close it anyway"
                  onClick={() => close(confirming.tab, true)}>
            Close it anyway — discard its work
          </button>
          <button className="btn btn-sm btn-ghost" disabled={busy}
                  onClick={() => setConfirming(null)}>
            Keep it
          </button>
        </span>
      )}
    </div>
  );
}
