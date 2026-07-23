import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "./api";
import { AppIcon } from "../../../ui/Icon";

// The session window — what else is open, and what holds no work.
//
// The controller has surveyed the window on every turn since the tab manager landed, but only
// INSIDE a drive: between drives nobody could ask, and nothing anywhere could act on the answer.
// `tidy_window()` was written, tested and had no call site, which is a capability the system does
// not actually have. This card is the operator's half of the fix.
//
// Surveying is free (a local socket) and automatic. CLOSING is explicit and asked for, because
// this window is shared with a human who may have opened something themselves — so the button is
// here rather than on a timer.

const ROLE_HELP = {
  search: "the results list the drive returns to",
  apply: "an application in progress",
  errand: "a cross-domain detour, e.g. mail for a login code",
  terminal: "finished and inert — a confirmation page",
  blank: "empty",
  unknown: "not ours to classify, so never closed automatically",
};

export function WindowCard({ domainId, tabId = "" }) {
  // Resolve our own browser url. Nothing else in the cockpit carries one, and threading it down
  // from the workspace would couple every parent to a detail only this card needs.
  const [browserUrl, setBrowserUrl] = useState("");
  const [win, setWin] = useState(null);
  const [tabs, setTabs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getJSON("/api/sessions")
      .then((d) => {
        const s = (d.sessions || []).find((x) => x.domain_id === domainId && x.live);
        setBrowserUrl(s?.port ? `http://localhost:${s.port}` : "");
      })
      .catch(() => setBrowserUrl(""));
  }, [domainId]);

  const load = useCallback(() => {
    if (!browserUrl) return;
    postJSON("/api/controller/window", { browser_url: browserUrl, tab_id: tabId })
      .then((d) => {
        setWin(d.window || null);
        setTabs(d.tabs || []);
      })
      .catch(() => setWin(null));
  }, [browserUrl, tabId]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const tidy = async () => {
    setBusy(true);
    setMsg("");
    try {
      const d = await postJSON("/api/controller/window/tidy", {
        browser_url: browserUrl,
        tab_id: tabId,
      });
      const n = (d.closed || []).length;
      setMsg(
        n
          ? `Closed ${n} tab${n === 1 ? "" : "s"} · ${d.tabs_before} → ${d.tabs_after}`
          : "Nothing held no work — everything open is doing something.",
      );
      if (d.refused?.length) setMsg((m) => `${m} · ${d.refused.length} refused`);
      load();
    } catch (e) {
      setMsg(e.message || "could not tidy");
    } finally {
      setBusy(false);
    }
  };

  if (!win) return null;

  const closable = win.closable || [];
  const anomalies = win.anomalies || [];

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon">
          <AppIcon name="layers" size={17} /> Session window
          {win.health && win.health !== "ok" && (
            <span className="badge badge--bad" style={{ marginLeft: 8 }}>{win.health}</span>
          )}
        </div>
        <span className="layer__count">
          {win.count} tab{win.count === 1 ? "" : "s"} · budget {win.budget}
        </span>
      </div>

      {/* Health first: a duplicate application is a fault, not clutter, and it names whether it can
          resolve itself or needs the operator to pick which tab holds the work. */}
      {anomalies.map((a, i) => (
        <div key={i} className="window-anomaly">
          <AppIcon name="alert" size={13} /> {a.why}
          {!a.resolvable && <em> — surveyed between drives, so pick which to keep before tidying.</em>}
        </div>
      ))}

      {win.over_budget && (
        <div className="coaching-blocked">
          Over budget — a cluttered window slows every operation in it.
        </div>
      )}

      <div className="window-tabs">
        {tabs.map((t) => {
          const isClosable = closable.some((c) => c.tab_id === t.tab_id);
          return (
            <div key={t.tab_id} className="window-tab" title={ROLE_HELP[t.role] || ""}>
              <span className={`badge badge--${t.active ? "ok" : "muted"}`}>{t.role}</span>
              <span className="window-tab__url">{t.url}</span>
              {t.active && <span className="window-tab__flag">driving</span>}
              {isClosable && <span className="window-tab__flag window-tab__flag--closable">no work</span>}
            </div>
          );
        })}
      </div>

      {(closable.length > 0 || msg) && (
        <div className="window-actions">
          {closable.length > 0 && (
            <button className="btn btn-sm" onClick={tidy} disabled={busy}>
              {busy ? "…" : `Tidy ${closable.length}`}
            </button>
          )}
          {msg && <span className="attention-item__hint">{msg}</span>}
        </div>
      )}
    </div>
  );
}
