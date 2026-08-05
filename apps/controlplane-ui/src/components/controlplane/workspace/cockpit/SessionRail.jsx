import { AppIcon } from "../../../../ui/Icon";
import { fmtTime } from "../api";

// THE RAIL — where we are, and nothing else.
//
// Five lifecycle phases, status only, current step, blockers. Completed phases collapse to one line
// that says what they ACHIEVED ("browser ready · signed in to Indeed"), not merely that they are
// shut — a collapsed step whose summary is "done" has thrown away the only thing worth keeping.
//
// The rail carries NO actions. Not one. Every control on this screen lives in the work surface,
// because the moment a second place can act, the operator has to decide which place means it — and
// that is the ambiguity the whole redesign exists to remove. The rail's only interaction is
// SELECTION: clicking a phase or a step points the inspector at it.

const MARK = {
  done: "check",
  current: "play",
  blocked: "alert",
  attention: "refresh",
  // ENTERED BUT NOT FINISHED, and it must not look like either neighbour. A tick would claim a
  // completion that has not happened; an empty circle would claim the phase was never reached.
  open: "circleDot",
  pending: "circle",
};

function isSameSelection(a, b) {
  return !!a && !!b && a.kind === b.kind && a.id === b.id;
}

export function SessionRail({ cockpit, selection, onSelect }) {
  const { phases, current, blocker, cycle } = cockpit;

  return (
    <div className="cockpit__pane cockpit__pane--rail">
      <div className="cockpit__pane-head">
        <AppIcon name="waypoints" size={14} /> Where we are
      </div>

      <div className="rail">
        {phases.map((ph) => {
          // A phase expands when it is the work, when it is stuck, or when the operator has
          // selected it. Everything else stays one line.
          const expanded = ph.status === "current" || ph.status === "blocked"
            || ph.status === "attention"
            || isSameSelection(selection, ph.select)
            || ph.steps.some((s) => isSameSelection(selection, s.select));

          return (
            <div key={ph.id} className="rail__phase" data-status={ph.status}>
              <button type="button" className="rail__head"
                      aria-expanded={expanded}
                      aria-current={ph.id === current ? "step" : undefined}
                      onClick={() => onSelect(ph.select)}>
                <span className="rail__mark">
                  <AppIcon name={MARK[ph.status] || "circle"} size={11} />
                </span>
                <span className="rail__body">
                  <span className="rail__label">{ph.label}</span>
                  <span className="rail__summary">{ph.summary}</span>
                </span>
              </button>

              {expanded && ph.steps.length > 0 && (
                <ul className="rail__steps">
                  {ph.steps.map((s) => (
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

        {/* The lifecycle CYCLES; the rail says so rather than drawing a straight line the work does
            not walk. Discover → Decide → Execute → Verify runs once per results page, and Execute
            runs once per application inside it. */}
        <p className="rail__cycle">
          Page {cycle.page}
          {cycle.pages_reviewed ? ` · ${cycle.pages_reviewed} reviewed` : ""}
          {cycle.application ? ` · application ${cycle.application.index} of ${cycle.application.total}` : ""}
          <br />
          Discover → Decide → Execute → Verify repeats per page. No end flag: it stops when there
          is no next page.
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
