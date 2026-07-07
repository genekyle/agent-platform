import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, sendJSON } from "./api";

// Manage the accounts a domain can run/post as. Each account is metadata only — a label, its
// isolating Chrome profile, and a secret_ref that NAMES where the login lives (the .env), never
// the secret itself. Two accounts = two profiles = two independent Chrome sessions, which is what
// makes running multiple logins side by side safe.

const EMPTY = { account_id: "", label: "", profile: "", secret_ref: "", notes: "", status: "active" };

// secret_ref "env:FB" (or "FB") → the two .env keys the operator must set.
function envKeys(secretRef) {
  const name = String(secretRef || "").replace(/^env:/, "").trim();
  if (!name) return null;
  return [`${name}_USERNAME`, `${name}_PASSWORD`];
}

function AccountForm({ draft, onChange, onSave, onCancel, busy, error }) {
  const isEdit = !!draft._existing;
  const keys = envKeys(draft.secret_ref);
  return (
    <div className="layer" style={{ borderColor: "#cfe0ff" }}>
      <div className="layer__head"><div className="layer__title">{isEdit ? `Edit ${draft.account_id}` : "Add account"}</div></div>
      <div style={{ display: "grid", gap: 10 }}>
        {!isEdit && (
          <label style={{ fontSize: 12 }} className="muted">Account id (stable key, e.g. facebook_alt)
            <input className="input" value={draft.account_id} onChange={(e) => onChange({ ...draft, account_id: e.target.value })} placeholder="facebook_alt" />
          </label>
        )}
        <label style={{ fontSize: 12 }} className="muted">Label
          <input className="input" value={draft.label} onChange={(e) => onChange({ ...draft, label: e.target.value })} placeholder="Facebook — reseller" />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <label style={{ fontSize: 12 }} className="muted">Chrome profile (isolation)
            <input className="input" value={draft.profile} onChange={(e) => onChange({ ...draft, profile: e.target.value })} placeholder="facebook_alt" />
          </label>
          <label style={{ fontSize: 12 }} className="muted">Status
            <select className="input" value={draft.status} onChange={(e) => onChange({ ...draft, status: e.target.value })}>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
        </div>
        <label style={{ fontSize: 12 }} className="muted">Secret ref (names where the login lives — never the secret)
          <input className="input" value={draft.secret_ref} onChange={(e) => onChange({ ...draft, secret_ref: e.target.value })} placeholder="env:FB_ALT" />
        </label>
        {keys && (
          <div className="mode-hint" style={{ marginTop: 0 }}>
            Put the login in <code>.env</code> as <strong>{keys[0]}</strong> and <strong>{keys[1]}</strong> — the app resolves them on demand and never stores or returns them.
          </div>
        )}
        <label style={{ fontSize: 12 }} className="muted">Notes
          <input className="input" value={draft.notes} onChange={(e) => onChange({ ...draft, notes: e.target.value })} />
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
    setBusy(true); setError("");
    try {
      const body = { domain_id: domain.id, label: draft.label, profile: draft.profile,
        secret_ref: draft.secret_ref, notes: draft.notes, status: draft.status };
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
  const remove = async (a) => {
    if (!window.confirm(`Delete account "${a.account_id}"? (Built-ins can only be disabled.)`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(a.account_id)}`, "DELETE").catch((e) => window.alert(String(e.message || e)));
    load();
  };

  return (
    <div className="section-body">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">👤 Accounts</div>
          <button className="btn btn-primary btn-sm" onClick={() => setDraft({ ...EMPTY })}>+ Add account</button>
        </div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          Run and post as more than one login. Each account gets its <strong>own Chrome profile</strong>, so their
          sessions never bleed together. Credentials never live here — an account only holds a <code>secret_ref</code>
          naming where the login sits in <code>.env</code>; the API exposes just a masked hint.
        </p>
      </div>

      {draft && (
        <AccountForm draft={draft} onChange={setDraft} onSave={save}
          onCancel={() => { setDraft(null); setError(""); }} busy={busy} error={error} />
      )}

      <div className="layer">
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Account</th><th>Profile</th><th>Credentials</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.account_id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{a.label} {a.builtin && <span className="badge badge--muted">built-in</span>}</div>
                    <div className="muted" style={{ fontSize: 11 }}>{a.account_id} · {a.secret_ref}</div>
                  </td>
                  <td className="muted">{a.profile}</td>
                  <td>
                    {a.has_creds
                      ? <span className="badge badge--ok">✓ {a.username_hint}</span>
                      : <span className="badge badge--warn" title={(envKeys(a.secret_ref) || []).join(" / ")}>no creds in .env</span>}
                  </td>
                  <td><span className={`badge ${a.status === "active" ? "badge--accent" : "badge--muted"}`}>{a.status}</span></td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn-sm" onClick={() => setDraft({ ...EMPTY, ...a, _existing: true })}>Edit</button>{" "}
                    <button className="btn btn-sm" onClick={() => toggleStatus(a)}>{a.status === "active" ? "Disable" : "Enable"}</button>{" "}
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
