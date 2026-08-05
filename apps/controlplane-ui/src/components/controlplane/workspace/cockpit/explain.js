// THE WHY, for whatever is selected.
//
// The decision inspector is a persistent pane, not a tooltip, and it answers the same seven
// questions about every selectable thing: what was OBSERVED, which RULE was applied, how sure we
// are and WHO says so, what ALTERNATIVES were considered, what EVIDENCE stands behind it, what we
// INTEND to do, and what the last attempt RESULTED in.
//
// Every one of those already existed in the read model. What they lacked was a home: they were
// scattered across an arbitration band, an orientation card, a witnesses `<details>`, a staleness
// tooltip, a mini-step chip's `title` and a "why" paragraph — six registers for one idea, each of
// them competing with the action they were meant to explain. PRINCIPLES §10 says the reasoning is
// kept on the record; this is where the record is read.
//
// Nothing here fabricates. A question we have no answer for renders as an explicit absence, never
// as a plausible sentence — an inspector that always has something to say is an inspector that
// cannot be trusted when it does.

import { BLOCKERS, ACTION_COPY } from "./lifecycle";

const NOT_MEASURED = { missing: true };

// Signals whose name ends in `_s` carry SECONDS; the rest carry a flag. Formatting everything as
// an age rendered `responsive: 1s`, which reads as "answered a second ago" and means "yes".
export function signalValue(name, value) {
  if (value === null || value === undefined) return "not measured";
  if (!String(name).endsWith("_s")) return value ? "yes" : "no";
  if (value < 90) return `${Math.round(value)}s`;
  if (value < 5400) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

const STALE_VERDICT_COPY = {
  continue: "operable — carry on",
  refresh: "reload this page before acting",
  renew: "reloading will not fix this — needs a fresh state",
  handoff: "cannot see the page well enough to judge",
};

function observedRows(p) {
  const obs = p.observer || null;
  const tabs = p.tabs || [];
  const rows = [
    { label: "Page", value: obs?.headline || (obs?.state || "").replaceAll("_", " ") || NOT_MEASURED,
      hint: obs?.state ? `machine name: ${obs.state}` : "no apply tab is being watched" },
    { label: "URL", value: obs?.url || tabs.find((t) => t.is_apply)?.url
      || tabs.find((t) => t.is_search)?.url || NOT_MEASURED, mono: true },
    { label: "Signed in", value: p.observed?.authenticated === undefined
      ? NOT_MEASURED : p.observed.authenticated ? "yes" : "no" },
    { label: "Results page", value: String(p.page ?? 1) },
  ];
  if (p.tab_drift?.opened?.length) {
    rows.push({ label: "Since last look", value: `${p.tab_drift.opened.length} tab(s) opened`,
      hint: p.tab_drift.opened.join("\n") });
  }
  return rows;
}

// THE WINDOW, still visible, and still not a count.
//
// Operator-directed 2026-07-30 and unchanged by this redesign: "an Indeed apply opens a SECOND tab
// and navigates it three times, and the cockpit's only word for that was '1 Tabs' — while the page
// the operator was being asked about lived in the tab nobody could see." The tabs move here rather
// than disappear: the inspector is persistent, so this is not behind a press — it is simply no
// longer competing with the action for the middle of the screen.
function windowRows(p) {
  return (p.tabs || []).map((t) => {
    let host = t.url; let path = "";
    try { const u = new URL(t.url); host = u.host.replace(/^www\./, ""); path = (u.pathname + u.search).slice(0, 60); }
    catch { /* a blank or malformed tab url is shown as-is */ }
    return { host, path, role: t.role, isApply: t.is_apply, isSearch: t.is_search,
      title: t.title, url: t.url };
  });
}

function confidenceOf(p) {
  const obs = p.observer;
  if (!obs) {
    return { level: null,
      detail: "No apply tab is open, so nothing was classified for this turn." , witnesses: [] };
  }
  const witnesses = obs.witnesses || [];
  const learned = witnesses.filter((w) => (w.source || "").includes(":"));
  const voting = learned.filter((w) => w.claim).length;
  return {
    level: obs.confidence || null,
    detail: learned.length
      ? `${witnesses.length} witnesses · ${learned.length} learned (${voting} voting, `
        + `${learned.length - voting} abstaining)`
      : `${witnesses.length} witnesses`,
    witnesses: witnesses.map((w) => ({
      source: w.source,
      learned: (w.source || "").includes(":"),
      weight: w.weight,
      // A witness that abstains is doing the honest thing on a page it has never met. It is shown
      // abstaining rather than dropped — a missing witness and a silent one are different facts.
      claim: w.claim || null,
      detail: w.detail,
    })),
    mismatch: obs.mismatch || null,
  };
}

function evidenceRows(p) {
  const rows = [];
  const st = p.staleness;
  if (st) {
    rows.push({ label: "Freshness", value: st.level === "fresh" ? "fresh" : `${st.level} · stale`,
      hint: [st.why, "", ...(st.signals || []).map((s) => `${s.name}: ${signalValue(s.name, s.value)} · ${s.level}`),
        ...(st.unmeasured?.length ? ["", `not measured: ${st.unmeasured.join(", ")}`] : []),
        "", `rules: ${st.rules_version} (provisional — thresholds not yet measured)`].join("\n"),
      // The level and the remedy are separate on purpose: a session left 14.5 hours was red on age
      // while still signed in and answering with 210 controls. "Very suspect, and a reload fixes
      // it" is the honest reading; collapsing the two is what made the detector propose destroying
      // a working session.
      note: st.verdict !== "continue" ? (STALE_VERDICT_COPY[st.verdict] || st.verdict) : "" });
  }
  if (p.last_step?.pace) {
    rows.push({ label: "Pace", value: `${p.last_step.pace.style}`, hint: p.last_step.pace.why });
  }
  if (p.open_pane) {
    rows.push({ label: "Open pane", value: p.open_pane.apply_type || "read",
      hint: JSON.stringify(p.open_pane, null, 2) });
  }
  if (p.applied_check) {
    rows.push({ label: "Applied before", value: p.applied_check.found ? "yes" : "no",
      hint: JSON.stringify(p.applied_check, null, 2) });
  }
  // Screenshots are not wired to this panel yet. Said out loud rather than left as a silent gap:
  // the operator asked for evidence/screenshots in the inspector, and the capture server writes
  // them (`apps/mcp/app/artifacts.py`) with no read path a session panel can call.
  rows.push({ label: "Screenshot", value: NOT_MEASURED,
    hint: "Not wired: the capture server stores observer screenshots, but no session-scoped read "
      + "endpoint exists yet." });
  return rows;
}

function resultOf(p) {
  const last = p.last_step;
  if (!last) return null;
  return {
    ok: last.ok !== false,
    text: ACTION_COPY[last.action] || last.action || "",
    detail: last.detail || "",
  };
}

function explainFocus(p, cockpit) {
  const f = cockpit.focus;
  const na = p.next_action;
  const alternatives = [];
  if (na?.secondary) {
    alternatives.push({ label: na.secondary.label, why: na.secondary.demoted_because
      || na.secondary.why, taken: false });
  }
  for (const st of (p.observer?.plan || []).slice(1)) {
    alternatives.push({ label: st.label, why: st.why, taken: false });
  }
  for (const alt of f.alternates || []) {
    if (alt) alternatives.push({ label: alt.label, why: alt.demoted || alt.why, taken: false });
  }

  return {
    title: f.title || "Now",
    subtitle: f.subtitle || "",
    // THE RULE, quoted from whoever actually applied it. `next_action.reason` is the arbitration
    // between the observer and the recipe, worded by the backend that made the call — it is not
    // paraphrased here, because a paraphrase is a second opinion wearing the first one's clothes.
    rule: na?.reason
      ? { text: na.reason, source: na.source === "observer" ? "the page (observer)" : "the recipe (ladder)" }
      : cockpit.blocker
        ? { text: cockpit.blocker.text, source: "stop-state — the session is waiting on you" }
        : f.why ? { text: f.why, source: f.group === "session"
        ? "the session preamble" : `${f.groupLabel}'s cycle` } : null,
    observed: observedRows(p),
    confidence: confidenceOf(p),
    alternatives,
    evidence: evidenceRows(p),
    intended: f.primary
      ? { label: f.primary.label, endpoint: f.primary.endpoint, body: f.primary.body, why: f.primary.why }
      : f.say ? { label: f.say, endpoint: null, body: null,
        why: "Not something this system can perform — it is the right next move to say, not press." }
        : null,
    result: resultOf(p),
  };
}

function explainRung(p, rungId) {
  const r = (p.ladder || []).find((x) => x.id === rungId);
  if (!r) return null;
  return {
    title: r.label,
    subtitle: r.kind === "consuming"
      ? (r.reached ? "consuming · spent" : "consuming · once only")
      : "standing · safe to re-run",
    rule: { text: r.why, source: "session_checkpoints.py — the checkpoint's own reason" },
    observed: [
      { label: "Status", value: r.status },
      { label: "Reached", value: r.reached ? r.reached.at : NOT_MEASURED },
      { label: "By", value: r.reached?.initiator || NOT_MEASURED },
      { label: "Evidence", value: r.reached?.evidence || NOT_MEASURED },
    ],
    confidence: { level: null, detail: "A checkpoint is a recorded fact, not a prediction — it "
      + "carries no confidence.", witnesses: [] },
    // A CONSUMING rung that has lapsed shows recovery, never a retry. If this ever offers to
    // re-run the query, the whole ladder design has been lost.
    alternatives: r.status === "lapsed" && r.recovery
      ? [{ label: "Recover", why: r.recovery, taken: false }] : [],
    evidence: evidenceRows(p),
    intended: null,
    result: null,
  };
}

function explainApplication(p, jobId) {
  const s = (p.queue?.steps || []).find((x) => x.job_id === jobId);
  if (!s) return null;
  const minis = s.minis || [];
  return {
    title: s.title || jobId,
    subtitle: [s.company, s.platform, s.landing_state?.replace(/_/g, " ")].filter(Boolean).join(" · "),
    rule: s.done
      ? { text: s.terminal_detail || `Ended as ${s.terminal}.`, source: "terminal flag" }
      : s.next_rung
        ? { text: `Next rung: ${s.next_rung.replace(/_/g, " ")}`, source: "apply_steps.py — the apply prefix" }
        : { text: "Past the known prefix — the rungs from here depend on where we landed, and "
            + "those are not built yet.", source: "apply_steps.py" },
    observed: [
      { label: "Platform", value: s.platform || NOT_MEASURED },
      { label: "Landed on", value: s.landing_state?.replace(/_/g, " ") || NOT_MEASURED },
      { label: "State", value: s.done ? s.terminal : "in flight" },
      { label: "Needs you", value: s.needs_operator ? "yes" : "no" },
    ],
    confidence: confidenceOf(p),
    alternatives: [],
    // THE MINI-STEP TRAIL as evidence rather than as a wall of chips beside the action. Every
    // attempt is kept — both sides of every correction, PRINCIPLES §10 — newest last.
    evidence: [
      ...minis.map((m, i) => ({ label: i === 0 ? "Trail" : "", value: `${m.rung} — ${m.outcome}`,
        hint: m.detail || "", tone: m.outcome })),
      ...evidenceRows(p),
    ],
    intended: null,
    result: s.terminal ? { ok: s.terminal === "submitted", text: s.terminal,
      detail: s.terminal_detail || "" } : null,
  };
}

function explainGroup(p, cockpit, groupId) {
  const g = cockpit.groups.find((x) => x.id === groupId);
  if (!g) return null;
  return {
    title: g.label,
    subtitle: g.summary,
    rule: {
      text: g.id === "session"
        ? "The preamble: a reachable browser, a held sign-in, the query and the radius — climbed "
          + "once, then held for the whole session. The consuming rungs are spent, never re-run."
        : "One page of the open-ended ladder: read it, pick from it in order, work every pick to "
          + "a terminal flag, then advance. A past page's record stays here.",
      source: "session_checkpoints.py — the ladder's shape",
    },
    observed: [
      { label: "Status", value: g.status },
      { label: "Steps", value: `${g.steps.filter((s) => s.status === "done").length}/${g.steps.length} done` },
      ...(g.status === "blocked" ? [{ label: "Blocked on", value: cockpit.blocker.text }] : []),
    ],
    confidence: { level: null, detail: "A group is the ladder's shape, not a claim about the page.",
      witnesses: [] },
    alternatives: [],
    evidence: evidenceRows(p),
    intended: null,
    result: null,
  };
}

/** Explain whatever is selected. Falls back to the focus, which is what the operator sees first. */
export function explain(panel, cockpit, selection) {
  const p = panel || {};
  const sel = selection || { kind: "focus" };
  const out = sel.kind === "rung" ? explainRung(p, sel.id)
    : sel.kind === "application" ? explainApplication(p, sel.id)
      : sel.kind === "group" ? explainGroup(p, cockpit, sel.id)
        : null;
  const base = out || explainFocus(p, cockpit);
  // The window rides on EVERY explanation, whatever is selected: which tabs are open, and which one
  // we are driving, is context for every decision on this screen rather than a property of one.
  return { ...base, window: windowRows(p), drift: p.tab_drift || null };
}

export { NOT_MEASURED, BLOCKERS };
