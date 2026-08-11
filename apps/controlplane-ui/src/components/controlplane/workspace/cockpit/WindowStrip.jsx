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

const ROLE_BADGE = { search: "ready", apply: "warn", errand: "accent", control: "muted" };

export default function WindowStrip({ sessionId, panel, onActed }) {
  const [confirming, setConfirming] = useState(null);   // {tab, detail}
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const tabs = panel?.tabs || [];
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

  return (
    <div className="window-strip">
      <span className="window-strip__label">
        <AppIcon name="boxes" size={12} /> Window · {tabs.length} tabs
      </span>
      {tabs.map((t) => {
        const host = (t.url || "").replace(/^https?:\/\/(www\.)?/, "").split("/")[0];
        const claim = t.claimed_by;
        return (
          <span key={t.tab_id} className="window-strip__tab"
                title={`${t.url}${claim ? `\nclaimed by ${claim.title || claim.job_id}` : ""}`}>
            <span className={`badge badge--${ROLE_BADGE[t.role] || "muted"}`}>{t.role}</span>
            <code>{host}</code>
            {claim && <em>· {claim.title || claim.job_id}</em>}
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
