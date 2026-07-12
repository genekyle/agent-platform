import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, sendJSON } from "./api";

// Per-employer Workday (and other ATS) accounts. Each cross-site apply routes to a company's own
// Workday tenant, which needs its OWN login — this is the one secure spot that holds them. The
// PASSWORD is typed here and encrypted straight into the local secrets vault (AES-256-GCM, key held
// outside the app); the registry only ever keeps a masked hint. Never stored in plaintext, never
// returned. (The agent cannot enter these credentials to authenticate, and cannot save the plaintext
// on your behalf — you type them here once; the app encrypts on save.)

const EMPTY = {
  account_id: "", label: "", login_url: "", notes: "", status: "active", username: "", password: "",
};

function slugify(s) {
  return String(s || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function WorkdayForm({ draft, onChange, onSave, onCancel, busy, error }) {
  const isEdit = !!draft._existing;
  const set = (patch) => onChange({ ...draft, ...patch });
  return (
    <div className="layer" style={{ borderColor: "#cfe0ff" }}>
      <div className="layer__head">
        <div className="layer__title">{isEdit ? `Edit ${draft.account_id}` : "Add Workday account"}</div>
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        <label style={{ fontSize: 12 }} className="muted">Employer
          <input className="input" value={draft.label} placeholder="Point32Health"
            onChange={(e) => {
              const label = e.target.value;
              set(isEdit ? { label } : { label, account_id: draft.account_id || `workday_${slugify(label)}` });
            }} />
        </label>
        <label style={{ fontSize: 12 }} className="muted">Workday sign-in URL
          <input className="input" value={draft.login_url} placeholder="https://employer.wd5.myworkdayjobs.com/…"
            onChange={(e) => set({ login_url: e.target.value })} />
        </label>
        {!isEdit && (
          <label style={{ fontSize: 12 }} className="muted">Account id (stable key)
            <input className="input" value={draft.account_id} placeholder="workday_point32health"
              onChange={(e) => set({ account_id: e.target.value })} />
          </label>
        )}

        <div className="panel" style={{ padding: "10px 12px", background: "#f7fbff" }}>
          <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8 }}>🔒 Login (encrypted into the vault)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <label style={{ fontSize: 12 }} className="muted">Username / email
              <input className="input" autoComplete="off" value={draft.username} placeholder="genomags@gmail.com"
                onChange={(e) => set({ username: e.target.value })} />
            </label>
            <label style={{ fontSize: 12 }} className="muted">Password
              <input className="input" type="password" autoComplete="new-password" value={draft.password}
                placeholder={isEdit ? "leave blank to keep current" : "••••••••"}
                onChange={(e) => set({ password: e.target.value })} />
            </label>
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
            Encrypted at rest (AES-256-GCM); the key lives outside the app. Only a masked hint is ever
            stored or shown.{isEdit && " Leave both blank to keep the current login."}
          </div>
        </div>

        <label style={{ fontSize: 12 }} className="muted">Notes
          <input className="input" value={draft.notes} onChange={(e) => set({ notes: e.target.value })} />
        </label>
        {error && <div className="error-banner">{error}</div>}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" disabled={busy} onClick={onSave}>{busy ? "Saving…" : "Save account"}</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export function WorkdayAccountsPanel() {
  const [accounts, setAccounts] = useState([]);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loginState, setLoginState] = useState({}); // account_id -> { status, detail }

  const load = useCallback(() => {
    getJSON("/api/accounts?domain_id=workday")
      .then((d) => setAccounts(d.accounts || [])).catch(() => setAccounts([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = useCallback(async () => {
    if (!draft.account_id?.trim()) { setError("Employer (or account id) is required"); return; }
    if (draft.username?.trim() && !draft.password) { setError("Enter a password (or clear the username to keep the current login)."); return; }
    const settingCreds = draft.username?.trim() && draft.password;
    setBusy(true); setError("");
    try {
      const body = {
        domain_id: "workday", kind: "workday", label: draft.label || draft.account_id,
        login_url: draft.login_url, notes: draft.notes, status: draft.status,
        profile: draft.account_id,
      };
      if (settingCreds) { body.username = draft.username.trim(); body.password = draft.password; }
      if (draft._existing) await sendJSON(`/api/accounts/${encodeURIComponent(draft.account_id)}`, "PATCH", body);
      else await postJSON("/api/accounts", { ...body, account_id: slugify(draft.account_id) });
      setDraft(null); load();
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }, [draft, load]);

  const clearCreds = async (a) => {
    if (!window.confirm(`Remove the saved login for "${a.label}"? (The account stays; it'll just have no credentials.)`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}/credentials`, "DELETE").catch(() => {});
    load();
  };
  const remove = async (a) => {
    if (!window.confirm(`Delete Workday account "${a.label}" and its saved login?`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}`, "DELETE").catch((e) => window.alert(String(e.message || e)));
    load();
  };

  // Operator-triggered login: fire the backend routine that resolves the vault credential and drives
  // the currently-open sign-in form. YOU press it; the app does the keystrokes (the agent never enters
  // credentials to authenticate). 2FA/captcha still land on you.
  const doLogin = async (a) => {
    setLoginState((s) => ({ ...s, [a.account_id]: { status: "logging_in", detail: "" } }));
    try {
      const r = await postJSON(`/api/accounts/${encodeURIComponent(a.account_id)}/login`, {});
      setLoginState((s) => ({ ...s, [a.account_id]: { status: r.status || (r.ok ? "submitted" : "failed"), detail: r.detail || "" } }));
    } catch (e) {
      setLoginState((s) => ({ ...s, [a.account_id]: { status: "failed", detail: String(e.message || e) } }));
    }
  };

  const STATUS_BADGE = {
    logging_in: ["badge--accent", "Signing in…"],
    submitted: ["badge--ok", "✓ Submitted"],
    no_login_form: ["badge--warn", "No form open"],
    failed: ["badge--warn", "Failed"],
  };

  return (
    <div className="section-body">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">🗂️ Workday Accounts</div>
          <button className="btn btn-primary btn-sm" onClick={() => setDraft({ ...EMPTY })}>+ Add account</button>
        </div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          Cross-site job applications route to each employer's own <strong>Workday tenant</strong>, and each
          needs its own login. Store them here: type the password in the form and it's{" "}
          <strong>encrypted into the local vault</strong> (AES-256-GCM, key held outside the app) — never in
          plaintext, never shown again, only a masked hint. Note: signing in to the live site with these is
          done by you; the app keeps them safe but won't auto-enter passwords to authenticate.
        </p>
      </div>

      {draft && (
        <WorkdayForm draft={draft} onChange={setDraft} onSave={save}
          onCancel={() => { setDraft(null); setError(""); }} busy={busy} error={error} />
      )}

      <div className="layer">
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Employer</th><th>Sign-in URL</th><th>Login</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.account_id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{a.label}</div>
                    <div className="muted" style={{ fontSize: 11 }}>{a.account_id}</div>
                  </td>
                  <td className="muted" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {a.login_url
                      ? <a href={a.login_url} target="_blank" rel="noreferrer">{a.login_url}</a>
                      : <span className="muted">—</span>}
                  </td>
                  <td>
                    {a.has_creds
                      ? <span className="badge badge--ok">✓ {a.username_hint}</span>
                      : <span className="badge badge--warn">no login set</span>}
                  </td>
                  <td><span className={`badge ${a.status === "active" ? "badge--accent" : "badge--muted"}`}>{a.status}</span></td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={!a.has_creds || loginState[a.account_id]?.status === "logging_in"}
                      title={a.has_creds ? "Run the login (you press it; the app does the keystrokes)" : "Add credentials first"}
                      onClick={() => doLogin(a)}
                    >
                      {loginState[a.account_id]?.status === "logging_in" ? "Signing in…" : "▶ Login"}
                    </button>{" "}
                    {loginState[a.account_id] && (() => {
                      const [cls, txt] = STATUS_BADGE[loginState[a.account_id].status] || ["badge--muted", loginState[a.account_id].status];
                      return <span className={`badge ${cls}`} title={loginState[a.account_id].detail}>{txt}</span>;
                    })()}{" "}
                    <button className="btn btn-sm" onClick={() => setDraft({ ...EMPTY, ...a, password: "", _existing: true })}>Edit</button>{" "}
                    {a.has_creds && a.secret_backend === "vault" && <button className="btn btn-sm" onClick={() => clearCreds(a)}>Clear login</button>}{" "}
                    <button className="btn btn-sm btn-danger" onClick={() => remove(a)}>Delete</button>
                  </td>
                </tr>
              ))}
              {accounts.length === 0 && <tr><td colSpan={5} className="empty-hint">No Workday accounts yet — add one per employer you apply to.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
