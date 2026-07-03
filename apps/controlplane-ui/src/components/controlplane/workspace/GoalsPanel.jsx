import { useEffect, useState } from "react";
import { getJSON } from "./api";

// The Goal layer — the most important UX shift. Instead of clicking "post item" / "check
// responses" as the interface, the operator enables GOALS (standing objectives). Each goal
// shows its on/off switch, schedule, and approval posture. Goals come from the training
// registry; their on/off state + the domain automation mode drive the posture text.

const APPROVAL_BY_MODE = {
  manual: "Asks before every action",
  supervised: "Asks before publish / apply / message",
  autopilot: "Runs unattended (approved recipes)",
};

export function GoalsPanel({ domain, mode, goalState, onToggleGoal }) {
  const [goals, setGoals] = useState([]);
  useEffect(() => {
    getJSON(`/api/training/goals?domain_id=${encodeURIComponent(domain.id)}`)
      .then((rows) => setGoals(Array.isArray(rows) ? rows : []))
      .catch(() => setGoals([]));
  }, [domain.id]);

  const enabled = (goalId) => (goalState?.[goalId] ?? true); // absent → on by default

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title">🎯 Goals</div>
        <span className="layer__sub">What this domain is set up to do</span>
      </div>

      {goals.length === 0 ? (
        <div className="empty-hint">No goals registered for this domain yet.</div>
      ) : (
        goals.map((g) => {
          const on = enabled(g.goal_id);
          return (
            <div key={g.goal_id} className="goal-card">
              <div style={{ minWidth: 0 }}>
                <div className="goal-card__name">{g.display_name || g.goal_id}</div>
                <div className="goal-card__meta">
                  <span className={`badge ${on ? "badge--ok" : "badge--muted"}`}>{on ? "Active" : "Paused"}</span>
                  <span>Schedule: Manual</span>
                  <span>· {APPROVAL_BY_MODE[mode] || APPROVAL_BY_MODE.manual}</span>
                </div>
              </div>
              <label className="switch" title={on ? "Pause goal" : "Activate goal"}>
                <input type="checkbox" checked={on} onChange={(e) => onToggleGoal(g.goal_id, e.target.checked)} />
                <span className="switch__track" />
              </label>
            </div>
          );
        })
      )}
      <div className="mode-hint" style={{ marginTop: 12 }}>
        Scheduling (hourly / daily) lands with Autopilot in a later pass — today goals run when you trigger
        their tasks below.
      </div>
    </div>
  );
}
