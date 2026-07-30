import { AppIcon } from "../../../ui/Icon";

// THE FILL PLAN — every field we would type into, what we would put in it, and where that value
// came from, BEFORE anything is typed.
//
// The API has planned this since apply_fill was written; nothing ever rendered it, so the only way
// to see what the system was about to put on a real employer's form was to read a JSON blob. That
// is the wrong place for a decision the operator owns.
//
// Two rules this card exists to keep visible:
//   * PROVENANCE TRAVELS WITH THE VALUE. "Nashua" from your stored answers and "Nashua" derived
//     from the account identity are different claims, and only one of them is something you said.
//   * A BLANK IS A REAL ANSWER. A field we hold no data for is shown as missing and stays empty —
//     never an invented street, never a plausible-looking guess. Missing is the honest output.
//
// Filling is not submitting. Nothing here sends an application; the submit stays a separate,
// deliberate press elsewhere, because that is the one that cannot be taken back.

const SOURCE_COPY = {
  working_variable: { label: "derived", tone: "muted", why: "computed at fill time (e.g. today's date)" },
  stored: { label: "your answer", tone: "ready", why: "from the answers you have saved" },
  identity: { label: "account", tone: "accent", why: "from the account identity we applied under" },
  missing: { label: "no data", tone: "warn", why: "we hold nothing for this — it stays blank" },
  skip: { label: "skipped", tone: "muted", why: "deliberately not filled" },
};

export default function FillPlan({ plan, summary, busy, onPlan, onFill }) {
  if (!plan) {
    return (
      <div className="cv-actions">
        <button className="btn btn-sm" disabled={busy} onClick={onPlan}
                title="Read the open form and show what would be typed — types nothing">
          Plan the fill
        </button>
      </div>
    );
  }

  const missing = summary?.missing || [];
  // Text fields are what the bunch pass actually types; dropdowns need the widget protocol and are
  // left for a targeted rung. Saying so here stops "it filled 9 of 13" reading as a failure.
  const selects = plan.filter((r) => r.widget === "select" && r.fillable);

  return (
    <div className="sc-login">
      <div className="sc-login__head">
        <AppIcon name="listTree" size={14} /> Fill plan
        <span className={`badge badge--${summary?.fillable ? "ready" : "muted"}`}>
          {summary?.fillable ?? 0} of {summary?.total ?? plan.length} ready
        </span>
      </div>

      {plan.length === 0 && (
        <p className="rung__meta">
          Nothing recognised on the page. If the form is an accordion, check the sections above —
          a closed section's fields are not on the page to be found.
        </p>
      )}

      {plan.length > 0 && (
        <ul className="rungs">
          {plan.map((r) => {
            const src = SOURCE_COPY[r.source] || { label: r.source, tone: "muted", why: "" };
            return (
              <li key={r.field} className={`rung rung--${r.fillable ? "held" : "pending"}`}>
                <div className="rung__body">
                  <div className="rung__line">
                    <span className="rung__label">{r.field}</span>
                    <span className={`badge badge--${src.tone}`} title={src.why}>{src.label}</span>
                    {r.widget === "select" && (
                      <span className="badge badge--muted"
                            title="A dropdown — the bunch fill does text only; this one needs its own step">
                        dropdown
                      </span>
                    )}
                  </div>
                  <div className="rung__meta">
                    {r.fillable
                      ? <code>{r.value}</code>
                      : <em>left blank — we hold no {r.answer_key?.replace(/_/g, " ")}</em>}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {missing.length > 0 && (
        <p className="cv-blocked">
          Needs you for {missing.length}: {missing.join(", ")}. These stay empty rather than guessed.
        </p>
      )}
      {selects.length > 0 && (
        <p className="rung__meta">
          {selects.length} dropdown{selects.length === 1 ? "" : "s"} won't be touched by the bunch
          fill — they commit through their own widget step.
        </p>
      )}

      <div className="cv-actions">
        <button className="btn btn-sm" disabled={busy} onClick={onPlan}
                title="Re-read the form and re-plan — types nothing">
          Re-plan
        </button>
        <button className="btn btn-sm btn-primary" disabled={busy || !summary?.fillable}
                title="Types the ready text fields at a human pace. Does NOT submit anything."
                onClick={onFill}>
          Fill the {summary?.fillable ?? 0} ready field(s)
        </button>
      </div>
    </div>
  );
}
