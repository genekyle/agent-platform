import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, needsApproval } from "./api";
import { AppIcon } from "../../../ui/Icon";

// The Task layer — the actual runnable work, with grouped controls (Run now / Retry). This is
// where the old scattered "manual buttons" live now: as task controls, not the main interface.
// Each task declares whether it's `outward` (publishes/applies/sends) so the automation mode can
// decide whether it runs immediately or asks first.

async function activeSessionId(host) {
  const rows = await getJSON("/api/training/sessions");
  const s = (rows || []).find((x) => (x.domain_id || "").includes(host) && x.status === "active");
  return s?.id ?? null;
}

const TASKS = {
  selling: [
    { key: "post", label: "Post queued items", outward: true, run: () => postJSON("/api/inventory/queue/run?dry_run=true") },
    { key: "check", label: "Check responses", outward: false, run: () => postJSON("/api/inventory/check-responses") },
    { key: "retry", label: "Retry failed", outward: false, run: () => postJSON("/api/inventory/queue/retry") },
  ],
  jobs: [
    {
      key: "sweep", label: "Run search sweep (≥50 mi)", outward: false,
      run: async (host) => {
        const sid = await activeSessionId(host);
        if (!sid) throw new Error("No active Indeed session — start one and open a search.");
        return postJSON("/api/search/sweep", { training_session_id: sid, min_miles: 50 });
      },
    },
    {
      key: "extract", label: "Extract current page", outward: false,
      run: async (host) => {
        const sid = await activeSessionId(host);
        if (!sid) throw new Error("No active Indeed session — start one and open a search.");
        return postJSON("/api/jobs/extract", { training_session_id: sid, tab_url: "indeed.com/jobs", search_query: "manual extract" });
      },
    },
  ],
};

function summarize(result) {
  if (!result || typeof result !== "object") return "Done.";
  const keys = ["count", "scraped", "new", "checked", "new_responses", "jobs_found", "pages_swept", "stopped_reason", "retried"];
  const parts = keys.filter((k) => result[k] !== undefined).map((k) => `${k.replace(/_/g, " ")}: ${result[k]}`);
  return parts.length ? parts.join(" · ") : "Done.";
}

export function TasksPanel({ domain, mode }) {
  const [queueCount, setQueueCount] = useState(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState(null);
  const tasks = TASKS[domain.kind] || [];

  const loadQueue = useCallback(() => {
    if (domain.kind !== "selling") return;
    getJSON("/api/inventory/queue").then((d) => setQueueCount((d.queue || []).length)).catch(() => {});
  }, [domain.kind]);
  useEffect(() => { loadQueue(); }, [loadQueue]);

  const run = async (task) => {
    if (needsApproval(mode, task.outward)) {
      const ok = window.confirm(
        `${task.label}\n\nThis ${task.outward ? "is an outward-facing action (it publishes/applies)" : "will run now"}. ` +
        `Your mode is "${mode}", which asks first. Continue?`,
      );
      if (!ok) return;
    }
    setBusy(task.key);
    setMsg(null);
    try {
      const result = await task.run(domain.host);
      setMsg({ type: "ok", text: `${task.label} → ${summarize(result)}` });
      loadQueue();
    } catch (e) {
      setMsg({ type: "error", text: String(e.message || e) });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon"><AppIcon name="workflow" size={17} /> Tasks</div>
        {domain.kind === "selling" && queueCount != null && (
          <span className="layer__count">{queueCount} in queue</span>
        )}
      </div>

      {tasks.length === 0 ? (
        <div className="empty-hint">No runnable tasks for this domain yet.</div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {tasks.map((t) => (
              <button
                key={t.key}
                className={`btn ${t.outward ? "btn-primary" : ""}`}
                disabled={!!busy}
                onClick={() => run(t)}
              >
                {busy === t.key ? "Running…" : t.label}
                {t.outward && <span className="badge badge--accent" style={{ marginLeft: 2 }}>approval</span>}
              </button>
            ))}
          </div>
          {msg && (
            <div className={msg.type === "error" ? "error-banner" : "mode-hint"} style={{ marginTop: 10 }}>
              {msg.text}
            </div>
          )}
          {domain.kind === "jobs" && (
            <div className="mode-hint" style={{ marginTop: 10 }}>
              These run inside your active Indeed training session. Applying to a specific job stays a
              per-job approval on the Jobs tab.
            </div>
          )}
        </>
      )}
    </div>
  );
}
