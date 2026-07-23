import { useEffect } from "react";
import { AppIcon } from "../../../ui/Icon";

// The context drawer — the system reasoning out loud, in its own window.
//
// Operator-directed 2026-07-23: the information was right, the presentation ("a blob") was not,
// and "this needs its own window." So the full picture slides in from the side with room to
// breathe and an actual layout, while the Coaching pane stays a compact queue.
//
// A READER, not a new pipeline: every section is parsed out of the same frozen prompts the
// reasoner itself consumed (bundle_prompt, belief_prompt, authority_prompt), so what the operator
// sees is literally what the policy saw. Where a section is absent from the SYSTEM rather than
// merely unrendered, it says so in those words — a missing capability and an empty panel must
// not look alike.

const MODE_TONE = { green: "ok", yellow: "warn", orange: "warn", red: "bad" };

const AXIS_MEANING = {
  state: "which page this is",
  element: "which control to touch",
  answer: "what value to give",
  effect: "what an action will do",
  novelty: "whether we have ever been anywhere like this",
};

function section(prompt, name) {
  if (!prompt) return "";
  const re = new RegExp(`# ${name}\\n([\\s\\S]*?)(?=\\n# |$)`);
  return (prompt.match(re)?.[1] || "").trim();
}
function fieldOf(block, key) {
  const line = (block || "").split("\n").find((l) => l.trim().startsWith(`${key}:`));
  return line ? line.split(":").slice(1).join(":").trim() : "";
}
function axes(beliefPrompt) {
  const line = fieldOf(beliefPrompt, "unsure");
  if (!line) return [];
  return line
    .split(/\s+/)
    .map((p) => p.split("="))
    .filter((p) => p.length === 2)
    .map(([k, v]) => [k, parseFloat(v)]);
}
function witnesses(beliefPrompt) {
  return (beliefPrompt || "")
    .split("\n")
    .filter((l) => l.trim().startsWith("- "))
    .map((l) => l.trim().slice(2));
}

function Gap({ children }) {
  return (
    <p className="cd-gap">
      <AppIcon name="alert" size={13} /> {children}
    </p>
  );
}

export function ContextDrawer({
  req,
  busy,
  blocked,
  note,
  draft,
  error,
  onNote,
  onStartCorrect,
  onDraft,
  onCancelCorrect,
  onGo,
  onCorrect,
  onStop,
  onClose,
}) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const bp = req.bundle_prompt || "";
  const belief = req.belief_prompt || "";
  const goal = section(bp, "GOAL");
  const state = section(bp, "STATE");
  const recipe = section(bp, "RECIPE");
  const recent = section(bp, "RECENT");
  const windowB = section(bp, "WINDOW");
  const unansweredBlock = (bp.match(/# UNANSWERED \(\d+\)\n([\s\S]*?)(?=\n# |$)/) || [])[1] || "";
  const unansweredCount = (bp.match(/# UNANSWERED \((\d+)\)/) || [])[1] || "0";
  const pred = req.prediction || {};
  const recipeState = fieldOf(state, "state");
  const beliefState = fieldOf(belief, "state");
  const why = fieldOf(belief, "why");
  const disagree = beliefState && recipeState && beliefState !== recipeState;
  const tone = MODE_TONE[req.mode] || "warn";

  return (
    <div className="cd-scrim" onClick={onClose}>
      <aside className="cd" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Context">
        {/* header */}
        <header className="cd__head">
          <div className="cd__head-main">
            <span className={`badge badge--${tone}`}>{req.mode}</span>
            <span className="cd__state">{req.state || "an unrecognised page"}</span>
            {req.maturity ? <span className="badge badge--muted">{req.maturity}</span> : null}
          </div>
          <button className="cd__close" onClick={onClose} title="Close (Esc)">
            <AppIcon name="close" size={18} />
          </button>
        </header>
        <p className="cd__why">{req.authority_reason}</p>

        <div className="cd__body">
          {/* HERO — the two independent answers to "where are we", side by side */}
          <section className="cd-hero">
            <div className="cd-hero__col">
              <div className="cd-hero__label">The recipe says</div>
              <div className="cd-hero__val">{recipeState || "(unrecognised)"}</div>
              <div className="cd-hero__by">by the URL</div>
            </div>
            <div className={`cd-hero__vs${disagree ? " cd-hero__vs--split" : ""}`}>
              {disagree ? "≠" : "="}
            </div>
            <div className="cd-hero__col">
              <div className="cd-hero__label">The observer says</div>
              <div className="cd-hero__val">{beliefState || "(no witness)"}</div>
              <div className="cd-hero__by">by what's on the page</div>
            </div>
          </section>
          {disagree && (
            <Gap>
              These disagree — and when they do, one of them is wrong about the page in front of
              you. Read the witnesses before approving anything.
            </Gap>
          )}
          {why && <p className="cd-note">{why}</p>}

          {/* uncertainty — one bar per axis, never one collapsed score */}
          {axes(belief).length > 0 && (
            <section className="cd-sec">
              <h4 className="cd-sec__h">How unsure, and about what</h4>
              <div className="cd-axes">
                {axes(belief).map(([k, v]) => (
                  <div key={k} className="cd-axis" title={AXIS_MEANING[k] || ""}>
                    <span className="cd-axis__k">{k}</span>
                    <span className="cd-axis__bar">
                      <span
                        className={`cd-axis__fill${v >= 0.9 ? " cd-axis__fill--hot" : ""}`}
                        style={{ width: `${Math.round(v * 100)}%` }}
                      />
                    </span>
                    <span className="cd-axis__n">{v.toFixed(2)}</span>
                  </div>
                ))}
              </div>
              <p className="cd-hint">
                1.00 = no idea. Separate on purpose: unsure <em>which page</em> (fine, ask) is not
                unsure <em>what to type</em> (never guess).
              </p>
            </section>
          )}

          {/* witnesses */}
          {witnesses(belief).length > 0 && (
            <section className="cd-sec">
              <h4 className="cd-sec__h">What each witness saw</h4>
              <div className="cd-witnesses">
                {witnesses(belief).map((w, i) => (
                  <div key={i} className="cd-witness">{w}</div>
                ))}
              </div>
            </section>
          )}

          {/* the task */}
          <section className="cd-sec">
            <h4 className="cd-sec__h">The task</h4>
            <dl className="cd-dl">
              <dt>goal</dt><dd>{fieldOf(goal, "goal") || "—"}</dd>
              <dt>complete?</dt><dd>{fieldOf(goal, "done") || "—"}</dd>
              <dt>recipe step</dt><dd>{fieldOf(recipe, "step") || "—"}</dd>
              <dt>should do next</dt><dd>{fieldOf(recipe, "next_action") || "—"}</dd>
              <dt>could land on</dt><dd>{fieldOf(recipe, "expected_next") || "—"}</dd>
            </dl>
            <Gap>
              Not yet modelled: how far through the whole application this is. The recipe knows the
              next step; nothing tracks progress across the task.
            </Gap>
          </section>

          {/* what the page wants */}
          <section className="cd-sec">
            <h4 className="cd-sec__h">What this page is asking for · {unansweredCount} required</h4>
            <pre className="cd-pre">{unansweredBlock.trim() || "(nothing required, or nothing scannable)"}</pre>
            <Gap>
              Only what the form <em>marks</em> required. A page that states its ask in prose —
              "we couldn't pull any work experience from your resume" — reads as zero required
              fields, which is how a drive nearly continued past an empty section.
            </Gap>
          </section>

          {/* intended action */}
          <section className="cd-sec">
            <h4 className="cd-sec__h">What it intends to do</h4>
            <code className="cd-code">
              {pred.intent || "—"} {pred.params ? JSON.stringify(pred.params) : ""}
            </code>
            {pred.rationale && <p className="cd-note">{pred.rationale}</p>}
            <dl className="cd-dl">
              <dt>confidence</dt><dd>{pred.confidence ?? "—"}</dd>
              <dt>decided by</dt><dd>{pred.rung || "—"}</dd>
              {pred.escalation_axis && (<><dt>stuck on</dt><dd>{pred.escalation_axis}</dd></>)}
              {pred.evidence?.length ? (<><dt>evidence</dt><dd>{pred.evidence.join(", ")}</dd></>) : null}
            </dl>
          </section>

          {/* diagnosis — absent, said plainly */}
          <section className="cd-sec">
            <h4 className="cd-sec__h">Diagnosis</h4>
            {req.verdict_prompt ? (
              <pre className="cd-pre">{req.verdict_prompt}</pre>
            ) : (
              <Gap>
                None. The supervisor runs only <em>after</em> an action, so a turn parked before
                acting has no diagnosis — the one place a verdict would help most is the one place
                it doesn't exist yet.
              </Gap>
            )}
          </section>

          {/* supporting detail, collapsed */}
          {req.authority_prompt && (
            <details className="cd-more">
              <summary>Who owns this turn</summary>
              <pre className="cd-pre">{req.authority_prompt.replace(/^# AUTHORITY\n/, "")}</pre>
            </details>
          )}
          {recent && (
            <details className="cd-more">
              <summary>What it just did</summary>
              <pre className="cd-pre">{recent}</pre>
            </details>
          )}
          {windowB && (
            <details className="cd-more">
              <summary>The window</summary>
              <pre className="cd-pre">{windowB}</pre>
            </details>
          )}
          <details className="cd-more">
            <summary>Exactly what the policy read</summary>
            <pre className="cd-pre">{bp}</pre>
            <pre className="cd-pre">{belief}</pre>
          </details>
        </div>

        {/* sticky footer — write back to the drive */}
        <footer className="cd__foot">
          {error && <div className="coaching-error">{error}</div>}
          {draft ? (
            <div className="cd-correct">
              <input
                placeholder="intent (click / set_text / select_option / submit)"
                value={draft.intent || ""}
                onChange={(e) => onDraft({ intent: e.target.value })}
              />
              <input
                placeholder={'params, e.g. {"field": "Job title *", "value": "…"}'}
                value={draft.params || ""}
                onChange={(e) => onDraft({ params: e.target.value })}
              />
            </div>
          ) : null}
          <textarea
            className="cd-note-in"
            rows={2}
            placeholder="Note / reason — what you know that it doesn't. Rides into the journal. Required to Correct."
            value={note}
            onChange={(e) => onNote(e.target.value)}
          />
          <div className="cd-actions">
            {draft ? (
              <>
                <button className="btn btn-sm btn-primary" onClick={onCorrect} disabled={busy}>
                  {busy ? "…" : "Send correction"}
                </button>
                <button className="btn btn-sm" onClick={onCancelCorrect}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button className="btn btn-sm" onClick={onGo} disabled={busy || !!blocked} title={blocked || "Act on the proposal as-is"}>
                  {busy ? "…" : "Go"}
                </button>
                <button className="btn btn-sm" onClick={onStartCorrect} disabled={busy}>
                  Correct
                </button>
                <button className="btn btn-sm btn-ghost" onClick={onStop} disabled={busy}>
                  Stop
                </button>
              </>
            )}
          </div>
          {blocked && !draft && <p className="cd-blocked">{blocked}</p>}
        </footer>
      </aside>
    </div>
  );
}
