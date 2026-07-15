import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, sendJSON } from "./workspace/api";

// Accounts — application logins organized COMPANY-FIRST, then ATS. Each company↔ATS pair gets one
// reusable login for cross-site applications. Username is a single shared address; the password is
// GENERATED (company initials + a shared suffix kept in the gitignored .env). The generated password
// is shown here so YOU can create the account on the ATS site — the agent never types it into a site
// or creates the account. Once a login exists you can save it (encrypted into the local vault) and
// use the operator-pressed "Login" to autofill it.

export function AccountsSection({ atsFilter = "" }) {
  const [data, setData] = useState({ companies: [], username: "", suffix_configured: false });
  const [ats, setAts] = useState([]);
  const [draft, setDraft] = useState({ company: "", ats_id: "", login_url: "" });
  const [revealed, setRevealed] = useState({}); // account_id -> {username, suggested_password}
  const [credForm, setCredForm] = useState(null); // { account_id, company, username, password }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    getJSON("/api/career_search/accounts")
      .then((d) => setData(d || { companies: [] })).catch(() => setData({ companies: [] }));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    getJSON("/api/career_search/ats").then((d) => setAts(d.ats_platforms || [])).catch(() => setAts([]));
  }, []);

  const addAccount = useCallback(async () => {
    if (!draft.company.trim() || !draft.ats_id) { setError("Company and ATS are required"); return; }
    setBusy(true); setError("");
    try {
      await postJSON("/api/career_search/accounts/ensure", {
        company: draft.company.trim(), ats_id: draft.ats_id, login_url: draft.login_url.trim(),
      });
      setDraft({ company: "", ats_id: "", login_url: "" });
      load();
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }, [draft, load]);

  const reveal = async (acct, company) => {
    if (revealed[acct.account_id]) { setRevealed((s) => { const n = { ...s }; delete n[acct.account_id]; return n; }); return; }
    const c = await getJSON(
      `/api/career_search/accounts/credentials?company=${encodeURIComponent(company)}&ats_id=${encodeURIComponent(acct.ats_id || "")}`,
    ).catch(() => null);
    if (c) setRevealed((s) => ({ ...s, [acct.account_id]: c }));
  };

  const saveCreds = async () => {
    if (!credForm.username?.trim() || !credForm.password) { setError("Username and password required"); return; }
    setBusy(true); setError("");
    try {
      await sendJSON(`/api/accounts/${encodeURIComponent(credForm.account_id)}`, "PATCH", {
        username: credForm.username.trim(), password: credForm.password, status: "active",
      });
      setCredForm(null); load();
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  };

  const doLogin = async (acct) => {
    try {
      const r = await postJSON(`/api/accounts/${encodeURIComponent(acct.account_id)}/login`, {});
      window.alert(r.ok ? `Login: ${r.status || "submitted"}` : `Login failed: ${r.detail || ""}`);
    } catch (e) { window.alert(String(e.message || e)); }
  };

  // OPERATOR-triggered account creation (Account Manager's create leg). YOU press it; the app fills
  // the ATS Create-Account form from the generated credential and submits. Open the ATS "Create
  // Account" screen in the browser first.
  const createAccount = async (acct, company) => {
    if (!window.confirm(`Create the ${acct.ats_id} account for ${company} with the generated login?\n\nThe ATS "Create Account" page must be open in the browser. Any email-verification/2FA step is yours.`)) return;
    try {
      const r = await postJSON("/api/career_search/accounts/create-account", { company, ats_id: acct.ats_id });
      window.alert(r.ok ? `Create account: ${r.status}` : `Not created: ${r.detail || ""}`);
      load();
    } catch (e) { window.alert(String(e.message || e)); }
  };

  const remove = async (acct) => {
    if (!window.confirm(`Delete account for ${acct.account_id}?`)) return;
    await sendJSON(`/api/accounts/${encodeURIComponent(acct.account_id)}`, "DELETE").catch(() => {});
    load();
  };

  return (
    <div className="section-body">
      <div className="layer">
        <div className="layer__head"><div className="layer__title">🔐 Accounts — by company → ATS</div></div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          One reusable login per <strong>company ↔ ATS</strong>. Username is <code>{data.username || "genomags@gmail.com"}</code>;
          the password is <strong>generated</strong> (company initials + your suffix
          {data.suffix_configured ? "" : " — ⚠️ set ATS_ACCOUNT_PW_SUFFIX in .env"}). Reveal it, create the
          account on the ATS site yourself, then save the login here (encrypted into the local vault).
          The agent never types credentials into a site or creates the account — that step is yours.
        </p>
      </div>

      <div className="layer" style={{ borderColor: "#cfe0ff" }}>
        <div className="layer__title" style={{ marginBottom: 8 }}>Add company account</div>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1.2fr 2fr auto", gap: 8, alignItems: "end" }}>
          <label style={{ fontSize: 12 }} className="muted">Company
            <input className="input" value={draft.company} placeholder="U.S. Bank National Association"
              onChange={(e) => setDraft({ ...draft, company: e.target.value })} />
          </label>
          <label style={{ fontSize: 12 }} className="muted">ATS
            <select className="input" value={draft.ats_id} onChange={(e) => setDraft({ ...draft, ats_id: e.target.value })}>
              <option value="">select…</option>
              {ats.map((a) => <option key={a.ats_id} value={a.ats_id}>{a.display_name}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12 }} className="muted">Login URL (optional)
            <input className="input" value={draft.login_url} placeholder="https://careers.company.com/…"
              onChange={(e) => setDraft({ ...draft, login_url: e.target.value })} />
          </label>
          <button className="btn btn-primary" disabled={busy} onClick={addAccount}>+ Add</button>
        </div>
        {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
      </div>

      {credForm && (
        <div className="layer" style={{ borderColor: "#cfe0ff" }}>
          <div className="layer__title" style={{ marginBottom: 8 }}>🔒 Save login for {credForm.account_id} (encrypted into vault)</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto auto", gap: 8, alignItems: "end" }}>
            <label style={{ fontSize: 12 }} className="muted">Username
              <input className="input" autoComplete="off" value={credForm.username}
                onChange={(e) => setCredForm({ ...credForm, username: e.target.value })} />
            </label>
            <label style={{ fontSize: 12 }} className="muted">Password
              <input className="input" type="password" autoComplete="new-password" value={credForm.password}
                onChange={(e) => setCredForm({ ...credForm, password: e.target.value })} />
            </label>
            <button className="btn btn-primary" disabled={busy} onClick={saveCreds}>Save</button>
            <button className="btn" onClick={() => setCredForm(null)}>Cancel</button>
          </div>
        </div>
      )}

      {(atsFilter
        ? data.companies
            .map((co) => ({ ...co, accounts: co.accounts.filter((a) => a.ats_id === atsFilter) }))
            .filter((co) => co.accounts.length > 0)
        : data.companies
      ).map((co) => (
        <div className="layer" key={co.company}>
          <div className="layer__head"><div className="layer__title">{co.company}</div></div>
          <div className="table-wrap">
            {/* Every company renders its OWN table, so auto-sized columns never line up between
                them. Fixed widths via colgroup keep all the company cards on one grid. */}
            <table className="runs-table accounts-table">
              <colgroup>
                <col className="accounts-col--ats" />
                <col />
                <col className="accounts-col--login" />
                <col className="accounts-col--status" />
                <col className="accounts-col--actions" />
              </colgroup>
              <thead><tr><th>ATS</th><th>Login URL</th><th>Generated login</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>
                {co.accounts.map((a) => (
                  <tr key={a.account_id}>
                    <td><span className="badge badge--accent">{a.ats_id}</span></td>
                    <td className="muted accounts-url">
                      {a.login_url ? <a href={a.login_url} target="_blank" rel="noreferrer">{a.login_url}</a> : "—"}
                    </td>
                    <td>
                      {revealed[a.account_id] ? (
                        <code className="accounts-cred">
                          {revealed[a.account_id].username} / {revealed[a.account_id].suggested_password || "(set suffix in .env)"}
                        </code>
                      ) : (
                        a.has_creds ? <span className="badge badge--ok">✓ {a.username_hint} (saved)</span>
                                    : <span className="muted" style={{ fontSize: 12 }}>hidden</span>
                      )}
                    </td>
                    <td><span className={`badge ${a.status === "active" ? "badge--ok" : "badge--warn"}`}>{a.status}</span></td>
                    <td>
                      {/* Two rows: the ONE action this account's lifecycle stage calls for, then the
                          always-available utilities. Beats five buttons crowded on one line. */}
                      <div className="acct-actions">
                        <div className="acct-actions__row">
                          {a.status !== "active" ? (
                            <button className="btn btn-sm btn-primary"
                                    title="Operator-triggered: fills + submits the ATS Create-Account form from the generated login (open that page first)"
                                    onClick={() => createAccount(a, co.company)}>+ Create account</button>
                          ) : (
                            <button className="btn btn-sm btn-primary" disabled={!a.has_creds}
                                    title={a.has_creds ? "Operator-triggered autofill" : "Save a login first"}
                                    onClick={() => doLogin(a)}>▶ Login</button>
                          )}
                        </div>
                        <div className="acct-actions__row">
                          <button className="btn btn-sm" onClick={() => reveal(a, co.company)}>{revealed[a.account_id] ? "Hide" : "Show password"}</button>
                          <button className="btn btn-sm" onClick={() => setCredForm({ account_id: a.account_id, company: co.company, username: data.username, password: "" })}>Save login</button>
                          <button className="btn btn-sm btn-danger" onClick={() => remove(a)}>Delete</button>
                        </div>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {data.companies.length === 0 && (
        <div className="layer"><div className="empty-hint">No accounts yet — add one per company you apply to.</div></div>
      )}
    </div>
  );
}
