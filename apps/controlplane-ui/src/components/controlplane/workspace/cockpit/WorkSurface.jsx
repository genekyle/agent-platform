import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { PickOrb } from "../OrderedPicks";
import FormSections from "../FormSections";
import FillPlan from "../FillPlan";
import { chooseFocus, TERMINAL_CHOICES } from "./lifecycle";

// THE WORK SURFACE — the one thing the operator can act on now.
//
// The rule this pane exists to enforce: EXACTLY ONE PRIMARY ACTION ON SCREEN. Not one per card,
// not one per stage — one. Everything a phase could theoretically offer is not the same as what
// this moment is asking, and rendering the former is how the old panel came to show "Work this
// step" twice (same label, same `/apply_step` endpoint, both styled primary, ~700px apart) while a
// results table for a decision already made sat above both of them.
//
// So: the focus comes from `lifecycle.focusFor`, and this component may not invent an action of its
// own. It renders `focus.primary` (or says the move it cannot perform, which is not a button),
// `focus.alternates` quietly beside it, and `focus.more` behind a disclosure. Sub-surfaces —
// the fill plan, the accordion, the results table — are the OBJECT being worked, not competing
// answers, and their own buttons are demoted accordingly.

function Actions({ focus, busy, call }) {
  if (!focus.primary && !focus.say && !(focus.alternates || []).length) return null;
  return (
    <div className="work__actions">
      {focus.primary && (
        <button className="btn btn-primary" disabled={busy} title={focus.primary.why}
                onClick={() => call(focus.primary.endpoint, focus.primary.body)}>
          {busy ? "…" : focus.primary.label}
        </button>
      )}
      {/* With no primary the alternates ARE the choice, so they are full size — shrinking them
          would imply a preselected answer that does not exist. */}
      {(focus.alternates || []).filter(Boolean).map((a, i) => (
        <span key={i} className="work__alt">
          <button className={focus.primary ? "btn btn-sm" : "btn"} disabled={busy} title={a.why}
                  onClick={() => call(a.endpoint, a.body)}>
            {a.label}
          </button>
          {a.demoted && <span className="work__alt-why"> — {a.demoted}</span>}
        </span>
      ))}
    </div>
  );
}

// A step we cannot perform is still the right next move to SAY. What it must never be is a button:
// a control that cannot act is how a panel lies about what it can do.
function Say({ focus }) {
  if (!focus.say) return null;
  return (
    <p className="work__say">
      {focus.say}
      <span className="work__say-note">
        Not something this system can perform — it is the move to make, not a button to press.
      </span>
    </p>
  );
}

function More({ focus, busy, onFlag }) {
  if (!(focus.more || []).length) return null;
  return (
    <details className="work__more">
      <summary>End this application another way — {TERMINAL_CHOICES.length + 1} outcomes</summary>
      <div className="work__more-grid">
        {/* Submitted is separated from the rest because it is the only one that means success and
            the only one that claims a real application went out. */}
        <button className="btn btn-sm" disabled={busy}
                title="Only press this when the application is CONFIRMED sent"
                onClick={() => onFlag("submitted", "")}>
          Submitted
        </button>
        {focus.more.map((f) => (
          <button key={f.flag} className="btn btn-sm btn-ghost" disabled={busy} title={f.why}
                  onClick={() => onFlag(f.flag, f.why)}>
            {f.label}
          </button>
        ))}
      </div>
    </details>
  );
}

// --- per-phase bodies: the OBJECT being worked, never a second question ----------------------

function SetupBody({ focus, panel, form, setForm, busy, call }) {
  if (focus.kind === "declare") {
    return (
      <>
        <div className="work-setup">
          <label className="work-field">
            <span>Query</span>
            <input value={form.query} disabled={busy} placeholder="reporting analyst"
                   onChange={(e) => setForm((f) => ({ ...f, query: e.target.value }))} />
          </label>
          <label className="work-field">
            <span>Location</span>
            <input value={form.location} disabled={busy} placeholder="Nashua, NH"
                   onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))} />
          </label>
          <label className="work-field work-field--narrow">
            <span>Radius (mi)</span>
            <input type="number" min={50} value={form.radius_miles} disabled={busy}
                   onChange={(e) => setForm((f) => ({ ...f, radius_miles: Number(e.target.value) }))} />
          </label>
        </div>
        <div className="work__actions">
          <button className="btn btn-primary" disabled={busy || !form.query.trim()}
                  onClick={() => call("/initialize", { ...form })}>
            {busy ? "…" : "Initialize this session"}
          </button>
        </div>
      </>
    );
  }

  if (focus.kind === "clean_start") {
    return (
      <div className="work__section">
        <div className="work__section-head">
          <AppIcon name="boxes" size={13} /> Inherited tabs
        </div>
        <ul className="rungs">
          {(panel.last_step?.fresh_start?.to_close || []).map((t) => (
            <li key={t.tab_id} className="rung rung--pending">
              <div className="rung__body">
                <div className="rung__line">
                  <span className="badge badge--muted">{t.role}</span>
                  <span className="rung__label">{t.url}</span>
                  {(t.role === "apply" || t.role === "errand") && (
                    <span className="badge badge--warn">may hold real work</span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
        {panel.last_step?.fresh_start?.keeper && (
          <p className="rung__meta">
            Keeping <code>{panel.last_step.fresh_start.keeper.url}</code> to land on.
          </p>
        )}
      </div>
    );
  }

  if (focus.kind === "login") {
    // The credential boundary, stated where the credential would be typed.
    return (
      <p className="cv-blocked">
        You take over at the password or code — we never type either.
        {panel.last_step?.login?.seen ? ` ${panel.last_step.login.seen} elements seen on this page.` : ""}
        {!(panel.last_step?.login?.options || []).length && panel.last_step?.login?.detail
          ? ` ${panel.last_step.login.detail}` : ""}
      </p>
    );
  }
  return null;
}

function DecideBody({ panel, picks, armed, onPick, onClear, note, setNote, busy }) {
  const results = panel.results || [];
  if (results.length === 0) return null;
  return (
    <>
      <div className="work__section">
        <div className="work__section-head">
          <AppIcon name="listTree" size={13} />
          {picks.length
            ? (armed ? "Click another number to swap the two — or the same one again to remove it."
              : "Click a number to pick it up and swap.")
            : "Click a circle to make it #1. They run in this order."}
          {picks.length > 0 && (
            <button className="btn btn-sm btn-ghost" disabled={busy} onClick={onClear}>Clear</button>
          )}
        </div>
        {/* The table scrolls INSIDE the surface. A 21-row list is the object of this decision, not
            a reason for the page to become three screens tall. */}
        <div className="work-results__scroll">
          <table className="work-results">
            <thead>
              <tr>
                <th className="work-results__ord">#</th>
                <th>Role</th><th>Company</th><th>Where</th><th>Pay</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.job_id} className={picks.includes(r.job_id) ? "is-picked" : ""}>
                  <td className="work-results__ord">
                    <PickOrb jobId={r.job_id} label={r.title} picks={picks} armed={armed}
                             onPick={onPick} />
                  </td>
                  <td>{r.title}</td>
                  <td>{r.company}</td>
                  <td>{r.location}</td>
                  <td>{r.salary || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <textarea className="work-note" rows={2} value={note}
                placeholder="Why these (or why none) — rides into the page's record."
                onChange={(e) => setNote(e.target.value)} />
    </>
  );
}

function ExecuteBody({ focus, panel, busy, call, decide }) {
  const last = panel.last_step;
  const account = focus.account || focus.handoff;

  return (
    <>
      {/* THE TEACHER'S PROPOSAL. Correct is a PEER of Go, never quieter: the golden training rows
          come from disagreement, and a surface whose easy path is always "yes" produces agreement
          and no signal. */}
      {focus.kind === "proposal" && (
        <ProposalBody proposal={focus.proposal} busy={busy} decide={decide} />
      )}

      {/* The credential card. The one place credentials appear, and the boundary is loud. */}
      {account && (
        <div className="work__section">
          <div className="work__section-head">
            <AppIcon name="shield" size={13} />
            {account.company} ({account.ats})
          </div>
          {focus.handoff?.remaining?.checked && (
            <p className="sc-account__remaining">
              {focus.handoff.remaining.operator.length > 0
                ? <><b>Needs you:</b> {focus.handoff.remaining.operator.join(" · ")}</>
                : focus.handoff.remaining.system.length > 0
                  ? <>Nothing needed from you — the account system fills the {focus.handoff.remaining.system.length} blank
                    field(s) from the stored credential.</>
                  : <>The form on screen is complete — nothing left but the button.</>}
            </p>
          )}
          {focus.handoff && (
            <dl className="sc-account__creds">
              <dt>Username</dt><dd><code>{focus.handoff.username || "—"}</code></dd>
              <dt>Password</dt>
              <dd>
                {focus.handoff.suggested_password
                  ? <code>{focus.handoff.suggested_password}</code>
                  : <span className="rung__meta">
                      {focus.handoff.suffix_configured ? "—"
                        : "no suggestion configured — choose your own that meets the site's rules"}
                    </span>}
              </dd>
            </dl>
          )}
        </div>
      )}

      {/* THE FORM ON THIS PAGE — the object being filled, in its own register.
          It renders once something has actually been READ (a fill plan, or the accordion's
          sections). Before that there is one quiet way in, not two standing buttons: the old panel
          offered "Plan the fill" for the whole of Execute because `remaining > 0` is true the whole
          time, so an unrecognised page with no form on it still showed a fill control. An
          affordance that is right for one screen in ten reads as noise on the other nine. */}
      {(last?.fill_plan || last?.sections) ? (
        <div className="work__section">
          <div className="work__section-head"><AppIcon name="listTree" size={13} /> The form on this page</div>
          <FormSections sections={last?.sections} busy={busy}
                        ats={panel.account_state?.ats} accordionAts={panel.accordion_ats}
                        onExpand={(what) => call("/apply_sections",
                          { ats: panel.account_state?.ats || "successfactors",
                            ...(what ? { expand: what } : {}) })} />
          {last?.fill_plan && (
            <FillPlan plan={last.fill_plan} summary={last.fill_summary} busy={busy}
                      onPlan={() => call("/apply_fill", { execute: false })}
                      onFill={() => call("/apply_fill", { execute: true })} />
          )}
        </div>
      ) : (
        <div className="work__section">
          <button className="btn btn-sm btn-ghost" disabled={busy}
                  title="Read the open form and show what would be typed — types nothing"
                  onClick={() => call("/apply_fill", { execute: false })}>
            Read this page&apos;s form
          </button>
        </div>
      )}
    </>
  );
}

function ProposalBody({ proposal, busy, decide }) {
  const [correcting, setCorrecting] = useState(null);
  const [err, setErr] = useState("");

  const send = () => {
    if (!correcting?.intent) { setErr("a correction needs an intent"); return; }
    let params = {};
    try { params = correcting.params ? JSON.parse(correcting.params) : {}; }
    catch { setErr('params must be JSON, e.g. {"field": "Work authorization", "value": "Yes"}'); return; }
    if (correcting.rationale.trim().length < 12) {
      setErr("a correction needs a reason — that reasoning is the training signal"); return;
    }
    setErr("");
    decide({ action: "correct", intent: correcting.intent, params, rationale: correcting.rationale });
    setCorrecting(null);
  };

  return (
    <div className="work__section">
      <div className="work__section-head">
        <AppIcon name="sparkle" size={13} /> Teacher proposes <code>{proposal.intent}</code>
        {Object.keys(proposal.params || {}).length > 0 && (
          <span className="rung__meta">
            {Object.entries(proposal.params).map(([k, v]) => `${k}: ${v}`).join(" · ")}
          </span>
        )}
      </div>
      {proposal.note && <p className="cv-blocked">{proposal.note}</p>}
      {proposal.expected_next?.length > 0 && (
        <p className="rung__meta">expects → {proposal.expected_next.join(", ")}</p>
      )}
      {err && <div className="coaching-error">{err}</div>}

      {correcting ? (
        <div className="cv-correct">
          <input placeholder="intent instead (click / set_text / select_option …)"
                 value={correcting.intent}
                 onChange={(e) => setCorrecting((c) => ({ ...c, intent: e.target.value }))} />
          <input placeholder='params, e.g. {"field": "Work authorization", "value": "Yes"}'
                 value={correcting.params}
                 onChange={(e) => setCorrecting((c) => ({ ...c, params: e.target.value }))} />
          <textarea className="work-note" rows={2}
                    placeholder="Why the teacher was wrong — this reasoning is the training signal. Required."
                    value={correcting.rationale}
                    onChange={(e) => setCorrecting((c) => ({ ...c, rationale: e.target.value }))} />
          <div className="work__actions">
            <button className="btn btn-primary" disabled={busy} onClick={send}>Send correction</button>
            <button className="btn btn-sm" onClick={() => setCorrecting(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        // Go and Correct are the same size on purpose.
        <div className="work__actions">
          <button className="btn btn-primary" disabled={busy}
                  onClick={() => decide({ action: "go" })}>Go</button>
          <button className="btn btn-primary" disabled={busy}
                  onClick={() => setCorrecting({ intent: proposal.intent,
                    params: JSON.stringify(proposal.params || {}), rationale: "" })}>
            Correct
          </button>
          <button className="btn btn-sm btn-ghost" disabled={busy}
                  onClick={() => decide({ action: "skip" })}>Skip</button>
        </div>
      )}
    </div>
  );
}

const SETUP_KINDS = new Set(["declare", "clean_start", "login"]);
const EXECUTE_KINDS = new Set(["proposal", "account_handoff", "account", "application"]);

export function WorkSurface({
  panel, cockpit, viewMoment, onExitDetour, busy, error, call, decide, onFlag,
  picks, armed, onPick, onClear, note, setNote, form, setForm,
}) {
  // THE ONE LEGITIMATE DETOUR: re-opening the current page's picker while its queue is being
  // worked — the STANDING select rung allows adding picks. Anything else the operator clicks in
  // the rail only changes what the inspector explains, never what this surface asks.
  const detour = viewMoment === "choose" && cockpit.focus.kind !== "choose"
    && (panel.results || []).length > 0;
  const focus = detour ? chooseFocus(panel, picks) : cockpit.focus;

  return (
    <div className="cockpit__pane">
      <div className="work">
        {detour && (
          <div className="work__detour">
            <AppIcon name="eye" size={13} />
            You re-opened <strong>{focus.groupLabel}&apos;s picker</strong> — picks add to this
            page&apos;s queue. The session&apos;s work is
            {" "}<strong>{cockpit.focus.title}</strong>.
            <button className="btn btn-sm" onClick={onExitDetour}>Back to the work</button>
          </div>
        )}

        <div className="work__eyebrow">
          <AppIcon name="play" size={12} /> {focus.groupLabel}
          {cockpit.blocker && !detour && <span className="badge badge--warn">needs you</span>}
        </div>

        <h2 className="work__title">{focus.title}</h2>
        {focus.subtitle && <p className="work__subtitle">{focus.subtitle}</p>}
        {focus.why && <p className="work__why">{focus.why}</p>}

        {error && <div className="coaching-error">{error}</div>}

        <Say focus={focus} />
        {focus.kind !== "proposal" && <Actions focus={focus} busy={busy} call={call} />}

        {SETUP_KINDS.has(focus.kind) && (
          <SetupBody focus={focus} panel={panel} form={form} setForm={setForm}
                     busy={busy} call={call} />
        )}
        {focus.kind === "choose" && (
          <DecideBody panel={panel} picks={picks} armed={armed} onPick={onPick} onClear={onClear}
                      note={note} setNote={setNote} busy={busy} />
        )}
        {EXECUTE_KINDS.has(focus.kind) && (
          <ExecuteBody focus={focus} panel={panel} busy={busy} call={call} decide={decide} />
        )}
        {focus.kind === "walked_out" && <VerifyBody panel={panel} />}

        {EXECUTE_KINDS.has(focus.kind) && <More focus={focus} busy={busy} onFlag={onFlag} />}
      </div>
    </div>
  );
}

function VerifyBody({ panel }) {
  const done = (panel.queue?.steps || []).filter((s) => s.done);
  if (!done.length) return null;
  return (
    <div className="work__section">
      <div className="work__section-head"><AppIcon name="checkCircle" size={13} /> Accounted for</div>
      <ul className="rungs">
        {done.map((s) => (
          <li key={s.job_id} className={`rung rung--${s.terminal === "submitted" ? "held" : "pending"}`}>
            <div className="rung__body">
              <div className="rung__line">
                <span className={`badge badge--${s.terminal === "submitted" ? "ready" : "muted"}`}>
                  {s.terminal}
                </span>
                <span className="rung__label">{s.title || s.job_id}</span>
                {s.company && <span className="rung__meta">{s.company}</span>}
              </div>
              {s.terminal_detail && <div className="rung__meta">{s.terminal_detail}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
