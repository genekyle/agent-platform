import { AppIcon } from "../../../../ui/Icon";
import { TERMINAL_CHOICES } from "./lifecycle";
import { API } from "../api";
import { explain } from "./explain";

function sameSelection(a, b) {
  return !!a && !!b && a.kind === b.kind && a.id === b.id;
}

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "No page"; }
}

export function NowPath({ cockpit, selection, onSelect }) {
  const currentGroup = cockpit.groups.find((g) => g.id === cockpit.current) || cockpit.groups[0];
  return (
    <section className="now-path" aria-label="Current session path">
      <div className="now-path__label"><AppIcon name="waypoints" size={13} /> Current path</div>
      <div className="now-path__groups">
        {cockpit.groups.map((g, i) => (
          <span key={g.id} className="now-path__segment">
            {i > 0 && <AppIcon name="chevronRight" size={13} />}
            <button type="button" data-status={g.status}
                    className={sameSelection(selection, g.select) ? "is-selected" : ""}
                    onClick={() => onSelect(g.select)}>
              <span className="now-path__dot" /> {g.label}
              <small>{g.summary}</small>
            </button>
          </span>
        ))}
      </div>
      {currentGroup?.steps?.length > 0 && (
        <div className="now-path__steps">
          {currentGroup.steps.map((step) => (
            <button key={step.key} type="button" data-status={step.status}
                    className={sameSelection(selection, step.select) ? "is-selected" : ""}
                    onClick={() => onSelect(step.select)}>
              <span /> {step.label}{step.note ? ` · ${step.note}` : ""}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export function NowContext({ panel, cockpit, selection, onOpenLens, onOpenTrace,
                             onFlag, busy }) {
  const observer = panel.observer;
  const snapshot = panel.perception_snapshot;
  const why = explain(panel, cockpit, selection);
  // The queue step the operator has pinned in the path, if any — the subject of the outcome
  // buttons below, which is deliberately NOT the same thing as the focus.
  const selected = selection?.kind === "application"
    ? (panel.queue?.steps || []).find((s) => s.job_id === selection.id) || null
    : null;
  const screenshot = snapshot?.screenshot_filename
    ? `${API}/api/observations/screenshots/${encodeURIComponent(snapshot.screenshot_filename)}`
    : null;

  return (
    <aside className="now-context">
      <section className="now-context__card now-context__card--lens">
        <div className="now-context__head">
          <span><AppIcon name="eye" size={13} /> Lens</span>
          <button type="button" onClick={onOpenLens}>Open</button>
        </div>
        <div className="now-context__visual">
          {screenshot
            ? <img src={screenshot} alt="Last page snapshot received by local perception" />
            : <div><AppIcon name="eye" size={22} /><span>No collected visual yet</span></div>}
        </div>
        <strong>{observer?.headline || observer?.state?.replaceAll("_", " ") || "Observer is not watching an application"}</strong>
        <p>{observer?.url ? hostOf(observer.url) : hostOf(snapshot?.url)}</p>
        <div className="now-context__badges">
          {observer?.confidence && <span className="badge badge--muted">{observer.confidence} confidence</span>}
          {observer?.mismatch && <span className="badge badge--warn">recipe mismatch</span>}
          {snapshot?.ts && <span className="badge badge--muted">visual collected</span>}
        </div>
      </section>

      <section className="now-context__card now-context__card--why">
        <div className="now-context__head">
          <span><AppIcon name="inspect" size={13} /> Why now</span>
        </div>
        <strong>{why?.title || cockpit.focus.title}</strong>
        <p>{why?.rule?.text || cockpit.focus.why || "No reasoning was recorded for this moment."}</p>
        {why?.rule?.source && <small>{why.rule.source}</small>}
      </section>

      {/* ACT ON THE STEP YOU SELECTED, not only on the one the focus is showing.
          Terminal flags lived on the work surface's More menu, which acts on `cycle.application`
          — the ATTENTION step. So a queued pick further down the list could not be ended at all:
          on 2026-08-14 skipping a duplicate Indeed had re-surfaced needed a curl, because the
          only pressable step was an unrelated application mid-flight. Selecting a queued row in
          the path now offers the same outcomes, scoped to what it is: a job nobody has opened is
          ended, never "submitted". */}
      {selected && (
        <section className="now-context__card">
          <div className="now-context__head">
            <span><AppIcon name="boxes" size={13} /> {selected.title || selected.job_id}</span>
          </div>
          <p>{selected.done
            ? `Already ended — ${selected.terminal}.`
            : `Queued from this page, ${selected.minis?.length ? "worked" : "never opened"}. End it here without touching the application in flight.`}</p>
          {!selected.done && (
            <div className="now-context__badges">
              {TERMINAL_CHOICES.filter((f) => f.flag !== "parked:operator" || selected.minis?.length)
                .map((f) => (
                  <button key={f.flag} className="btn btn-sm btn-ghost" disabled={busy}
                          aria-label={`${selected.title || selected.job_id}: ${f.label}`}
                          title={f.why} onClick={() => onFlag(f.flag, f.why, selected.job_id)}>
                    {f.label}
                  </button>
                ))}
            </div>
          )}
        </section>
      )}

      <WhatWeKnew orientation={panel.last_step?.orientation} held={panel.last_step?.account_on_file} />

      <section className="now-context__card now-context__card--trace">
        <div className="now-context__head">
          <span><AppIcon name="activity" size={13} /> Control record</span>
          <button type="button" onClick={onOpenTrace}>Open trace</button>
        </div>
        <p>Cockpit commands are recorded as <strong>operator</strong>. Automatic and teacher steps retain their own provenance in the trace.</p>
        {panel.last_step && (
          <small>Last result: {panel.last_step.ok === false ? "did not verify" : "recorded"} · {panel.last_step.detail || panel.last_step.action}</small>
        )}
      </section>

      <Learning learning={panel.learning} />
    </aside>
  );
}

/**
 * WHAT WE ALREADY KNEW, at the step that just ran. The producers — the registry's own note, the
 * account store, the world-fact staleness report — all existed while eight separate incidents
 * paid full price to rediscover what they held (LEARNINGS: "a note nothing reads at classify time
 * is not memory, it is an archive"). The crank consults them now; this is where the operator sees
 * the same sentences it cited, so a wrong cue is arguable rather than invisible.
 *
 * Renders nothing when nothing was learned — a card that says "consulted, nothing known" on every
 * crank teaches the reader to skip it, and the line is the whole point.
 */
function WhatWeKnew({ orientation, held }) {
  const cues = orientation?.cues || [];
  const stale = orientation?.stale_claims || [];
  const account = held || orientation?.account;
  if (!cues.length && !stale.length && !account) return null;
  return (
    <section className="now-context__card">
      <div className="now-context__head">
        <span><AppIcon name="listTree" size={13} /> What we already knew</span>
        {orientation?.ats_id ? <small>{orientation.ats_id}</small> : null}
      </div>
      {cues.map((cue) => (
        <p key={cue.text}><strong>·</strong> {cue.text}</p>
      ))}
      {account ? (
        <p>
          <strong>·</strong> an account for this employer is already on file —{" "}
          <code>{account.account_id}</code>
          {account.match === "canonical" ? " (matched across a different spelling — confirm it is the same employer)" : ""}
        </p>
      ) : null}
      {stale.map((claim) => (
        <p key={claim.id}>
          <strong>·</strong> <code>{claim.id}</code> is {claim.outdriven_by_days}d outdriven —
          treat it as unverified. {claim.recheck}
        </p>
      ))}
      {orientation?.silent?.length ? (
        <small>silent: {orientation.silent.join(", ")} — nothing on file, not nothing to know</small>
      ) : null}
    </section>
  );
}

// WHAT THE INNER LAYERS ARE GETTING RIGHT — the practice, made visible. Ported from the retired
// DecisionInspector: ORIENTER (the recipe's predicted next screen vs. the StepRunner's after-look)
// and SHADOW (what the controller would have decided, journaled beside what we did) are both
// scored on every crank, and a scorecard nobody can see is the same as not practising.
//
// A missing measurement renders as an explicit absence, never a zero: "0% accurate" and "never
// asked" look identical on a dial and mean opposite things.
function Learning({ learning }) {
  if (!learning) return null;
  const o = learning.orienter || {};
  const s = learning.shadow || {};
  const pct = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : null);
  const oScore = pct(o.accuracy);
  // GATED ON THE SAMPLE, NOT ON THE NUMBER. `shadow_agreement` returns a deliberate `0.0` when it
  // has no pairs — honest in the report and misleading on a dial, where it reads as "the
  // controller agrees with nothing" rather than "never asked".
  const sScore = (s.n || 0) > 0 ? pct(s.agreement) : null;

  return (
    <section className="now-context__card now-context__card--learning">
      <div className="now-context__head">
        <span><AppIcon name="learning" size={13} /> Learning</span>
      </div>
      <div className="learning">
        <div className="learning__row">
          <span className="learning__name">Orienter</span>
          {oScore
            ? <>
                <span className="learning__score">{oScore}</span>
                <span className="learning__meta">
                  {o.hits}/{o.scored} transitions called
                  {o.unscored > 0 && <> · {o.unscored} unreadable</>}
                </span>
              </>
            : <span className="learning__none">
                {o.trials ? "no scoreable trial yet" : "not practised yet"}
              </span>}
        </div>
        {(o.recent || []).length > 0 && (
          <ul className="learning__trials">
            {o.recent.slice(0, 4).map((t, i) => (
              <li key={i} className="learning__trial">
                <span className={`is-${t.result}`}>{t.result}</span>
                <span>{(t.from || "?").replace(/_/g, " ")} → {(t.to || "?").replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="learning__row">
          <span className="learning__name">Shadow</span>
          {sScore
            ? <>
                <span className="learning__score">{sScore}</span>
                <span className="learning__meta">
                  {s.agree}/{s.n} paired steps matched
                </span>
              </>
            : <span className="learning__none">no paired step yet</span>}
        </div>
      </div>
    </section>
  );
}
