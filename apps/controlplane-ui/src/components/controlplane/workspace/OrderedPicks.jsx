// The ordered-pick click target. Its state lives in `useOrderedPicks` — see that file for the
// gestures and why the order is load-bearing.

// The click target itself. A real <button> rather than a styled <div>: it has to be reachable by
// keyboard and announce its state, since it is now carrying more meaning than a tick.
export function PickOrb({ jobId, label, picks, armed, onPick }) {
  const at = picks.indexOf(jobId);
  const picked = at !== -1;
  const isArmed = armed === jobId;
  const name = label || jobId;

  const hint = !picked
    ? `Pick ${name} — it becomes #${picks.length + 1}`
    : isArmed
      ? `#${at + 1} ${name} — click another number to swap, or click again to remove`
      : armed
        ? `Swap #${at + 1} ${name} with the one you have picked up`
        : `#${at + 1} ${name} — click to pick it up and swap`;

  return (
    <button
      type="button"
      className={`pick-orb${picked ? " is-picked" : ""}${isArmed ? " is-armed" : ""}`}
      aria-pressed={picked}
      aria-label={hint}
      title={hint}
      onClick={() => onPick(jobId)}
    >
      {picked ? at + 1 : ""}
    </button>
  );
}
