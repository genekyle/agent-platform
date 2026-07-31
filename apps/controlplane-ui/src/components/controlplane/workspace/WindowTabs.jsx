import { AppIcon } from "../../../ui/Icon";

// THE WINDOW, VISIBLE. What tabs this session's Chrome has open, which one is the search, which
// one is the application, and what moved last.
//
// Operator-directed 2026-07-30, and the reason is worth keeping: the cockpit's only word for the
// browser was a count. An Indeed apply opens a SECOND tab and navigates it three times, so the
// page the operator was being asked to judge lived in a tab the panel never mentioned — while the
// panel read "1 Tabs". Out of sight, out of mind, in a system whose whole state is which page is
// in front of which tab. A count is not a window.
//
// Roles come from the server, which classifies them with the SAME function the drift detector
// uses — so this panel and the ledger cannot disagree about which tab is the application.

const ROLE_TONE = {
  apply: "accent",
  search: "ready",
  errand: "warn",
  blank: "muted",
  unknown: "muted",
};

// The host is what tells you where you are at a glance; the path is what tells you what step.
// Showing the whole URL makes every row the same width and none of them readable.
function split(url) {
  try {
    const u = new URL(url);
    return { host: u.host.replace(/^www\./, ""), path: (u.pathname + u.search).slice(0, 72) };
  } catch {
    return { host: url || "—", path: "" };
  }
}

export default function WindowTabs({ tabs, drift }) {
  if (!tabs) return null;

  return (
    <div className="sc-login">
      <div className="sc-login__head">
        <AppIcon name="boxes" size={14} /> Window
        <span className="badge badge--muted">
          {tabs.length} tab{tabs.length === 1 ? "" : "s"}
        </span>
        {/* Drift is what the window did on its own — a tab that opened, closed or navigated since
            the last look. Shown here rather than buried, because on this system a new tab IS the
            next step. */}
        {drift?.opened?.length > 0 && (
          <span className="badge badge--warn" title={drift.opened.join("\n")}>
            {drift.opened.length} opened since last look
          </span>
        )}
      </div>

      {tabs.length === 0 && (
        <p className="rung__meta">No tabs answering — the session's Chrome may be gone.</p>
      )}

      <ul className="rungs">
        {tabs.map((t) => {
          const { host, path } = split(t.url);
          return (
            <li key={t.tab_id} className={`rung rung--${t.is_apply ? "held" : "pending"}`}>
              <span className={`rung__mark badge badge--${ROLE_TONE[t.role] || "muted"}`}>
                <AppIcon name={t.role === "apply" ? "briefcase" : "search"} size={13} />
              </span>
              <div className="rung__body">
                <div className="rung__line">
                  <span className="rung__label">{host}</span>
                  <span className={`badge badge--${ROLE_TONE[t.role] || "muted"}`}>{t.role}</span>
                  {/* WHICH ONE ARE WE DRIVING. Two Indeed tabs look identical by host; only these
                      say which is the results list and which is the application in progress. */}
                  {t.is_apply && <span className="badge badge--accent">being applied</span>}
                  {t.is_search && <span className="badge badge--ready">the search</span>}
                </div>
                <div className="rung__meta" title={t.url}>
                  <code>{path || "/"}</code>
                </div>
                {t.title && <div className="rung__meta">{t.title}</div>}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
