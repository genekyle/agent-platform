import { Fragment } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { explain, NOT_MEASURED } from "./explain";

// THE DECISION INSPECTOR — a persistent "Why?", in its own column.
//
// It answers the same seven questions about whatever is selected, in the same order, every time:
// observed → rule → confidence → alternatives → evidence → intended → result. A fixed order is the
// point: the operator learns where the answer lives once, instead of hunting for whichever card
// happened to carry it this render.
//
// This is PRINCIPLES §10 given a surface. The reasoning was always recorded — it was just scattered
// across a band's `reason`, an orientation card, a `<details>` of witnesses, three `title`
// tooltips and a paragraph, each of them sitting next to (and competing with) the action it was
// explaining. Reasoning that has to fight the button for attention is reasoning nobody reads.
//
// A question we cannot answer renders as an explicit absence. An inspector that always has
// something to say cannot be trusted on the occasions when it does.

function Value({ value, mono }) {
  if (value === NOT_MEASURED || value === undefined || value === null || value === "") {
    return <span className="inspector__missing">not measured</span>;
  }
  return <span className={mono ? "is-mono" : undefined}>{String(value)}</span>;
}

function Rows({ rows }) {
  // dt/dd are direct grid children — a wrapper element here would collapse both columns into one.
  return (
    <dl className="inspector__rows">
      {rows.map((r, i) => (
        <Fragment key={i}>
          <dt title={r.hint || undefined}>{r.label}</dt>
          <dd className={r.mono ? "is-mono" : undefined} title={r.hint || undefined}>
            <Value value={r.value} />
            {r.note && <em> — {r.note}</em>}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function DecisionInspector({ panel, cockpit, selection }) {
  const x = explain(panel, cockpit, selection);
  if (!x) return null;

  const trail = (x.evidence || []).filter((e) => e.tone);
  const rows = (x.evidence || []).filter((e) => !e.tone);

  return (
    <aside className="cockpit__pane cockpit__pane--inspector">
      <div className="cockpit__pane-head">
        <AppIcon name="inspect" size={14} /> Why
      </div>

      <div className="inspector">
        <div className="inspector__title">{x.title}</div>
        {x.subtitle && <div className="inspector__subtitle">{x.subtitle}</div>}

        {x.rule && (
          <section className="inspector__block">
            <div className="inspector__label">Rule applied</div>
            <p className="inspector__rule">
              {x.rule.text}
              <em className="inspector__source">— {x.rule.source}</em>
            </p>
          </section>
        )}

        <section className="inspector__block">
          <div className="inspector__label">Observed</div>
          <Rows rows={x.observed} />
        </section>

        {/* THE WINDOW — every tab, which one is the search, which one is being applied. A count is
            not a window: an apply opens a second tab and navigates it three times, and the page
            the operator is being asked about lives in the one nobody can see. */}
        <section className="inspector__block">
          <div className="inspector__label">
            Window · {x.window.length} tab{x.window.length === 1 ? "" : "s"}
            {x.drift?.opened?.length ? ` · ${x.drift.opened.length} opened since last look` : ""}
          </div>
          {x.window.length === 0
            ? <p className="inspector__rule inspector__missing">
                No tabs answering — the session's Chrome may be gone.
              </p>
            : (
              <ul className="inspector__witnesses">
                {x.window.map((t, i) => (
                  <li key={i} className="inspector__witness" title={t.url}>
                    {t.host}
                    <span className="badge badge--muted">{t.role}</span>
                    {t.isApply && <span className="badge badge--accent">being applied</span>}
                    {t.isSearch && <span className="badge badge--ready">the search</span>}
                    <span className="inspector__witness-detail"><code>{t.path || "/"}</code></span>
                  </li>
                ))}
              </ul>
            )}
        </section>

        <section className="inspector__block">
          <div className="inspector__label">Confidence</div>
          {x.confidence.level
            ? <div className="inspector__rule">
                <span className="badge badge--muted">{x.confidence.level}</span>{" "}
                {x.confidence.detail}
              </div>
            : <p className="inspector__rule inspector__missing">{x.confidence.detail}</p>}

          {x.confidence.mismatch && (
            <p className="inspector__rule">
              <span className="badge badge--warn">mismatch</span> {x.confidence.mismatch.detail}
            </p>
          )}

          {x.confidence.witnesses.length > 0 && (
            <ul className="inspector__witnesses">
              {x.confidence.witnesses.map((w, i) => (
                <li key={i} className={`inspector__witness${w.claim ? "" : " is-abstaining"}`}>
                  <code>{w.source}</code>
                  {w.learned && (
                    <span className="badge badge--muted">
                      learned{w.weight != null && w.weight !== 1 ? ` ·${w.weight}` : ""}
                    </span>
                  )}
                  {" → "}
                  {/* A witness that abstains says so. A dissent is never hidden: the dissent is
                      often the finding. */}
                  {w.claim || <em>abstains</em>}
                  <span className="inspector__witness-detail">{w.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="inspector__block">
          <div className="inspector__label">Alternatives considered</div>
          {x.alternatives.length === 0
            ? <p className="inspector__rule inspector__missing">Nothing else was on the table.</p>
            : (
              <ul className="inspector__alts">
                {x.alternatives.map((a, i) => (
                  <li key={i}>
                    {a.label}
                    <em className="inspector__alt-why">{a.why}</em>
                  </li>
                ))}
              </ul>
            )}
        </section>

        <section className="inspector__block">
          <div className="inspector__label">Evidence</div>
          {trail.length > 0 && (
            <div className="inspector__trail">
              {trail.map((t, i) => (
                <span key={i} className="inspector__trail-chip" data-tone={t.tone}
                      title={t.hint || undefined}>
                  {t.value}
                </span>
              ))}
            </div>
          )}
          <Rows rows={rows} />
        </section>

        <section className="inspector__block">
          <div className="inspector__label">Intended action</div>
          {x.intended
            ? (
              <div className="inspector__intent">
                {x.intended.label}
                {x.intended.why && <div className="inspector__alt-why">{x.intended.why}</div>}
                {/* The endpoint, said out loud. PRINCIPLES §8: the model emits intents, never
                    selectors — so the intent is a thing that can be read, cited and disagreed
                    with. */}
                {x.intended.endpoint && (
                  <code className="inspector__endpoint">
                    POST {x.intended.endpoint} {JSON.stringify(x.intended.body || {})}
                  </code>
                )}
              </div>
            )
            : <p className="inspector__rule inspector__missing">Nothing to press here.</p>}
        </section>

        <section className="inspector__block">
          <div className="inspector__label">Result</div>
          {x.result
            ? (
              <p className={`inspector__result ${x.result.ok ? "is-ok" : "is-bad"}`}>
                {x.result.text}
                {x.result.detail && <span className="inspector__result-detail">{x.result.detail}</span>}
              </p>
            )
            : <p className="inspector__rule inspector__missing">Nothing has been attempted yet.</p>}
        </section>
      </div>
    </aside>
  );
}
