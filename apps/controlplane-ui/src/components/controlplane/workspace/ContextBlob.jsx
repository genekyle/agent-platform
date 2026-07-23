import { AppIcon } from "../../../ui/Icon";

// The context blob — the system reasoning out loud.
//
// Operator-directed 2026-07-23: "I need to know what its thoughts or intent or process are, more
// open and forward… where it thinks it is in the current task, what it should be doing, what the
// observer thinks, what the new page is asking for."
//
// Almost all of this ALREADY EXISTED and was simply never rendered. The parked request carries
// ~1,900 characters of frozen, human-readable serialisation — bundle_prompt, belief_prompt,
// authority_prompt — and the pane was showing four fields out of it. The observer in particular
// was invisible: on the very first drive this was opened against, it believed a work-experience
// form was a SUBMITTED application, the two witnesses disagreed with each other, and novelty read
// 0.97 — none of which reached the operator, who was being asked to approve an action on that
// basis.
//
// So this is deliberately a READER, not a new data pipeline: it parses the same frozen prompts the
// reasoner itself consumed. One source of truth, and what the operator sees is literally what the
// policy saw. Where a section is genuinely absent from the system rather than merely unrendered —
// the supervisor's verdict before an action, task-level progress — it says so in those words,
// because a blank panel and a missing capability must not look alike.

function section(prompt, name) {
  if (!prompt) return "";
  const re = new RegExp(`# ${name}\\n([\\s\\S]*?)(?=\\n# |$)`);
  return (prompt.match(re)?.[1] || "").trim();
}

function fieldOf(block, key) {
  const line = (block || "").split("\n").find((l) => l.trim().startsWith(`${key}:`));
  return line ? line.split(":").slice(1).join(":").trim() : "";
}

// "unsure: state=0.94 element=1.00 …" -> [[axis, value], …]
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

const AXIS_MEANING = {
  state: "which page this is",
  element: "which control to touch",
  answer: "what value to give",
  effect: "what an action will do",
  novelty: "whether we have ever been anywhere like this",
};

export function ContextBlob({ req }) {
  if (!req) return null;

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
  const beliefState = fieldOf(belief, "state");
  const recipeState = fieldOf(state, "state");
  const why = fieldOf(belief, "why");
  const disagree = beliefState && recipeState && beliefState !== recipeState;

  return (
    <div className="blob">
      <div className="blob__head">
        <AppIcon name="sparkle" size={15} /> Context — what it is thinking
      </div>

      {/* 1. WHERE IT THINKS IT IS — two independent answers, and whether they agree */}
      <div className="blob__section">
        <div className="blob__label">Where it thinks it is</div>
        <div className="blob__rows">
          <div>
            <span className="blob__k">the recipe says</span>
            <span className="blob__v">{recipeState || "(unrecognised)"}</span>
          </div>
          <div>
            <span className="blob__k">the observer says</span>
            <span className="blob__v">{beliefState || "(no witness)"}</span>
          </div>
        </div>
        {disagree && (
          <div className="blob__flag">
            These disagree. The recipe classifies by URL; the observer classifies by what is on the
            page. When they split, one of them is wrong about the page you are looking at.
          </div>
        )}
        {why && <div className="blob__why">{why}</div>}
      </div>

      {/* 2. HOW SURE, PER AXIS — one number per kind of doubt, never one collapsed score */}
      {axes(belief).length > 0 && (
        <div className="blob__section">
          <div className="blob__label">How unsure, and about what</div>
          <div className="blob__axes">
            {axes(belief).map(([k, v]) => (
              <div key={k} className="blob__axis" title={AXIS_MEANING[k] || ""}>
                <span className="blob__k">{k}</span>
                <span className="blob__bar">
                  <span
                    className={`blob__bar-fill${v >= 0.9 ? " blob__bar-fill--hot" : ""}`}
                    style={{ width: `${Math.round(v * 100)}%` }}
                  />
                </span>
                <span className="blob__num">{v.toFixed(2)}</span>
              </div>
            ))}
          </div>
          <div className="blob__hint">
            1.00 means no idea. These are separate on purpose — being unsure which page this is
            (fine, ask) is not the same as being unsure what value to type (never guess).
          </div>
        </div>
      )}

      {/* 3. THE WITNESSES, individually */}
      {witnesses(belief).length > 0 && (
        <div className="blob__section">
          <div className="blob__label">What each witness saw</div>
          {witnesses(belief).map((w, i) => (
            <div key={i} className="blob__witness">{w}</div>
          ))}
        </div>
      )}

      {/* 4. THE TASK — goal, what the recipe expects next, where it can land */}
      <div className="blob__section">
        <div className="blob__label">The task</div>
        <div className="blob__rows">
          <div><span className="blob__k">goal</span><span className="blob__v">{fieldOf(goal, "goal") || "—"}</span></div>
          <div><span className="blob__k">complete?</span><span className="blob__v">{fieldOf(goal, "done") || "—"}</span></div>
          <div><span className="blob__k">recipe step</span><span className="blob__v">{fieldOf(recipe, "step") || "—"}</span></div>
          <div><span className="blob__k">should do next</span><span className="blob__v">{fieldOf(recipe, "next_action") || "—"}</span></div>
          <div><span className="blob__k">could land on</span><span className="blob__v">{fieldOf(recipe, "expected_next") || "—"}</span></div>
        </div>
        <div className="blob__gap">
          Not yet modelled: how far through the whole application this is. The recipe knows the
          next step, nothing tracks progress across the task.
        </div>
      </div>

      {/* 5. WHAT THE PAGE WANTS — the machine-readable half, and an honest note about the rest */}
      <div className="blob__section">
        <div className="blob__label">What this page is asking for ({unansweredCount} required)</div>
        <pre className="blob__pre">{unansweredBlock.trim() || "(nothing required, or nothing scannable)"}</pre>
        <div className="blob__gap">
          This is only what the form MARKS as required. A page that states its ask in prose — “we
          couldn’t pull any work experience from your resume” — reads as zero required fields, which
          is how a drive nearly continued past an empty section.
        </div>
      </div>

      {/* 6. WHAT IT INTENDS, AND ON WHAT EVIDENCE */}
      <div className="blob__section">
        <div className="blob__label">What it intends to do</div>
        <code className="blob__code">
          {pred.intent || "—"} {pred.params ? JSON.stringify(pred.params) : ""}
        </code>
        {pred.rationale && <div className="blob__why">{pred.rationale}</div>}
        <div className="blob__rows">
          <div><span className="blob__k">confidence</span><span className="blob__v">{pred.confidence ?? "—"}</span></div>
          <div><span className="blob__k">decided by</span><span className="blob__v">{pred.rung || "—"}</span></div>
          {pred.escalation_axis && (
            <div><span className="blob__k">stuck on</span><span className="blob__v">{pred.escalation_axis}</span></div>
          )}
          {pred.evidence?.length ? (
            <div><span className="blob__k">evidence</span><span className="blob__v">{pred.evidence.join(", ")}</span></div>
          ) : null}
        </div>
      </div>

      {/* 7. WHO OWNS THE TURN */}
      {req.authority_prompt && (
        <div className="blob__section">
          <div className="blob__label">Who owns this turn</div>
          <pre className="blob__pre">{req.authority_prompt.replace(/^# AUTHORITY\n/, "")}</pre>
        </div>
      )}

      {/* 8. WHAT IT JUST DID */}
      {recent && (
        <div className="blob__section">
          <div className="blob__label">What it just did</div>
          <pre className="blob__pre">{recent}</pre>
        </div>
      )}

      {/* 9. THE DIAGNOSIS — absent, and saying so beats an empty box */}
      <div className="blob__section">
        <div className="blob__label">Diagnosis</div>
        {req.verdict_prompt ? (
          <pre className="blob__pre">{req.verdict_prompt}</pre>
        ) : (
          <div className="blob__gap">
            None. The supervisor only runs AFTER an action, so a turn parked before acting has no
            diagnosis — the one place a verdict would be most useful is the one place it does not
            exist yet.
          </div>
        )}
      </div>

      {windowB && (
        <div className="blob__section">
          <div className="blob__label">The window</div>
          <pre className="blob__pre">{windowB}</pre>
        </div>
      )}

      <details className="blob__raw">
        <summary>Exactly what the policy read</summary>
        <pre className="blob__pre">{bp}</pre>
        <pre className="blob__pre">{belief}</pre>
      </details>
    </div>
  );
}
