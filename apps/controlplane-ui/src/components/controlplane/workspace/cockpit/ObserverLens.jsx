import { useCallback, useEffect, useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { API, fmtTime, getJSON } from "../api";

const POLL_MS = 5000;

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "—"; }
}

function Value({ children }) {
  return children === undefined || children === null || children === ""
    ? <span className="lens__missing">not measured</span>
    : children;
}

export function ObserverLens({ sessionId }) {
  const [panel, setPanel] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setPanel(await getJSON(`/api/session_control/${sessionId}`));
      setError("");
    } catch (e) {
      setError(e.message || "Could not read the local observer");
    } finally {
      setRefreshing(false);
    }
  }, [sessionId]);

  useEffect(() => {
    const initial = setTimeout(load, 0);
    const timer = setInterval(load, POLL_MS);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, [load]);

  if (!panel && !error) return <p className="empty-hint">Opening the local system&apos;s lens…</p>;
  if (!panel) return <div className="coaching-error">{error}</div>;

  const observer = panel.observer;
  const snapshot = panel.perception_snapshot;
  const belief = snapshot?.belief;
  // belief.uncertainty is an OBJECT of axes ({state, element, answer, effect, novelty}) — an
  // object rendered as a React child throws and takes the whole pane down (found 2026-08-10).
  const unc = belief?.uncertainty;
  const uncState = unc && typeof unc === "object" ? unc.state : unc;
  const novelty = unc && typeof unc === "object" ? unc.novelty : belief?.novelty;
  // Outside ladder drives `observer` is null, but the snapshot's belief carries the same
  // witnesses — show what the system actually has instead of an empty card.
  const witnesses = (observer?.witnesses?.length ? observer.witnesses : null)
    || (belief?.witnesses || []).map((w) => ({
      source: w.name,
      claim: w.label,
      detail: [
        w.margin !== undefined ? `margin ${Number(w.margin).toFixed(2)}` : null,
        w.novelty !== undefined ? `novelty ${Number(w.novelty).toFixed(2)}` : null,
        (w.top_evidence || []).length ? `evidence: ${(w.top_evidence || []).join(", ")}` : null,
      ].filter(Boolean).join(" · "),
    }));
  const screenshot = snapshot?.screenshot_filename
    ? `${API}/api/observations/screenshots/${encodeURIComponent(snapshot.screenshot_filename)}`
    : null;
  const observedUrl = observer?.url || snapshot?.url || panel.tabs?.find((t) => t.is_apply)?.url
    || panel.tabs?.find((t) => t.is_search)?.url || "";
  const visualDrift = snapshot?.url && observedUrl && snapshot.url !== observedUrl;

  return (
    <div className="lens">
      <section className="lens__viewer cockpit__pane">
        <div className="cockpit__pane-head">
          <AppIcon name="eye" size={14} /> Last collected visual
          <span className="lens__head-spacer" />
          {visualDrift && <span className="badge badge--warn">page moved since capture</span>}
          <button type="button" className="btn btn-sm" disabled={refreshing} onClick={load}>
            {refreshing ? "Reading…" : "Read now"}
          </button>
        </div>
        <div className="lens__viewport">
          {screenshot ? (
            <img src={screenshot} alt="Last safely collected page shown to local perception" />
          ) : (
            <div className="lens__empty-visual">
              <AppIcon name="eye" size={28} />
              <strong>No safely collected screenshot yet</strong>
              <span>The next non-credential step that runs through local perception will appear here.</span>
            </div>
          )}
        </div>
        <div className="lens__capture-meta">
          <span><strong>Collected</strong> <Value>{fmtTime(snapshot?.ts)}</Value></span>
          <span><strong>Page</strong> <Value>{hostOf(snapshot?.url)}</Value></span>
          <span><strong>Artifact</strong> <Value>{snapshot?.artifact}</Value></span>
        </div>
        <p className="lens__privacy">
          Credential-flow turns intentionally collect no screenshot. The Lens shows only visuals
          already admitted to the local training/perception path.
        </p>
      </section>

      <div className="lens__readout">
        {error && <div className="coaching-error">{error}</div>}

        <section className="cockpit__pane lens__card">
          <div className="cockpit__pane-head"><AppIcon name="inspect" size={14} /> Observer conclusion</div>
          <div className="lens__card-body">
            <div className="lens__headline">
              <strong>{observer?.headline || observer?.state?.replaceAll("_", " ") || "No application is being watched"}</strong>
              {observer?.confidence && <span className="badge badge--reasoning">{observer.confidence}</span>}
            </div>
            <dl className="lens__facts">
              <dt>State</dt><dd><Value>{observer?.state?.replaceAll("_", " ")}</Value></dd>
              <dt>Kind</dt><dd><Value>{observer?.kind}</Value></dd>
              <dt>URL</dt><dd className="is-mono"><Value>{observedUrl}</Value></dd>
              <dt>Local belief</dt><dd><Value>{belief?.state || belief?.label}</Value></dd>
              <dt>Uncertainty</dt><dd><Value>{uncState !== undefined && uncState !== null ? Number(uncState).toFixed(2) : null}</Value></dd>
              <dt>Novelty</dt><dd><Value>{novelty !== undefined && novelty !== null ? Number(novelty).toFixed(2) : null}</Value></dd>
            </dl>
            {observer?.mismatch && (
              <div className="lens__mismatch">
                <span className="badge badge--warn">Expected and observed disagree</span>
                <p>{observer.mismatch.detail || observer.mismatch.reason || String(observer.mismatch)}</p>
              </div>
            )}
          </div>
        </section>

        <section className="cockpit__pane lens__card">
          <div className="cockpit__pane-head"><AppIcon name="listTree" size={14} /> Proposed way forward</div>
          <div className="lens__card-body">
            {(observer?.plan || []).length === 0 ? (
              <p className="lens__missing">The observer offered no action for this page.</p>
            ) : (
              <ol className="lens__plan">
                {observer.plan.map((step) => (
                  <li key={step.id || step.label}>
                    <strong>{step.label || step.id}</strong>
                    <span>{step.why || (step.driveable ? "Local system can perform this." : "Requires a handoff.")}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </section>

        <section className="cockpit__pane lens__card">
          <div className="cockpit__pane-head">
            <AppIcon name="boxes" size={14} /> Evidence · {witnesses.length} witnesses
          </div>
          <div className="lens__card-body">
            {witnesses.length === 0 ? (
              <p className="lens__missing">No observer witnesses reported for this moment.</p>
            ) : (
              <ul className="lens__witnesses">
                {witnesses.map((w, i) => (
                  <li key={`${w.source}-${i}`}>
                    <div><code>{w.source}</code>{w.claim
                      ? <span className="badge badge--muted">{w.claim}</span>
                      : <span className="badge badge--muted">abstains</span>}</div>
                    <p>{w.detail || "No detail recorded."}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="cockpit__pane lens__card">
          <div className="cockpit__pane-head"><AppIcon name="domains" size={14} /> Window context</div>
          <div className="lens__card-body">
            <ul className="lens__tabs">
              {(panel.tabs || []).map((tab) => (
                <li key={tab.tab_id || tab.url}>
                  <strong>{hostOf(tab.url)}</strong>
                  <span className="badge badge--muted">{tab.role}</span>
                  {tab.is_apply && <span className="badge badge--reasoning">observed</span>}
                  {tab.is_search && <span className="badge badge--ready">search</span>}
                  <code>{tab.url}</code>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </div>
  );
}
