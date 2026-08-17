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

// WHAT THE FIELD ACTUALLY IS, IN THE SCANNER'S OWN WORDS. The census names the control and this
// table decides what the operator is shown; where they disagree, the panel asks for the wrong
// thing in the wrong words.
//
// Operator, 2026-08-16, looking at a required résumé upload: *"it got it right … that it needs a
// file, but it's asking for an 'answer'? … our ui needs to bend with that optionality and
// understand what is it truly going to ask."* The census had read `kind: "file"` correctly and the
// panel then offered to type a sentence into it — and would have emitted `set_text`.
//
// THERE ARE TWO SCANNERS AND THEY NAME KINDS DIFFERENTLY — corrected 2026-08-16 after a first pass
// aligned this table to the wrong one. The census that feeds THIS card is `protocols.py`'s
// `_SCAN_EVERY_DOCUMENT_JS`, and its rule is
//
//     kind: __isReactSelect(el) ? 'react_select' : el.tagName.toLowerCase()
//
// so most kinds are simply TAG NAMES — `button`, `textarea`, `input`, `select` — with
// `checkbox_group`, `radio_group`, `file` and `unknown` set explicitly. `widget_probe.py` has its
// own richer vocabulary (`aria_listbox`, `prompt_hierarchical`, `month_year`, …) for the widget
// PROTOCOL, and those names never reach this census. Replacing the tag names with the probe's
// broke the three rows that had always worked, on a page of sixteen.
//
// So this is the UNION, and the census names come first. A kind that is not here is not broken; it
// degrades to a text box, which is right for text and wrong for everything else.
const KIND_COPY = {
  // --- the census's own vocabulary (protocols.py) --------------------------------------------
  button: "press a button",
  textarea: "long answer",
  input: "answer",
  select: "dropdown",
  react_select: "dropdown",
  checkbox_group: "check",
  radio_group: "pick one",
  file: "attach a file",
  unknown: "unrecognised control",
  // --- widget_probe's protocol vocabulary, harmless here and correct if it ever arrives -------
  native_select: "dropdown",
  aria_listbox: "dropdown",
  prompt_hierarchical: "browse the list",
  segmented_date: "date",
  month_year: "month & year",
  number: "a number",
  text: "answer",
};

//: The kinds whose value is CHOSEN from the page rather than typed into it. They share the
//: enumerate-then-pick affordance, and typing at them is a fallback, not the main road.
const CHOICE_KINDS = new Set(["select", "native_select", "react_select", "aria_listbox",
                              "prompt_hierarchical", "radio_group", "checkbox_group"]);

//: A required "field" whose control is a BUTTON — Eversource asks twelve yes/no questions this way.
//: There is no text to set: the answer is WHICH BUTTON gets pressed, which is the `click` intent.
//: Typing at one taught nothing and was journaled as though it had.
const PRESS_KINDS = new Set(["button"]);

//: A value no real option list contains — `/select_option` answers it `no_option` and returns the
//: widget's own enumerated choices (the working probe from the 2026-08-10 attended drive, now a
//: button instead of a hand-typed curl).
const ENUMERATE_SENTINEL = "(list the options)";

//: A control the scanner could not classify. Asking the page what it is beats typing at it — the
//: executor's vocabulary has carried `describe` all along and nothing could reach it.
const DESCRIBE_SENTINEL = "(describe this control)";

//: A file field is the one kind whose answer we usually ALREADY HOLD, so the panel offers it as a
//: press instead of asking for a path by hand. The PATH comes from the server (`assets.resume_path`
//: — one pointer, reused across every ATS); the UI never carries a filesystem path of its own,
//: because a hard-coded one is wrong the first time the asset moves and silently teaches an upload
//: of nothing.

// THE INTENT IS A PROPERTY OF THE CONTROL, NOT OF THE TYPING. Every branch here already existed in
// the executor's vocabulary (`_INTENT_PARAMS` carries `upload` and `set_date` and has since before
// this card was written); the panel simply had no way to reach them, so a file input and a date
// pair were both taught as `set_text`. That is the "capability exists behind an endpoint with no
// surface" failure this component's own header describes from the 08-10 audit, recurring one layer
// up.
function intentFor(row, value) {
  const kind = row.kind || "";
  if (value === DESCRIBE_SENTINEL) {
    // Not an answer at all — a question back at the page. Only reachable on `unknown`, where
    // typing would be a guess dressed as a teaching example.
    return { intent: "describe", params: { field: row.field } };
  }
  if (kind === "file") {
    // The value IS a path. `upload` takes `path` — typing a sentence at a file input taught
    // nothing and would have been journaled as if it had.
    return { intent: "upload", params: { field: row.field, path: value } };
  }
  if (kind === "month_year" || kind === "segmented_date") {
    // "03/2021" or "March 2021" — split at the boundary the intent declares.
    const parts = String(value).split(/[\s/,-]+/).filter(Boolean);
    const year = parts.find((p) => /^\d{4}$/.test(p)) || "";
    const month = parts.find((p) => p !== year) || "";
    return { intent: "set_date", params: { field: row.field, month, year } };
  }
  if (PRESS_KINDS.has(kind)) {
    // The question IS the control's label and the answer is which button to press, so the value
    // rides as `value` on a `click` — `set_text` at a <button> writes nowhere.
    return { intent: "click", params: { control: row.field, value } };
  }
  if (kind === "radio_group" || kind === "checkbox_group") {
    return { intent: "check_group", params: { field: row.field, values: [value] } };
  }
  if (CHOICE_KINDS.has(kind)) {
    return { intent: "select_option", params: { field: row.field, value } };
  }
  return { intent: "set_text", params: { field: row.field, value } };
}

//: What to put in the box, per kind — the placeholder is the cheapest place to say what the field
//: will accept, and "type the answer" said it wrong for every non-text control on the page.
function placeholderFor(kind, truncated) {
  if (truncated) return "or type the exact option";
  if (PRESS_KINDS.has(kind)) return "which button — e.g. Yes";
  if (kind === "file") return "absolute path to the file";
  if (kind === "month_year" || kind === "segmented_date") return "MM/YYYY";
  if (kind === "number") return "a number";
  if (CHOICE_KINDS.has(kind)) return "type the exact option";
  return "type the answer";
}

//: The verb on the button. "Answer" is right for a question and wrong for an upload or a date.
function verbFor(kind) {
  if (PRESS_KINDS.has(kind)) return "Press";
  if (kind === "file") return "Attach";
  if (kind === "month_year" || kind === "segmented_date") return "Set date";
  if (CHOICE_KINDS.has(kind)) return "Choose";
  return "Answer";
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

function Row({ row, busy, onArm, armed, rowId, resumePath }) {
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
            {row.kind === "file" && resumePath && (
              <button className="btn btn-sm btn-ghost" disabled={busy}
                      title={`Attach the résumé already on file — ${resumePath}`}
                      onClick={() => onArm(row, resumePath, rowId)}>
                Attach the résumé on file
              </button>
            )}
            {((!(row.options || []).length && !canEnumerate) || truncated) && (
              <span className="form-census__type">
                <input value={typed} disabled={busy}
                       placeholder={placeholderFor(row.kind, truncated)}
                       onChange={(e) => setTyped(e.target.value)} />
                <button className="btn btn-sm btn-ghost" disabled={busy || !typed.trim()}
                        onClick={() => { onArm(row, typed.trim(), rowId); setTyped(""); }}>
                  {verbFor(row.kind)}
                </button>
              </span>
            )}
            {row.kind === "unknown" && (
              // A control we could not classify is the one case where the honest first move is to
              // ask the page what it is, rather than to type at it and hope.
              <button className="btn btn-sm btn-ghost" disabled={busy}
                      title="Ask the page to describe this control before answering it"
                      onClick={() => onArm(row, DESCRIBE_SENTINEL, rowId)}>
                Describe this control
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export default function FormCensus({ census, busy, taught, onTeach, onReread, resumePath,
                                     fillable = 0, onAutofill }) {
  const [armedAct, setArmedAct] = useState(null);   // {row, value, rationale}
  if (!census) return null;
  const unanswered = census.unanswered || [];
  const answered = census.answered || [];
  // THE CONTROLS THE PAGE HAS THAT THIS SCREEN DOES NOT REQUIRE. The scanner has always returned
  // them and this card dropped them on the floor, so the panel implied the page held nothing but
  // its required rows — while the operator was looking at an education and work-experience section
  // we have driven before and could not see here (2026-08-16). They are a DISCLOSURE, not the
  // work: collapsed by default, one line when you do not need them, there when you do.
  const optional = census.optional || [];

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
    <div className="sc-login form-census" aria-busy={busy ? "true" : "false"}>
      <div className="sc-login__head">
        <AppIcon name="listTree" size={14} /> The form as it stands
        <span className={`badge badge--${unanswered.length ? "warn" : "ready"}`}>
          {unanswered.length ? `${unanswered.length} unanswered` : "all answered"}
        </span>
        {answered.length > 0 && (
          <span className="badge badge--muted">{answered.length} answered</span>
        )}
        {optional.length > 0 && (
          <span className="badge badge--muted">{optional.length} not required</span>
        )}
      </div>

      {/* THE PAGE IS MOVING AND THIS LIST IS NOT. A census is a photograph: while a step runs, the
          rows on screen describe the page as it WAS, and their buttons still look live — which is
          how a press lands on a row that no longer exists. Say so, rather than letting a stale
          list read as a current one. (Operator, 2026-08-16: "something letting the user know that
          we know the form has changed … but give a little bit for the form to change.") */}
      {busy && (
        <p className="rung__meta form-census__reading" aria-live="polite">
          <span className="form-census__pulse" aria-hidden="true" />
          Re-reading the page — these rows are the previous look, so they are held until it lands.
        </p>
      )}

      {/* WHAT THE PROFILE CAN ANSWER, OFFERED AS A PRESS. The capability existed (`apply_fill`
          with execute) but was worded as a description and only appeared once a plan had been
          drawn, so the operator asked for an auto-fill that was already there. A count makes the
          offer honest: it says how many of the unanswered rows we actually hold answers for, and
          nothing about the rest. */}
      {fillable > 0 && onAutofill && (
        <div className="cv-actions form-census__autofill">
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={onAutofill}
                  title="Fill every field the profile holds an answer for — types on the live page, then reads back what actually landed">
            Fill {fillable} from the profile
          </button>
          <span className="rung__meta">
            {unanswered.length > fillable
              ? `${unanswered.length - fillable} would still need you.`
              : "That covers every unanswered field."}
          </span>
        </div>
      )}

      {unanswered.length === 0 && answered.length === 0 && optional.length === 0 && (
        <p className="rung__meta">The scanner found no fields on this page.</p>
      )}
      {unanswered.length === 0 && (answered.length > 0 || optional.length > 0) && (
        <p className="rung__meta">Nothing required is outstanding on this screen.</p>
      )}

      {unanswered.length > 0 && (
        <ul className="rungs">
          {unanswered.map((r, i) => (
            <Row key={rowKey(r, i, "u")} row={r} busy={busy} rowId={rowKey(r, i, "u")}
                 onArm={arm} armed={armedAct?.key === rowKey(r, i, "u")}
                 resumePath={resumePath} />
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
                   onArm={arm} armed={armedAct?.key === rowKey(r, i, "a")}
                   resumePath={resumePath} />
            ))}
          </ul>
        </details>
      )}

      {optional.length > 0 && (
        <details className="work__more">
          <summary>
            {optional.length} other control(s) on this page — not required to continue, still
            yours to work
          </summary>
          <p className="rung__meta">
            The screen does not need these to advance. They are listed because a page that offers
            more than it demands is the normal case — an education or work-history section is
            optional to the Continue and still the reason you are here.
          </p>
          <ul className="rungs">
            {optional.map((r, i) => (
              <Row key={rowKey(r, i, "o")} row={r} busy={busy} rowId={rowKey(r, i, "o")}
                   onArm={arm} armed={armedAct?.key === rowKey(r, i, "o")}
                   resumePath={resumePath} />
            ))}
          </ul>
        </details>
      )}

      <div className="cv-actions">
        <button className="btn btn-sm" disabled={busy} onClick={onReread} aria-label="Re-read the form"
                title="Re-scan the live form — reads, types nothing">
          {busy ? "Reading…" : "Re-read the form"}
        </button>
      </div>
    </div>
  );
}
