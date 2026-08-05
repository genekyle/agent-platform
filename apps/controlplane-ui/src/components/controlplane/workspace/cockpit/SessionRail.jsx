import { AppIcon } from "../../../../ui/Icon";
import { fmtTime } from "../api";

// THE RAIL — where we are, and nothing else.
//
// The groups mirror the checkpoint ladder's real shape: the SESSION preamble (climbed once, held
// always), then one group PER RESULTS PAGE — each page carrying its own read rung, its picks rung,
// and its applications. A past page collapses to its record ("1 of 21 picked by operator"), never
// to a bare "done" — a collapsed step whose summary is "done" has thrown away the only thing worth
// keeping. The current page is the work.
//
// The rail carries NO actions. Not one. Every control on this screen lives in the work surface,
// because the moment a second place can act, the operator has to decide which place means it — and
// that is the ambiguity the whole redesign exists to remove. The rail's only interaction is
// SELECTION: clicking a group or a step points the inspector at it (and clicking the current
// page's picks rung re-opens the picker in the work surface — a detour, labelled as one there).

const MARK = {
  done: "check",
  current: "play",
  blocked: "alert",
  attention: "refresh",
  // ENTERED BUT NOT FINISHED, and it must not look like either neighbour. A tick would claim a
  // completion that has not happened; an empty circle would claim the group was never reached.
  open: "circleDot",
  pending: "circle",
};

function isSameSelection(a, b) {
  return !!a && !!b && a.kind === b.kind && a.id === b.id;
}

export function SessionRail({ cockpit, selection, onSelect }) {
  const { groups, current, blocker, cycle } = cockpit;

  return (
    <div className="cockpit__pane cockpit__pane--rail">
      <div className="cockpit__pane-head">
        <AppIcon name="waypoints" size={14} /> Where we are
      </div>

      <div className="rail">
        {groups.map((g) => {
          // A group expands when it is the work, when it is stuck or lapsing, or when the operator
          // has selected it or something in it. Everything else stays one line — its record.
          const expanded = g.status === "current" || g.status === "blocked"
            || g.status === "attention"
            || isSameSelection(selection, g.select)
            || g.steps.some((s) => isSameSelection(selection, s.select));

          return (
            <div key={g.id} className="rail__phase" data-status={g.status}>
              <button type="button" className="rail__head"
                      aria-expanded={expanded}
                      aria-current={g.id === current ? "step" : undefined}
                      onClick={() => onSelect(g.select)}>
                <span className="rail__mark">
                  <AppIcon name={MARK[g.status] || "circle"} size={11} />
                </span>
                <span className="rail__body">
                  <span className="rail__label">{g.label}</span>
                  <span className="rail__summary">{g.summary}</span>
                </span>
              </button>

              {expanded && g.steps.length > 0 && (
                <ul className="rail__steps">
                  {g.steps.map((s) => (
                    <li key={s.key}>
                      <button type="button"
                              className={`rail__step${isSameSelection(selection, s.select) ? " is-selected" : ""}`}
                              data-status={s.status}
                              title={[s.meta, s.at ? fmtTime(s.at) : ""].filter(Boolean).join(" · ")}
                              onClick={() => onSelect(s.select)}>
                        <span className="rail__step-dot" />
                        <span className="rail__step-label">{s.label}</span>
                        {s.note && <span className="rail__step-note">{s.note}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}

        {/* The ladder is open-ended; the rail says so rather than implying a finish line. */}
        <p className="rail__cycle">
          {cycle.application
            ? `Application ${cycle.application.index} of ${cycle.application.total} on page ${cycle.page}`
            : `Page ${cycle.page}${cycle.pages_reviewed ? ` · ${cycle.pages_reviewed} reviewed` : ""}`}
          <br />
          A page is read, picked from, and its applications worked to a terminal flag — then the
          next page. No end flag: it stops when there is no next page.
        </p>

        {blocker && (
          <p className="rail__blocker">
            <AppIcon name="alert" size={11} /> {blocker.text}
          </p>
        )}
      </div>
    </div>
  );
}
