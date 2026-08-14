import { useState } from "react";
import { AppIcon } from "../../../ui/Icon";

// THE FORM AS IT STANDS — the scanner's own census of this page's required fields, answered rows
// included, each unanswered one carrying its controller verb.
//
// This card exists because of the 2026-08-10 operator audit: mid-application, the cockpit's only
// word for a form was a fill-plan COUNT ("0 of 0 ready") — the planner drops fields it holds no
// answer_key for, the scanner returned only the unanswered names, and the one endpoint that can
// act any form verb (`/apply_teach`) had no surface at all. So the operator stood before a live
// screener with no way to see it and no way to answer it, while every capability existed behind
// an endpoint. The census renders what the scanner read; the verbs go through `/apply_teach`, so
// every press is validated against the intent vocabulary, journaled with its rationale, and
// recorded on the apply step — cockpit work IS corpus work.
//
// Two rules:
//   * ANSWERED IS NOT DONE. A filled field can be filled wrong (the "Are you an Active
//     Employee → Yes" near-self-withdrawal) — answered rows render with their values so a wrong
//     answer is visible, and re-answering an answered choice is allowed.
//   * NOTHING ACTS UNARMED. A choice chip or typed answer ARMS a confirm row showing exactly
//     what will be taught, with the rationale editable — one deliberate press acts, on a live
//     page, never a stray click.

const KIND_COPY = {
  radio_group: "pick one",
  checkbox_group: "check",
  react_select: "dropdown",
  select: "dropdown",
  textarea: "long answer",
  input: "answer",
};

//: A value no real option list contains — `/select_option` answers it `no_option` and returns the
//: widget's own enumerated choices (the working probe from the 2026-08-10 attended drive, now a
//: button instead of a hand-typed curl).
const ENUMERATE_SENTINEL = "(list the options)";

function intentFor(row, value) {
  const kind = row.kind || "";
  if (kind === "radio_group" || kind === "checkbox_group") {
    return { intent: "check_group", params: { field: row.field, values: [value] } };
  }
  if (kind === "react_select" || kind === "select") {
    return { intent: "select_option", params: { field: row.field, value } };
  }
  return { intent: "set_text", params: { field: row.field, value } };
}

// A ROW'S IDENTITY IS NOT ITS NAME. Two rows on one form can carry the same name — Boston
// Children's uploader censused twice, once as "CHOOSE A FILE" and once from its own helper text —
// and both lists here keyed on `field` alone. React said what that costs out loud ("children may
// be duplicated and/or omitted"), and an OMITTED row on a census that gates the Submit is a
// required field the operator never sees. The `armed` comparison had the same fault from the same
// cause: arming one row lit up every row sharing its name.
//
// The selector is the structural identity the census already mints; the index is the fallback for
// rows that have none, and it is stable because the list order is the walk order.
// Scoped by LIST too: the answered and unanswered lists each restart their index, so an
// unscoped key can collide across them.
const rowKey = (r, i, list) => `${list}|${r.selector || ""}|${r.field || ""}|${i}`;

function Row({ row, busy, onArm, armed, rowId }) {
  const [typed, setTyped] = useState("");
  const kindWord = KIND_COPY[row.kind] || row.kind || "answer";
  const canEnumerate = row.kind === "react_select" && !(row.options || []).length;
  // A CAPPED LIST OFFERED AS THE WHOLE LIST. The census ships at most 24 options — right, nobody
  // needs 250 strings in a payload — and this surface rendered them as the complete set of
  // buttons, with the type-it box appearing ONLY when there were no options at all. So a
  // truncated dropdown was a wall: 24 choices and no way to reach the rest. Measured live
  // 2026-08-14 on Boston Children's, where Country ran to ~250 and Area of Interest to 32.
  // Absence from a sample is not absence (interaction.measured), so the count is stated and the
  // free-text path stays open whenever the list is short of the page's own total.
  const truncated = !!row.options_truncated;
  return (
    <li className={`rung rung--${row.answered ? (row.valid ? "held" : "pending") : "pending"}`}>
      <div className="rung__body">
        <div className="rung__line">
          <span className="rung__label">{row.field}</span>
          <span className="badge badge--muted">{kindWord}</span>
          {row.answered && !row.valid && (
            <span className="badge badge--warn"
                  title="The page marks this filled value invalid — it will block the advance">
              filled but invalid
            </span>
          )}
          {!row.answered && (
            <span className="badge badge--warn">unanswered</span>
          )}
          {row.required_via === "none" && (
            <span className="badge badge--muted"
                  title="The form calls this voluntary — it never blocks the required-fields gate, but some pages still refuse to advance until an explicit choice (declining counts) is made">
              voluntary
            </span>
          )}
        </div>
        {row.answered && row.value_preview && (
          <div className="rung__meta"><code>{row.value_preview}</code></div>
        )}
        {!armed && (
          <div className="rung__meta form-census__verbs">
            {(row.options || []).map((opt) => (
              <button key={opt} className="btn btn-sm btn-ghost" disabled={busy}
                      aria-label={`${row.field}: ${opt}`}
                      title={`Teach: answer “${row.field}” with “${opt}” — journaled, reversible`}
                      onClick={() => onArm(row, opt, rowId)}>
                {opt}
              </button>
            ))}
            {canEnumerate && (
              <button className="btn btn-sm btn-ghost" disabled={busy} aria-label="List choices"
                      title="This dropdown only shows its choices while open — probe it with a value that cannot match; its refusal carries the real option list"
                      onClick={() => onArm(row, ENUMERATE_SENTINEL, rowId)}>
                List choices
              </button>
            )}
            {truncated && (
              <span className="badge badge--warn"
                    title={`This page offers ${row.option_count} choices and the census carries `
                      + `at most ${(row.options || []).length}. The ones not shown are not absent `
                      + `— type the exact option instead; the widget is read live when it acts.`}>
                {(row.options || []).length} of {row.option_count} shown
              </span>
            )}
            {((!(row.options || []).length && !canEnumerate) || truncated) && (
              <span className="form-census__type">
                <input value={typed} disabled={busy}
                       placeholder={truncated ? "or type the exact option" : "type the answer"}
                       onChange={(e) => setTyped(e.target.value)} />
                <button className="btn btn-sm btn-ghost" disabled={busy || !typed.trim()}
                        onClick={() => { onArm(row, typed.trim(), rowId); setTyped(""); }}>
                  Answer
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export default function FormCensus({ census, busy, taught, onTeach, onReread }) {
  const [armedAct, setArmedAct] = useState(null);   // {row, value, rationale}
  if (!census) return null;
  const unanswered = census.unanswered || [];
  const answered = census.answered || [];

  const arm = (row, value, key) => setArmedAct({
    row, value, key,
    rationale: value === ENUMERATE_SENTINEL
      ? `probe to enumerate the choices of “${row.field}” — a closed listbox only shows them open`
      : `operator answered “${row.field}” with “${value}” from the cockpit form census`,
  });
  const teach = () => {
    const { row, value, rationale } = armedAct;
    const act = intentFor(row, value);
    onTeach(act.intent, act.params, rationale);
    setArmedAct(null);
  };

  return (
    <div className="sc-login form-census">
      <div className="sc-login__head">
        <AppIcon name="listTree" size={14} /> The form as it stands
        <span className={`badge badge--${unanswered.length ? "warn" : "ready"}`}>
          {unanswered.length ? `${unanswered.length} unanswered` : "all answered"}
        </span>
        {answered.length > 0 && (
          <span className="badge badge--muted">{answered.length} answered</span>
        )}
      </div>

      {unanswered.length === 0 && answered.length === 0 && (
        <p className="rung__meta">The scanner found no required fields on this page.</p>
      )}

      {unanswered.length > 0 && (
        <ul className="rungs">
          {unanswered.map((r, i) => (
            <Row key={rowKey(r, i, "u")} row={r} busy={busy} rowId={rowKey(r, i, "u")}
                 onArm={arm} armed={armedAct?.key === rowKey(r, i, "u")} />
          ))}
        </ul>
      )}

      {armedAct && (
        <div className="cv-correct form-census__confirm">
          <p className="rung__meta">
            Teach <code>{intentFor(armedAct.row, armedAct.value).intent}</code> ·{" "}
            <strong>{armedAct.row.field}</strong> ={" "}
            <code>{armedAct.value}</code> — acts on the live page, journaled with the reason.
          </p>
          <textarea className="work-note" rows={2} value={armedAct.rationale}
                    onChange={(e) => setArmedAct((a) => ({ ...a, rationale: e.target.value }))} />
          <div className="work__actions">
            <button className="btn btn-sm btn-primary" disabled={busy || !armedAct.rationale.trim()}
                    onClick={teach}>
              Do it
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => setArmedAct(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {taught && (
        <p className={taught.outcome === "ok" || taught.held ? "rung__meta" : "cv-blocked"}>
          {taught.held ? "Held for you (consequential gate)." :
            `Taught ${taught.intent || "the action"} → ${taught.outcome || "no outcome"}`}
          {taught.landed_state ? ` · landed on ${taught.landed_state}` : ""}
          {taught.detail ? ` — ${taught.detail}` : ""}
        </p>
      )}

      {answered.length > 0 && (
        <details className="work__more">
          <summary>The {answered.length} answered field(s) — check them, a filled answer can
            still be the wrong one</summary>
          <ul className="rungs">
            {answered.map((r, i) => (
              <Row key={rowKey(r, i, "a")} row={r} busy={busy} rowId={rowKey(r, i, "a")}
                   onArm={arm} armed={armedAct?.key === rowKey(r, i, "a")} />
            ))}
          </ul>
        </details>
      )}

      <div className="cv-actions">
        <button className="btn btn-sm" disabled={busy} onClick={onReread} aria-label="Re-read the form"
                title="Re-scan the live form — reads, types nothing">
          Re-read the form
        </button>
      </div>
    </div>
  );
}
