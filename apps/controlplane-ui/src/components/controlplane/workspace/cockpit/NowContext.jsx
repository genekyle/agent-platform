import { AppIcon } from "../../../../ui/Icon";
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

export function NowContext({ panel, cockpit, selection, onOpenLens, onOpenTrace }) {
  const observer = panel.observer;
  const snapshot = panel.perception_snapshot;
  const why = explain(panel, cockpit, selection);
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
    </aside>
  );
}
