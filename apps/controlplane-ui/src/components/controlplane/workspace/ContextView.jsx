import { AppIcon } from "../../../ui/Icon";

// ContextView — the system reasoning out loud, as an inline panel.
//
// The right pane of the Live drive tab. A READER, not a new pipeline: every section is parsed out
// of the same frozen prompts the reasoner itself consumed (bundle_prompt, belief_prompt,
// authority_prompt), so what the operator sees is literally what the policy saw. Where a section
// is absent from the SYSTEM rather than merely unrendered, it says so in those words — a missing
// capability and an empty panel must not look alike.
//
// Presentational only: no fetching, no drive state. The Live panel owns the queue, the selection
// and the write-back controls; this just renders the selected request.

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
  return line.split(/\s+/).map((p) => p.split("=")).filter((p) => p.length === 2).map(([k, v]) => [k, parseFloat(v)]);
}
function witnesses(beliefPrompt) {
  return (beliefPrompt || "").split("\n").filter((l) => l.trim().startsWith("- ")).map((l) => l.trim().slice(2));
}

function Gap({ children }) {
  return (
    <p className="cv-gap">
      <AppIcon name="alert" size={13} /> {children}
    </p>
  );
}

export function ContextView({ req }) {
  if (!req) {
    return (
      <div className="cv cv--empty">
        <AppIcon name="waypoints" size={22} />
        <p>Select a parked drive to see where it thinks it is, how sure it is, and what it wants to do.</p>
      </div>
    );
  }

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

  return (
    <div className="cv">
      {/* HERO — the two independent answers to "where are we" */}
      <section className="cv-hero">
        <div className="cv-hero__col">
          <div className="cv-hero__label">The recipe says</div>
          <div className="cv-hero__val">{recipeState || "(unrecognised)"}</div>
          <div className="cv-hero__by">by the URL</div>
        </div>
        <div className={`cv-hero__vs${disagree ? " cv-hero__vs--split" : ""}`}>{disagree ? "≠" : "="}</div>
        <div className="cv-hero__col">
          <div className="cv-hero__label">The observer says</div>
          <div className="cv-hero__val">{beliefState || "(no witness)"}</div>
          <div className="cv-hero__by">by what's on the page</div>
        </div>
      </section>
      {disagree && (
        <Gap>
          These disagree — and when they do, one of them is wrong about the page in front of you.
          Read the witnesses before approving anything.
        </Gap>
      )}
      {why && <p className="cv-note">{why}</p>}

      {axes(belief).length > 0 && (
        <section className="cv-sec">
          <h4 className="cv-sec__h">How unsure, and about what</h4>
          <div className="cv-axes">
            {axes(belief).map(([k, v]) => (
              <div key={k} className="cv-axis" title={AXIS_MEANING[k] || ""}>
                <span className="cv-axis__k">{k}</span>
                <span className="cv-axis__bar">
                  <span className={`cv-axis__fill${v >= 0.9 ? " cv-axis__fill--hot" : ""}`} style={{ width: `${Math.round(v * 100)}%` }} />
                </span>
                <span className="cv-axis__n">{v.toFixed(2)}</span>
              </div>
            ))}
          </div>
          <p className="cv-hint">
            1.00 = no idea. Separate on purpose: unsure <em>which page</em> (fine, ask) is not unsure{" "}
            <em>what to type</em> (never guess).
          </p>
        </section>
      )}

      {witnesses(belief).length > 0 && (
        <section className="cv-sec">
          <h4 className="cv-sec__h">What each witness saw</h4>
          <div className="cv-witnesses">
            {witnesses(belief).map((w, i) => <div key={i} className="cv-witness">{w}</div>)}
          </div>
        </section>
      )}

      <section className="cv-sec">
        <h4 className="cv-sec__h">The task</h4>
        <dl className="cv-dl">
          <dt>goal</dt><dd>{fieldOf(goal, "goal") || "—"}</dd>
          <dt>complete?</dt><dd>{fieldOf(goal, "done") || "—"}</dd>
          <dt>recipe step</dt><dd>{fieldOf(recipe, "step") || "—"}</dd>
          <dt>should do next</dt><dd>{fieldOf(recipe, "next_action") || "—"}</dd>
          <dt>could land on</dt><dd>{fieldOf(recipe, "expected_next") || "—"}</dd>
        </dl>
        <Gap>
          Not yet modelled: how far through the whole application this is. The recipe knows the next
          step; nothing tracks progress across the task.
        </Gap>
      </section>

      <section className="cv-sec">
        <h4 className="cv-sec__h">What this page is asking for · {unansweredCount} required</h4>
        <pre className="cv-pre">{unansweredBlock.trim() || "(nothing required, or nothing scannable)"}</pre>
        <Gap>
          Only what the form <em>marks</em> required. A page that states its ask in prose — "we
          couldn't pull any work experience from your resume" — reads as zero required fields, which
          is how a drive nearly continued past an empty section.
        </Gap>
      </section>

      <section className="cv-sec">
        <h4 className="cv-sec__h">What it intends to do</h4>
        <code className="cv-code">{pred.intent || "—"} {pred.params ? JSON.stringify(pred.params) : ""}</code>
        {pred.rationale && <p className="cv-note">{pred.rationale}</p>}
        <dl className="cv-dl">
          <dt>confidence</dt><dd>{pred.confidence ?? "—"}</dd>
          <dt>decided by</dt><dd>{pred.rung || "—"}</dd>
          {pred.escalation_axis && (<><dt>stuck on</dt><dd>{pred.escalation_axis}</dd></>)}
          {pred.evidence?.length ? (<><dt>evidence</dt><dd>{pred.evidence.join(", ")}</dd></>) : null}
        </dl>
      </section>

      <section className="cv-sec">
        <h4 className="cv-sec__h">Diagnosis</h4>
        {req.verdict_prompt ? (
          <pre className="cv-pre">{req.verdict_prompt}</pre>
        ) : (
          <Gap>
            None. The supervisor runs only <em>after</em> an action, so a turn parked before acting
            has no diagnosis — the one place a verdict would help most is the one place it doesn't
            exist yet.
          </Gap>
        )}
      </section>

      {req.authority_prompt && (
        <details className="cv-more">
          <summary>Who owns this turn</summary>
          <pre className="cv-pre">{req.authority_prompt.replace(/^# AUTHORITY\n/, "")}</pre>
        </details>
      )}
      {recent && (
        <details className="cv-more">
          <summary>What it just did</summary>
          <pre className="cv-pre">{recent}</pre>
        </details>
      )}
      {windowB && (
        <details className="cv-more">
          <summary>The window, as it read it</summary>
          <pre className="cv-pre">{windowB}</pre>
        </details>
      )}
      <details className="cv-more">
        <summary>Exactly what the policy read</summary>
        <pre className="cv-pre">{bp}</pre>
        <pre className="cv-pre">{belief}</pre>
      </details>
    </div>
  );
}
