import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "./api";
import { DomainIcon } from "../../../ui/Icon";

// The Status layer. Replaces the old "Connect / Sign in / Check sign-in" three-button mess with
// ONE honest, computed status and ONE primary action. Login is a supervised TASK, not a button:
// the primary types saved credentials and stops only at a human-only gate (captcha / 2FA), which
// the operator clears once. The system decides when to check sign-in; you're pulled in only when
// it can't proceed.

const LEVEL_CLASS = { ready: "status-card--ready", attention: "status-card--attention", idle: "status-card--idle" };
const DOT_CLASS = { ready: "dot--ready", attention: "dot--attention", idle: "dot--idle" };

export function StatusCard({ domain }) {
  // connected: channel browser reachable (selling) or a live session (jobs)
  // authed: true | false | null(unknown)
  const [connected, setConnected] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [authed, setAuthed] = useState(null);
  const [note, setNote] = useState(null); // login-as-task message (e.g. "clear the 2FA…")
  const [busy, setBusy] = useState("");
  const [checkedAt, setCheckedAt] = useState(null);
  const autoCheckedRef = useRef(false);

  /* ---- selling: persistent channel browser ---- */
  const pollChannel = useCallback(() => {
    if (domain.kind !== "selling") return;
    getJSON(`/api/channels/${domain.channel}/status`)
      .then((s) => {
        setConnected(!!s.connected);
        setSessionId(s.session_id);
        if (!s.connected) { setAuthed(null); autoCheckedRef.current = false; }
      })
      .catch(() => {});
  }, [domain.kind, domain.channel]);

  /* ---- jobs: live training session ---- */
  const pollSession = useCallback(() => {
    if (domain.kind !== "jobs") return;
    getJSON("/api/training/sessions")
      .then((rows) => {
        const s = (rows || []).find((x) => (x.domain_id || "").includes(domain.host) && x.status === "active");
        setConnected(!!s);
        setSessionId(s?.id ?? null);
        if (!s) { setAuthed(null); autoCheckedRef.current = false; }
      })
      .catch(() => {});
  }, [domain.kind, domain.host]);

  useEffect(() => {
    if (domain.kind === "coming_soon") return undefined;
    const tick = domain.kind === "selling" ? pollChannel : pollSession;
    tick();
    const t = setInterval(tick, domain.kind === "selling" ? 6000 : 10000);
    return () => clearInterval(t);
  }, [domain.kind, pollChannel, pollSession]);

  const recheck = useCallback(async () => {
    if (!sessionId) return;
    setBusy("check");
    try {
      const r = await getJSON(`/api/runtime/auth_status?training_session_id=${sessionId}&tab_url=${domain.tabUrl}`);
      setAuthed(r.authed === true ? true : r.authed === false ? false : null);
      if (r.authed === false && r.guidance) setNote(r.guidance);
      else if (r.authed === true) setNote(null);
      setCheckedAt(new Date());
    } catch {
      /* best-effort */
    } finally {
      setBusy("");
    }
  }, [sessionId, domain.tabUrl]);

  // The system decides when to check sign-in: once, automatically, when a browser/session is up
  // but its auth is still unknown. Guarded so it fires once per connect (not every poll).
  useEffect(() => {
    if (connected && sessionId && authed === null && !autoCheckedRef.current) {
      autoCheckedRef.current = true;
      recheck();
    }
  }, [connected, sessionId, authed, recheck]);

  const connect = useCallback(async () => {
    setBusy("connect");
    setNote("Opening the supervised browser…");
    try {
      const r = await postJSON(`/api/channels/${domain.channel}/connect`);
      setConnected(!!r.connected);
      setSessionId(r.session_id);
      setAuthed(r.authed === true ? true : r.authed === false ? false : null);
      setNote(r.connected ? null : "Could not open the browser. Try again.");
      setCheckedAt(new Date());
    } catch (e) {
      setNote(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, [domain.channel]);

  const signIn = useCallback(async () => {
    setBusy("login");
    setNote("Signing in — typing your saved credentials…");
    try {
      const r = await postJSON(`/api/channels/${domain.channel}/login`);
      if (r.logged_in) { setAuthed(true); setNote(null); }
      else { setAuthed(false); setNote(r.message || "Facebook is asking for a human step — clear it in the window, then re-check."); }
      setTimeout(() => { pollChannel(); }, 1500);
    } catch (e) {
      setNote(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, [domain.channel, pollChannel]);

  // --- derive the single status + single primary action ---
  let level = "idle";
  let head = "Not connected";
  let reason = domain.responsibility;
  let primary = null;

  if (domain.kind === "coming_soon") {
    level = "idle";
    head = "Not connected yet";
    reason = `${domain.responsibility} This domain is scaffolded in the cockpit but isn't wired to a backend yet.`;
  } else if (domain.kind === "selling") {
    if (!connected) {
      level = "idle"; head = "Browser not open";
      reason = "The supervised browser isn't open. Open it so the agent can attach and work.";
      primary = { label: "Open supervised browser", onClick: connect };
    } else if (authed === true) {
      level = "ready"; head = "Ready to work";
      reason = "Signed in — the persistent browser stays up and agents attach to it to run tasks.";
    } else if (authed === false) {
      level = "attention"; head = "Needs sign-in";
      reason = note || "Facebook is asking for login. Sign-in runs as a supervised task: the agent types your saved credentials and stops only at a captcha / 2FA for you to clear.";
      primary = { label: "Sign in", onClick: signIn };
    } else {
      level = "idle"; head = "Connected";
      reason = "Browser is up. Checking whether it's signed in…";
      primary = { label: "Check sign-in", onClick: recheck };
    }
  } else if (domain.kind === "jobs") {
    if (!connected) {
      level = "idle"; head = "No active session";
      reason = "Start an Indeed training session (Training tab) and open a search — the workspace runs inside it.";
    } else if (authed === true) {
      level = "ready"; head = "Ready to work";
      reason = "Signed in to Indeed on the active session — search, shortlist, and apply can run.";
    } else if (authed === false) {
      level = "attention"; head = "Needs sign-in";
      reason = note || "Not signed in to Indeed. Complete the Google sign-in in the session's browser window; the agent resumes once it's healthy.";
      primary = { label: "Re-check sign-in", onClick: recheck };
    } else {
      level = "idle"; head = "Session active";
      reason = "Checking whether the session is signed in to Indeed…";
      primary = { label: "Check sign-in", onClick: recheck };
    }
  }

  const busyLabel = { connect: "Opening…", login: "Signing in…", check: "Checking…" };

  return (
    <div className={`status-card ${LEVEL_CLASS[level]}`}>
      <div style={{ minWidth: 0 }}>
        <div className="status-card__head">
          <span className={`dot ${DOT_CLASS[level]}`} />
          <DomainIcon id={domain.id} size={17} /> {head}
        </div>
        <div className="status-card__reason">{reason}</div>
        <div className="status-card__meta">
          {domain.kind === "selling" ? (connected ? "Browser: healthy" : "Browser: not open") : (connected ? "Session: active" : "Session: none")}
          {authed === true ? " · Account: signed in" : authed === false ? " · Account: signed out" : ""}
          {checkedAt ? ` · checked ${checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
        </div>
      </div>
      {primary && (
        <div className="status-card__actions">
          <button className="btn btn-primary" disabled={!!busy} onClick={primary.onClick}>
            {busy ? (busyLabel[busy] || "Working…") : primary.label}
          </button>
        </div>
      )}
    </div>
  );
}
