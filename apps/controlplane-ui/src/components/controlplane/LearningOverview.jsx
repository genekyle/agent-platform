import { useCallback, useEffect, useMemo, useState } from "react";
import { AppIcon } from "../../ui/Icon";
import { getJSON } from "./workspace/api";

const FLOW = [
  { id: "capture", label: "Capture", icon: "inspect" },
  { id: "label", label: "Label", icon: "tag" },
  { id: "train", label: "Train", icon: "learning" },
  { id: "evaluate", label: "Evaluate", icon: "chart" },
  { id: "promote", label: "Promote", icon: "checkCircle" },
];

function Metric({ label, value, detail, tone = "neutral" }) {
  return (
    <article className={`ops-metric ops-metric--${tone}`}>
      <div className="ops-metric__label">{label}</div>
      <div className="ops-metric__value">{value}</div>
      <div className="ops-metric__detail">{detail}</div>
    </article>
  );
}

export function LearningOverview({ onOpenSection }) {
  const [command, setCommand] = useState(null);
  const [controller, setController] = useState(null);

  const load = useCallback(async () => {
    const [commandResult, controllerResult] = await Promise.allSettled([
      getJSON("/api/command-center/summary"),
      getJSON("/api/controller/summary"),
    ]);
    if (commandResult.status === "fulfilled") setCommand(commandResult.value);
    if (controllerResult.status === "fulfilled") setController(controllerResult.value);
  }, []);

  useEffect(() => {
    const initialTimer = setTimeout(load, 0);
    const timer = setInterval(load, 15000);
    return () => {
      clearTimeout(initialTimer);
      clearInterval(timer);
    };
  }, [load]);

  const flywheel = command?.flywheel || {};
  const toLabel = flywheel.to_label_total ?? 0;
  const labeled = flywheel.labeled_total ?? 0;
  const accuracy = flywheel.grounding_accuracy;
  const programs = controller?.programs?.length ?? controller?.program_count ?? 0;
  const reasonedRate = controller?.reasoned_rate;
  const verifiedRate = controller?.verified_rate;

  const activeFlowIndex = useMemo(() => {
    if (toLabel > 0) return 1;
    if (labeled > 0 && accuracy == null) return 2;
    if (accuracy != null && programs === 0) return 3;
    if (programs > 0) return 4;
    return 0;
  }, [toLabel, labeled, accuracy, programs]);

  return (
    <div className="learning-overview">
      <section className="ops-hero-panel">
        <div>
          <div className="ops-eyebrow">Learning loop</div>
          <h2>Turn corrections into capability</h2>
          <p>
            Every captured state, confirmed label, and verified decision moves repeatable work toward
            the local student—while the teacher remains available for new terrain.
          </p>
        </div>
        <button className="primary-btn" onClick={() => onOpenSection("label")}>
          <AppIcon name="tag" size={16} />
          Label next{toLabel ? ` (${toLabel})` : ""}
        </button>
      </section>

      <section className="ops-metric-grid">
        <Metric label="Waiting for labels" value={toLabel} detail="examples in the review queue" tone={toLabel ? "warning" : "success"} />
        <Metric label="Labeled" value={labeled} detail="confirmed training examples" />
        <Metric
          label="Grounding accuracy"
          value={accuracy == null ? "Not trained" : `${Math.round(accuracy * 100)}%`}
          detail={accuracy == null ? "waiting for the first reliable model" : "latest reported evaluation"}
          tone={accuracy == null ? "neutral" : "success"}
        />
        <Metric label="Intent programs" value={programs} detail="verified sequences available at the free rung" />
        <Metric
          label="Verified decisions"
          value={verifiedRate == null ? "—" : `${Math.round(verifiedRate * 100)}%`}
          detail="actions that landed where expected"
          tone={verifiedRate == null ? "neutral" : "success"}
        />
        <Metric
          label="Reasoning coverage"
          value={reasonedRate == null ? "—" : `${Math.round(reasonedRate * 100)}%`}
          detail="teaching rows with a usable why"
        />
      </section>

      <section className="panel learning-flow-panel">
        <div className="panel-header">
          <div>
            <h2>From observation to graduation</h2>
            <p>The active stage is inferred from the current queue and evaluation state.</p>
          </div>
        </div>
        <div className="learning-flow" aria-label="Learning flow">
          {FLOW.map((step, index) => (
            <div key={step.id} className={`learning-flow__step ${index < activeFlowIndex ? "is-done" : index === activeFlowIndex ? "is-active" : ""}`}>
              <span className="learning-flow__icon"><AppIcon name={step.icon} size={17} /></span>
              <span>{step.label}</span>
              {index < FLOW.length - 1 ? <span className="learning-flow__line" /> : null}
            </div>
          ))}
        </div>
      </section>

      <section className="learning-action-grid">
        <button className="ops-action-card" onClick={() => onOpenSection("coverage")}>
          <AppIcon name="chart" />
          <span><strong>Find coverage gaps</strong><small>See which page states should be captured next.</small></span>
          <AppIcon name="arrowRight" />
        </button>
        <button className="ops-action-card" onClick={() => onOpenSection("controller")}>
          <AppIcon name="route" />
          <span><strong>Inspect the controller</strong><small>Review rung mix, reasoning, verification, and programs.</small></span>
          <AppIcon name="arrowRight" />
        </button>
        <button className="ops-action-card" onClick={() => onOpenSection("scorecard")}>
          <AppIcon name="corpus" />
          <span><strong>Check corpus quality</strong><small>Understand what can train and what is quarantined.</small></span>
          <AppIcon name="arrowRight" />
        </button>
      </section>
    </div>
  );
}
