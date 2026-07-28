import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, sendJSON } from "./api";
import { AppIcon } from "../../../ui/Icon";

// Manage the accounts a domain can run/post as. Each account is metadata (label + isolating Chrome
// profile + notes); the LOGIN is typed right here and encrypted into the secrets vault (AES-256-GCM,
// pluggable key provider) — never stored in the registry, never logged. Two accounts = two
// profiles = two independent Chrome sessions.
//
// A stored login CAN be read back, on request, via the one reveal endpoint — because "did I type
// this right?" is otherwise unanswerable, and a wrong stored password surfaces much later as a
// confusing login failure rather than as the typo it is. It is fetched per-account when asked for,
// never with the account list.

const EMPTY = { account_id: "", label: "", profile: "", notes: "", status: "active", username: "", password: "" };

function Field({ label, hint, children }) {
  return (
    <div className="form-field">
      <span className="form-field__label">{label}</span>
      {children}
      {hint && <span className="form-field__hint">{hint}</span>}
    </div>
  );
}

// The password control: type into it, peek at what you typed, or pull back what's already stored.
// `revealed` is kept separate from `draft.password` so that looking at the saved value never
// counts as EDITING it — otherwise a curious click would re-submit the same password on save and
// silently rewrite the vault entry.
function PasswordField({ draft, isEdit, onChange, onReveal }) {
  const [visible, setVisible] = useState(false);
  const [revealed, setRevealed] = useState(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState("");

  // A different account in the form means the revealed value is not ours any more.
  useEffect(() => { setRevealed(null); setVisible(false); setNote(""); }, [draft.account_id]);

  const showStored = revealed !== null && !draft.password;
  const value = showStored ? revealed : draft.password;

  const reveal = async () => {
    setLoading(true); setNote("");
    try {
      const r = await postJSON(`/api/accounts/${encodeURIComponent(draft.account_id)}/credentials/reveal`, {});
      setRevealed(r.password || "");
      setVisible(true);
      setNote(r.backend === "env" ? "Read from .env" : "Decrypted from the vault");
    } catch {
      setNote("No stored login to show.");
    } finally { setLoading(false); }
  };

  return (
    <Field
      label="Password"
      hint={note || (showStored ? "This is the saved login — editing it replaces the stored one." : undefined)}
    >
      <div className="input-affix">
        <input
          className={`input ${visible && value ? "input--revealed" : ""}`}
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          value={value}
          onChange={(e) => { setRevealed(null); onChange(e.target.value); }}
          placeholder={isEdit ? "leave blank to keep current" : "••••••••"}
        />
        <button
          type="button"
          className="input-affix__btn"
          title={visible ? "Hide" : "Show"}
          aria-label={visible ? "Hide password" : "Show password"}
          disabled={!value}
          onClick={() => setVisible((v) => !v)}
        >
          <AppIcon name={visible ? "eyeOff" : "eye"} size={15} />
        </button>
      </div>
      {isEdit && onReveal !== false && revealed === null && (
        <button type="button" className="btn btn-sm" style={{ justifySelf: "start", marginTop: 2 }}
          disabled={loading} onClick={reveal}>
          {loading ? "Reading…" : "Show saved password"}
        </button>
      )}
    </Field>
  );
}

function AccountForm({ draft, onChange, onSave, onCancel, busy, error }) {
  const isEdit = !!draft._existing;
  return (
    <div className="layer" style={{ borderColor: "var(--line-strong)" }}>
      <div className="layer__head">
        <div className="layer__title">{isEdit ? `Edit ${draft.account_id}` : "Add account"}</div>
        {isEdit && <span className="layer__sub">{draft.secret_backend || "env"}-backed</span>}
      </div>

      <div className="form-grid">
        {!isEdit && (
          <Field label="Account id" hint="Stable key — used for the profile directory and the vault entry.">
            <input className="input" value={draft.account_id} placeholder="facebook_alt"
              onChange={(e) => onChange({ ...draft, account_id: e.target.value })} />
          </Field>
        )}

        <Field label="Label">
          <input className="input" value={draft.label} placeholder="Facebook — reseller"
            onChange={(e) => onChange({ ...draft, label: e.target.value })} />
        </Field>

        <div className="form-row">
          <Field label="Chrome profile" hint="Its own user-data-dir — this is what keeps sessions from bleeding together.">
            <input className="input" value={draft.profile} placeholder="facebook_alt"
              onChange={(e) => onChange({ ...draft, profile: e.target.value })} />
          </Field>
          <Field label="Status">
            <select className="input" value={draft.status}
              onChange={(e) => onChange({ ...draft, status: e.target.value })}>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </Field>
        </div>

        <div className="form-fieldset">
          <div className="form-fieldset__title">
            <AppIcon name="lock" size={14} /> Login
          </div>
          <div className="form-row">
            <Field label="Username / email">
              <input className="input" autoComplete="off" value={draft.username} placeholder="seller@gmail.com"
                onChange={(e) => onChange({ ...draft, username: e.target.value })} />
            </Field>
            <PasswordField draft={draft} isEdit={isEdit}
              onChange={(password) => onChange({ ...draft, password })} />
          </div>
          <div className="form-field__hint" style={{ marginTop: 8 }}>
            Encrypted at rest (AES-256-GCM); the key lives outside the app.
            {isEdit && " Leave both blank to keep the current login."}
          </div>
        </div>

        <Field label="Notes">
          <input className="input" value={draft.notes}
            onChange={(e) => onChange({ ...draft, notes: e.target.value })} />
        </Field>

        {error && <div className="error-banner">{error}</div>}

        <div className="form-actions">
          <button className="btn btn-primary" disabled={busy} onClick={onSave}>{busy ? "Saving…" : "Save account"}</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export function AccountsPanel({ domain }) {
  const [accounts, setAccounts] = useState([]);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    getJSON(`/api/accounts?domain_id=${encodeURIComponent(domain.id)}`)
      .then((d) => setAccounts(d.accounts || [])).catch(() => setAccounts([]));
  }, [domain.id]);
  useEffect(() => { load(); }, [load]);

  const save = useCallback(async () => {
    if (!draft.account_id?.trim()) { setError("Account id is required"); return; }
    const settingCreds = draft.username?.trim() && draft.password;
    if (draft.username?.trim() && !draft.password) { setError("Enter a password (or clear the username to keep the current login)."); return; }
    setBusy(true); setError("");
    try {
      const body = { domain_id: domain.id, label: draft.label, profile: draft.profile, notes: draft.notes, status: draft.status };
      if (settingCreds) { body.username = draft.username.trim(); body.password = draft.password; }
      if (draft._existing) await sendJSON(`/api/accounts/${encodeURIComponent(draft.account_id)}`, "PATCH", body);
      else await postJSON("/api/accounts", { ...body, account_id: draft.account_id });
      setDraft(null); load();
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }, [draft, domain.id, load]);

  const toggleStatus = async (a) => {
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}`, "PATCH",
      { status: a.status === "active" ? "disabled" : "active" }).catch(() => {});
    load();
  };
  const clearCreds = async (a) => {
    if (!window.confirm(`Remove the saved login for "${a.account_id}"? (The account stays; it'll just have no credentials.)`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}/credentials`, "DELETE").catch(() => {});
    load();
  };
  const remove = async (a) => {
    if (!window.confirm(`Delete account "${a.account_id}" and its saved login? (Built-ins can only be disabled.)`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}`, "DELETE").catch((e) => window.alert(String(e.message || e)));
    load();
  };

  return (
    <div className="section-body">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">Accounts</div>
          <button className="btn btn-primary btn-sm" onClick={() => setDraft({ ...EMPTY })}>+ Add account</button>
        </div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          Run and post as more than one login. Each account gets its <strong>own Chrome profile</strong>, so their
          sessions never bleed together. Type the login right here — it's <strong>encrypted into the local vault</strong>
          {" "}(AES-256-GCM, key held outside the app) and never stored in plaintext. The account list carries only a
          masked hint; you can read a saved password back from its Edit form when you need to check it.
        </p>
      </div>

      {draft && (
        <AccountForm draft={draft} onChange={setDraft} onSave={save}
          onCancel={() => { setDraft(null); setError(""); }} busy={busy} error={error} />
      )}

      <div className="layer">
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Account</th><th>Profile</th><th>Login</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.account_id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{a.label} {a.builtin && <span className="badge badge--muted">built-in</span>}</div>
                    <div className="muted" style={{ fontSize: 11 }}>{a.account_id} · {a.secret_backend || "env"}</div>
                  </td>
                  <td className="muted">{a.profile}</td>
                  <td>
                    {a.has_creds
                      ? <span className="badge badge--ok">✓ {a.username_hint}</span>
                      : <span className="badge badge--warn">no login set</span>}
                  </td>
                  <td><span className={`badge ${a.status === "active" ? "badge--accent" : "badge--muted"}`}>{a.status}</span></td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn-sm" onClick={() => setDraft({ ...EMPTY, ...a, password: "", _existing: true })}>Edit</button>{" "}
                    <button className="btn btn-sm" onClick={() => toggleStatus(a)}>{a.status === "active" ? "Disable" : "Enable"}</button>{" "}
                    {a.has_creds && a.secret_backend === "vault" && <button className="btn btn-sm" onClick={() => clearCreds(a)}>Clear login</button>}{" "}
                    {!a.builtin && <button className="btn btn-sm btn-danger" onClick={() => remove(a)}>Delete</button>}
                  </td>
                </tr>
              ))}
              {accounts.length === 0 && <tr><td colSpan={5} className="empty-hint">No accounts yet — add one to post/run as it.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
