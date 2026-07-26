import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

// Ordered picking — a checkbox that also says WHEN. The STATE half; the click target it
// drives lives in OrderedPicks.jsx (split so Fast Refresh keeps working — a module that
// exports both a hook and a component disables it for the whole file).
//
// A plain checkbox answers "is this one in?" and nothing else. But the order picks are made IS
// the order the applications run: `/choose` enqueues in the order it receives them, and the apply
// queue is strictly sequential. So the panel was already deciding the running order by click
// order and showing no sign of it — the one control whose sequence mattered looked exactly like
// one whose sequence did not.
//
// Operator, 2026-07-25: "they still 'act' like a checkbox but now also assigned a number value
// that decides what order each one is applied."
//
// THE GESTURES, and why these:
//   * click an unpicked row      -> it takes the next number (this is the checkbox part)
//   * click a picked number      -> ARM it (nothing moves yet; you have said "this one")
//   * click a different number   -> the two SWAP places, disarm
//   * click the armed one again  -> unpick it, disarm
//   * Clear                      -> everything back to unpicked
//
// Swapping rather than re-assigning is the whole point: reordering must not mean undoing every
// pick and redoing it in a new sequence. And the second click on an armed pick has to mean
// something — "put it back" is the only reading that does not strand the operator in a mode.
//
// Removing a pick COMPACTS the rest (4 picks, drop #2, and you hold 1·2·3 — never 1·3·4),
// because the number is a position in a queue, not a name. That falls out of storing the order
// as an array and rendering index+1.
//
// The order that comes out of here is the order that goes to /choose, which enqueues in the order
// it receives — so #1 is the application the queue works first. The numbers are not decoration.

// ONE atomic state, not two. The order and the armed pick always change together, so holding
// them as separate useStates meant every gesture read BOTH from a closure — and a closure is a
// snapshot. Three clicks inside one React batch all saw the same empty `picks` and the last one
// won: two picks silently dropped (measured while building this, 2026-07-25). A human clicking at
// human speed re-renders between clicks and never sees it, which is exactly what makes it the
// kind of bug that ships. A reducer gets the live state every time, batched or not.
function reduce(state, action) {
  const { picks, armed } = state;

  if (action.type === "clear") return { picks: [], armed: null };

  // Swap in a draft read back from storage (a scope change — turning the page).
  if (action.type === "load") return action.state;

  if (action.type === "retain") {
    const live = new Set(action.jobIds);
    const kept = picks.filter((x) => live.has(x));
    const nextArmed = armed && live.has(armed) ? armed : null;
    // Identity matters: returning a new object every ping would re-render the whole panel every
    // 5 seconds for nothing.
    if (kept.length === picks.length && nextArmed === armed) return state;
    return { picks: kept, armed: nextArmed };
  }

  if (action.type !== "pick") return state;
  const { jobId } = action;
  const at = picks.indexOf(jobId);

  if (at === -1) return { picks: [...picks, jobId], armed: null };   // take the next number
  if (armed === null) return { picks, armed: jobId };                // pick it up
  if (armed === jobId) {                                             // put it back
    return { picks: picks.filter((x) => x !== jobId), armed: null };
  }
  const from = picks.indexOf(armed);
  if (from === -1) return { picks, armed: jobId };                   // armed one vanished
  const next = [...picks];                                           // swap the two
  next[from] = jobId;
  next[at] = armed;
  return { picks: next, armed: null };
}

// THE DRAFT SURVIVES THE PANEL. Picks used to live only in component state, so clicking out of
// the picker — another tab, a stray click, an unmount — silently threw away a selection the
// operator had spent real attention on. Operator, 2026-07-26: "that menu is our selector for that
// particular page so whatever we select needs to persist."
//
// Scoped to (session, page) because that is exactly what a selection IS: page 2's picks are not
// page 1's, and reopening page 1 must show page 1's draft rather than whatever was chosen last.
//
// localStorage, not the server, and the line is deliberate: an unsubmitted draft is not a
// decision yet. It becomes one — journaled, with a decider, on the `select:N` rung — the moment
// "Take" is pressed. Persisting a draft server-side would put half-made choices in the record.
const KEY = (scope) => `sc.picks.${scope || "none"}`;

function load(scope) {
  try {
    const raw = window.localStorage.getItem(KEY(scope));
    const picks = raw ? JSON.parse(raw) : [];
    return { picks: Array.isArray(picks) ? picks.filter((x) => typeof x === "string") : [],
             armed: null };
  } catch {
    return { picks: [], armed: null };   // a corrupt draft is an empty one, never a crash
  }
}

export function useOrderedPicks(scope) {
  const [state, dispatch] = useReducer(reduce, scope, load);

  // Re-read when the scope changes — turning to page 2 must not inherit page 1's draft.
  const seen = useRef(scope);
  useEffect(() => {
    if (seen.current !== scope) {
      seen.current = scope;
      dispatch({ type: "load", state: load(scope) });
    }
  }, [scope]);

  // Write through on every change. Cheap (a short array of ids) and it is the whole point.
  useEffect(() => {
    try {
      if (state.picks.length) {
        window.localStorage.setItem(KEY(scope), JSON.stringify(state.picks));
      } else {
        window.localStorage.removeItem(KEY(scope));
      }
    } catch { /* a browser refusing storage must not break picking */ }
  }, [scope, state.picks]);

  // `dispatch` is stable, so these never go stale and never re-make themselves.
  const pick = useCallback((jobId) => dispatch({ type: "pick", jobId }), []);
  const clear = useCallback(() => dispatch({ type: "clear" }), []);
  const retain = useCallback((jobIds) => dispatch({ type: "retain", jobIds }), []);

  return useMemo(() => ({ picks: state.picks, armed: state.armed, pick, clear, retain }),
                 [state, pick, clear, retain]);
}
