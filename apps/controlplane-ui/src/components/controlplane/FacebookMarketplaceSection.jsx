import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

async function api(path, opts) {
  const r = await fetch(`${API}${path}`, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail || `HTTP ${r.status}`);
  return r.json();
}
const jpost = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
const jpatch = (path, body) =>
  api(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const ITEM_STATUS_COLOR = {
  draft: "#8b949e", ready_to_post: "#58a6ff", queued: "#d29922", posting: "#d29922",
  posted: "#3fb950", active: "#3fb950", needs_attention: "#f85149", sold: "#a371f7",
  error: "#f85149", archived: "#6e7681",
};
const TASK_STATUS_COLOR = {
  waiting: "#8b949e", running: "#d29922", posted: "#3fb950", failed: "#f85149",
  skipped: "#6e7681", needs_review: "#f0883e",
};
const ITEM_STATUSES = ["draft", "ready_to_post", "queued", "posting", "posted", "active",
  "needs_attention", "sold", "error", "archived"];

function Pill({ value, colors = ITEM_STATUS_COLOR }) {
  if (!value) return <span className="muted">—</span>;
  const c = colors[value] || "#8b949e";
  return (
    <span style={{ color: c, border: `1px solid ${c}55`, background: `${c}18`, borderRadius: 20,
      padding: "1px 8px", fontSize: 11, whiteSpace: "nowrap", textTransform: "capitalize" }}>
      {String(value).replace(/_/g, " ")}
    </span>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "block" }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 3 }}>{label}</div>
      {children}
    </label>
  );
}

function Thumb({ photos }) {
  const src = (photos || [])[0];
  if (src) return <img src={src} alt="" style={{ width: 34, height: 34, objectFit: "cover", borderRadius: 6 }} />;
  return <div style={{ width: 34, height: 34, borderRadius: 6, background: "#21262d",
    display: "grid", placeItems: "center", color: "#6e7681" }}><i className="ti ti-photo" /></div>;
}

const fmtTime = (t) => (t ? new Date(t).toLocaleString() : "—");

/** The Marketplace selling workspace — inventory-first, channel-agnostic. */
export function FacebookMarketplaceSection({ section }) {
  if (section === "inventory") return <InventoryPanel />;
  if (section === "queue") return <QueuePanel />;
  if (section === "listings") return <ActiveListingsPanel />;
  if (section === "messages") return <MessagesPanel />;
  if (section === "activity") return <ActivityPanel />;
  if (section === "settings") return <SettingsPanel />;
  return <OverviewPanel />;
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                    */
/* -------------------------------------------------------------------------- */
function OverviewPanel() {
  const [ov, setOv] = useState(null);
  const load = useCallback(() => api("/api/inventory/overview").then(setOv).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  if (!ov) return <div className="section-body"><p className="muted">Loading…</p></div>;

  const cards = [
    { label: "Inventory items", value: ov.total_items, icon: "ti-box" },
    { label: "Drafts", value: ov.draft, icon: "ti-file-pencil" },
    { label: "Queued", value: ov.queued, icon: "ti-clock" },
    { label: "Active listings", value: ov.active_listings, icon: "ti-broadcast" },
    { label: "New responses", value: ov.items_with_responses, icon: "ti-message" },
    { label: "Needs attention", value: ov.needs_attention, icon: "ti-alert-triangle" },
    { label: "Sold", value: ov.sold, icon: "ti-checks" },
  ];
  return (
    <div className="section-body">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12 }}>
        {cards.map((c) => (
          <div key={c.label} className="panel" style={{ padding: "14px 16px" }}>
            <div className="muted" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
              <i className={`ti ${c.icon}`} /> {c.label}
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>{c.value ?? 0}</div>
          </div>
        ))}
      </div>
      <p className="muted" style={{ marginTop: 14, fontSize: 12 }}>
        Last marketplace check: {fmtTime(ov.last_checked_at)}. Your inventory is the source of truth;
        Facebook Marketplace is one sales channel — the model is built to add eBay/OfferUp/Shopify later.
      </p>
      <ManualControls onDone={load} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Inventory                                                                   */
/* -------------------------------------------------------------------------- */
const EMPTY_ITEM = { title: "", description: "", category: "", price: "", condition: "",
  pickup_location: "", photos: [], notes: "" };

function InventoryPanel() {
  const [items, setItems] = useState([]);
  const [filters, setFilters] = useState({ status: "", category: "", search: "", price_min: "", price_max: "" });
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);   // item being added/edited (or null)
  const [drawerId, setDrawerId] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    const qs = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => { if (v !== "" && v != null) qs.set(k, v); });
    api(`/api/inventory/items?${qs}`).then((d) => setItems(d.items || [])).catch(() => {});
  }, [filters]);
  useEffect(() => { load(); }, [load]);

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  const queueSelected = useCallback(async () => {
    if (!selected.size) return;
    setMsg("");
    try {
      const r = await jpost("/api/inventory/queue", { item_ids: [...selected] });
      setMsg(`Queued ${r.count} item(s).`);
      setSelected(new Set());
      load();
    } catch (e) { setMsg(String(e.message || e)); }
  }, [selected, load]);

  return (
    <div className="section-body">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" placeholder="Search title…" value={filters.search}
            onChange={(e) => setFilters({ ...filters, search: e.target.value })} style={{ width: 160 }} />
          <select className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">All statuses</option>
            {ITEM_STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>
          <input className="input" placeholder="Category" value={filters.category}
            onChange={(e) => setFilters({ ...filters, category: e.target.value })} style={{ width: 110 }} />
          <input className="input" placeholder="Min $" value={filters.price_min}
            onChange={(e) => setFilters({ ...filters, price_min: e.target.value })} style={{ width: 70 }} />
          <input className="input" placeholder="Max $" value={filters.price_max}
            onChange={(e) => setFilters({ ...filters, price_max: e.target.value })} style={{ width: 70 }} />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" disabled={!selected.size} onClick={queueSelected}>
            Add to queue{selected.size ? ` (${selected.size})` : ""}
          </button>
          <button className="btn btn-primary" onClick={() => setEditing({ ...EMPTY_ITEM })}>+ Add item</button>
        </div>
      </div>
      {msg && <div className="muted" style={{ marginTop: 8 }}>{msg}</div>}

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr>
              <th></th><th></th><th>Item</th><th>Category</th><th>Price</th><th>Condition</th>
              <th>Status</th><th>Listing</th><th>Resp.</th><th>Last checked</th><th></th>
            </tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} style={{ cursor: "pointer" }}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(it.id)} onChange={() => toggle(it.id)} />
                  </td>
                  <td onClick={() => setDrawerId(it.id)}><Thumb photos={it.photos} /></td>
                  <td onClick={() => setDrawerId(it.id)}>{it.title || <span className="muted">Untitled</span>}</td>
                  <td onClick={() => setDrawerId(it.id)} className="muted">{it.category || "—"}</td>
                  <td onClick={() => setDrawerId(it.id)}>{it.price ? `$${it.price}` : "—"}</td>
                  <td onClick={() => setDrawerId(it.id)} className="muted">{it.condition || "—"}</td>
                  <td onClick={() => setDrawerId(it.id)}><Pill value={it.internal_status} /></td>
                  <td onClick={() => setDrawerId(it.id)}><Pill value={it.listing_status} /></td>
                  <td onClick={() => setDrawerId(it.id)} style={{ textAlign: "center" }}>
                    {it.response_count || 0}{it.unread_response_count ? ` (${it.unread_response_count} new)` : ""}
                  </td>
                  <td onClick={() => setDrawerId(it.id)} className="muted" style={{ fontSize: 12 }}>{fmtTime(it.last_checked_at)}</td>
                  <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn-sm" onClick={() => setEditing(it)}>Edit</button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && <tr><td colSpan={11} className="muted" style={{ padding: 16 }}>
                No items yet. Add your first item to inventory.
              </td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {editing && <ItemForm item={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {drawerId && <ItemDrawer itemId={drawerId} onClose={() => setDrawerId(null)}
        onChanged={load} onEdit={(it) => { setDrawerId(null); setEditing(it); }} />}
    </div>
  );
}

function ItemForm({ item, onClose, onSaved }) {
  const [d, setD] = useState({ ...EMPTY_ITEM, ...item, photos: (item.photos || []).join("\n") });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const save = useCallback(async () => {
    if (!d.title.trim()) { setErr("Title is required"); return; }
    setBusy(true); setErr("");
    const payload = { ...d, photos: String(d.photos || "").split("\n").map((s) => s.trim()).filter(Boolean) };
    delete payload.id; delete payload.channels;
    try {
      if (item.id) await jpatch(`/api/inventory/items/${item.id}`, payload);
      else await jpost("/api/inventory/items", payload);
      onSaved();
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  }, [d, item, onSaved]);

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <div className="panel-header"><div>{item.id ? "Edit item" : "New item"}</div></div>
      <div style={{ display: "grid", gap: 10, padding: "10px 4px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10 }}>
          <Field label="Title *"><input className="input" value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} placeholder="Nike Tech Hoodie, black, M" /></Field>
          <Field label="Price"><input className="input" value={d.price} onChange={(e) => setD({ ...d, price: e.target.value })} placeholder="45" /></Field>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
          <Field label="Category"><input className="input" value={d.category} onChange={(e) => setD({ ...d, category: e.target.value })} placeholder="Apparel" /></Field>
          <Field label="Condition"><input className="input" value={d.condition} onChange={(e) => setD({ ...d, condition: e.target.value })} placeholder="Used - Good" /></Field>
          <Field label="Pickup location"><input className="input" value={d.pickup_location} onChange={(e) => setD({ ...d, pickup_location: e.target.value })} placeholder="Nashua, NH" /></Field>
        </div>
        <Field label="Description"><textarea rows={3} className="input" value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} /></Field>
        <Field label="Photo URLs (one per line)"><textarea rows={2} className="input" value={d.photos} onChange={(e) => setD({ ...d, photos: e.target.value })} placeholder="https://…" /></Field>
        <Field label="Notes"><input className="input" value={d.notes} onChange={(e) => setD({ ...d, notes: e.target.value })} /></Field>
        {err && <div className="error-banner">{err}</div>}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
          <button className="btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function ItemDrawer({ itemId, onClose, onChanged, onEdit }) {
  const [item, setItem] = useState(null);
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api(`/api/inventory/items/${itemId}`).then((d) => setItem(d.item)).catch(() => {}), [itemId]);
  useEffect(() => { load(); }, [load]);

  const act = useCallback(async (label, fn) => {
    setBusy(label);
    try { await fn(); await load(); onChanged?.(); } catch { /* surfaced in the activity log */ } finally { setBusy(""); }
  }, [load, onChanged]);

  const postNow = () => act("post", async () => {
    await jpost("/api/inventory/queue", { item_ids: [itemId] });
    await jpost("/api/inventory/queue/run?dry_run=true");
  });

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", zIndex: 40 }} />
      <div style={{ position: "fixed", top: 0, right: 0, height: "100%", width: 440, maxWidth: "92vw",
        background: "#0d1117", borderLeft: "1px solid #30363d", zIndex: 50, overflowY: "auto", padding: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: 16 }}>{item?.title || "Item"}</strong>
          <button className="btn btn-sm" onClick={onClose}><i className="ti ti-x" /></button>
        </div>
        {!item ? <p className="muted" style={{ marginTop: 12 }}>Loading…</p> : (
          <>
            <div style={{ display: "flex", gap: 8, margin: "12px 0", flexWrap: "wrap" }}>
              <Pill value={item.internal_status} />
              {item.listing_status && <Pill value={item.listing_status} />}
            </div>
            {(item.photos || []).length > 0 && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
                {item.photos.map((p, i) => <img key={i} src={p} alt="" style={{ width: 70, height: 70, objectFit: "cover", borderRadius: 6 }} />)}
              </div>
            )}
            <table style={{ width: "100%", fontSize: 13 }}><tbody>
              {[["Price", item.price ? `$${item.price}` : "—"], ["Category", item.category || "—"],
                ["Condition", item.condition || "—"], ["Pickup", item.pickup_location || "—"],
                ["Responses", `${item.response_count || 0}${item.unread_response_count ? ` (${item.unread_response_count} new)` : ""}`],
                ["Last checked", fmtTime(item.last_checked_at)]].map(([k, v]) => (
                <tr key={k}><td className="muted" style={{ padding: "3px 8px 3px 0", verticalAlign: "top", width: 90 }}>{k}</td><td>{v}</td></tr>
              ))}
            </tbody></table>
            {item.description && <p style={{ fontSize: 13, marginTop: 8, whiteSpace: "pre-wrap" }}>{item.description}</p>}

            {(item.channels || []).length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>Channel listings</div>
                {item.channels.map((l) => (
                  <div key={l.id} className="panel" style={{ padding: "8px 10px", marginBottom: 6, fontSize: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>{l.channel}{l.simulated ? " · simulated" : ""}</span>
                      <Pill value={l.listing_status} />
                    </div>
                    {l.listing_url && <a href={l.listing_url} target="_blank" rel="noreferrer">{l.listing_url}</a>}
                  </div>
                ))}
              </div>
            )}
            {item.notes && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>Notes: {item.notes}</p>}

            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 16 }}>
              <button className="btn btn-sm" onClick={() => onEdit(item)}>Edit</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("queue", () => jpost("/api/inventory/queue", { item_ids: [itemId] }))}>Add to queue</button>
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={postNow}>{busy === "post" ? "Posting…" : "Post now"}</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("check", () => jpost("/api/inventory/check-responses"))}>Check responses</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("sold", () => jpost(`/api/inventory/items/${itemId}/sold`))}>Mark sold</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("archive", () => jpost(`/api/inventory/items/${itemId}/archive`))}>Archive</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Posting Queue                                                               */
/* -------------------------------------------------------------------------- */
function QueuePanel() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api("/api/inventory/queue").then((d) => setQueue(d.queue || [])).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  const run = async (path, label) => {
    setBusy(true); setMsg("");
    try { const r = await jpost(path, selected.size ? { task_ids: [...selected] } : undefined);
      setMsg(`${label}: ${JSON.stringify(r)}`); setSelected(new Set()); load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  };
  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="section-body">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn-primary" disabled={busy} onClick={() => run("/api/inventory/queue/run?dry_run=true", "Ran queue")}>
          {busy ? "Running…" : "Run queue"}
        </button>
        <button className="btn" disabled={busy} onClick={() => run("/api/inventory/queue/retry", "Retried failed")}>Retry failed</button>
        <button className="btn" disabled={busy} onClick={() => run("/api/inventory/queue/clear", "Cleared completed")}>Clear completed</button>
        <button className="btn" disabled={busy || !selected.size} onClick={() => run("/api/inventory/queue/remove", "Removed")}>Remove selected</button>
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Posting is <strong>simulated</strong> for now (listings are flagged simulated) — the runner loop
        plugs in here once a channel is signed in. Sign in under Settings.
      </p>
      {msg && <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>{msg}</div>}

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th></th><th>#</th><th>Item</th><th>Price</th><th>Channel</th><th>Status</th><th>Attempts</th><th>Last attempt</th><th>Error</th></tr></thead>
            <tbody>
              {queue.map((t) => (
                <tr key={t.id}>
                  <td><input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)} /></td>
                  <td>{t.position}</td>
                  <td>{t.item_title || t.item_id}</td>
                  <td>{t.item_price ? `$${t.item_price}` : "—"}</td>
                  <td className="muted">{t.channel}</td>
                  <td><Pill value={t.status} colors={TASK_STATUS_COLOR} /></td>
                  <td style={{ textAlign: "center" }}>{t.attempts}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{fmtTime(t.last_attempt_at)}</td>
                  <td className="muted" style={{ fontSize: 12, color: t.error_message ? "#f85149" : undefined }}>{t.error_message || "—"}</td>
                </tr>
              ))}
              {queue.length === 0 && <tr><td colSpan={9} className="muted" style={{ padding: 16 }}>Queue is empty. Select items in Inventory and “Add to queue”.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Active Listings + Messages                                                  */
/* -------------------------------------------------------------------------- */
function useListings(activeOnly) {
  const [listings, setListings] = useState([]);
  const load = useCallback(() => api(`/api/inventory/listings?active_only=${activeOnly}`)
    .then((d) => setListings(d.listings || [])).catch(() => {}), [activeOnly]);
  useEffect(() => { load(); }, [load]);
  return [listings, load];
}

function CheckResponsesButton({ onDone }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <button className="btn btn-primary" disabled={busy} onClick={async () => {
        setBusy(true); setMsg("");
        try { const r = await jpost("/api/inventory/check-responses");
          setMsg(`Checked ${r.checked} listing(s), ${r.new_responses} new.`); onDone?.();
        } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
      }}>{busy ? "Checking…" : "Check responses now"}</button>
      {msg && <span className="muted" style={{ fontSize: 12 }}>{msg}</span>}
    </div>
  );
}

function ActiveListingsPanel() {
  const [listings, load] = useListings(true);
  return (
    <div className="section-body">
      <CheckResponsesButton onDone={load} />
      <div className="panel" style={{ marginTop: 12 }}>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Item</th><th>Price</th><th>Channel</th><th>Status</th><th>Responses</th><th>Unread</th><th>URL</th><th>Last checked</th></tr></thead>
            <tbody>
              {listings.map((l) => (
                <tr key={l.id}>
                  <td>{l.item_title || l.item_id}</td>
                  <td>{l.item_price ? `$${l.item_price}` : "—"}</td>
                  <td className="muted">{l.channel}{l.simulated ? " · sim" : ""}</td>
                  <td><Pill value={l.listing_status} /></td>
                  <td style={{ textAlign: "center" }}>{l.response_count || 0}</td>
                  <td style={{ textAlign: "center" }}>{l.unread_response_count || 0}</td>
                  <td>{l.listing_url ? <a href={l.listing_url} target="_blank" rel="noreferrer">open</a> : <span className="muted">—</span>}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{fmtTime(l.last_checked_at)}</td>
                </tr>
              ))}
              {listings.length === 0 && <tr><td colSpan={8} className="muted" style={{ padding: 16 }}>No active listings yet. Post items from the queue.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MessagesPanel() {
  const [listings, load] = useListings(true);
  const withMsgs = listings.filter((l) => (l.unread_response_count || 0) > 0 || (l.response_count || 0) > 0);
  return (
    <div className="section-body">
      <CheckResponsesButton onDone={load} />
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Reading actual buyer messages is a future runner task; today “Check responses” records the check.
        Listings with responses appear here.
      </p>
      <div className="panel" style={{ marginTop: 8 }}>
        <div className="panel-header"><div>Responses <span className="muted">({withMsgs.length})</span></div></div>
        {withMsgs.length === 0 ? <p className="muted" style={{ padding: "12px 14px", margin: 0 }}>No buyer responses yet.</p> : (
          <table className="runs-table">
            <thead><tr><th>Item</th><th>Channel</th><th>Unread</th><th>Total</th><th>Last checked</th></tr></thead>
            <tbody>
              {withMsgs.map((l) => (
                <tr key={l.id}><td>{l.item_title}</td><td className="muted">{l.channel}</td>
                  <td style={{ textAlign: "center", color: l.unread_response_count ? "#f0883e" : undefined }}>{l.unread_response_count || 0}</td>
                  <td style={{ textAlign: "center" }}>{l.response_count || 0}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{fmtTime(l.last_checked_at)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Agent Activity (log + loop handoffs)                                         */
/* -------------------------------------------------------------------------- */
function ActivityPanel() {
  const [log, setLog] = useState([]);
  const [handoffs, setHandoffs] = useState([]);
  const load = useCallback(() => {
    api("/api/inventory/log?limit=80").then((d) => setLog(d.log || [])).catch(() => {});
    api("/api/runtime/handoffs?open_only=true").then((d) => setHandoffs(d.handoffs || [])).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const resolve = async (id) => { await jpost(`/api/runtime/handoffs/${id}/resolve`); load(); };

  return (
    <div className="section-body">
      <button className="btn" onClick={load}>Refresh</button>
      {handoffs.length > 0 && (
        <div className="panel" style={{ marginTop: 12, borderLeft: "3px solid #f0883e" }}>
          <div className="panel-header"><div>The loop needs you <span className="muted">({handoffs.length})</span></div></div>
          {handoffs.map((h) => (
            <div key={h.id} style={{ padding: "8px 12px", borderTop: "1px solid #21262d" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong style={{ fontSize: 13 }}>{h.why}</strong>
                <button className="btn btn-sm" onClick={() => resolve(h.id)}>Resolve</button>
              </div>
              <div className="muted" style={{ fontSize: 12 }}>{h.suggestion}</div>
            </div>
          ))}
        </div>
      )}
      <div className="panel" style={{ marginTop: 12 }}>
        <div className="panel-header"><div>Activity log</div></div>
        <table className="runs-table">
          <thead><tr><th>Time</th><th>Action</th><th>Status</th><th>Message</th></tr></thead>
          <tbody>
            {log.map((e) => (
              <tr key={e.id}>
                <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{fmtTime(e.timestamp)}</td>
                <td className="muted">{e.action_type}</td>
                <td><Pill value={e.status} colors={{ ok: "#3fb950", error: "#f85149", info: "#58a6ff" }} /></td>
                <td>{e.message}</td>
              </tr>
            ))}
            {log.length === 0 && <tr><td colSpan={4} className="muted" style={{ padding: 16 }}>No activity yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Settings — channel sign-in + manual agent controls                          */
/* -------------------------------------------------------------------------- */
function ManualControls({ onDone }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const run = async (label, path) => {
    setBusy(label); setMsg("");
    try { const r = await jpost(path); setMsg(`${label} → ${JSON.stringify(r)}`); onDone?.(); }
    catch (e) { setMsg(String(e.message || e)); } finally { setBusy(""); }
  };
  const ctrls = [
    ["Post queued items", "/api/inventory/queue/run?dry_run=true"],
    ["Check responses", "/api/inventory/check-responses"],
    ["Retry failed", "/api/inventory/queue/retry"],
  ];
  return (
    <div className="panel" style={{ marginTop: 14, padding: "12px 14px" }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
        Manual agent controls — each button is a placeholder for a future scheduled agent task.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {ctrls.map(([label, path]) => (
          <button key={label} className="btn btn-sm" disabled={!!busy} onClick={() => run(label, path)}>
            {busy === label ? "…" : label}
          </button>
        ))}
      </div>
      {msg && <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>{msg}</div>}
    </div>
  );
}

function SettingsPanel() {
  return (
    <div className="section-body">
      <AuthProfileCard />
      <ManualControls />
      <p className="muted" style={{ fontSize: 12, marginTop: 14 }}>
        Channel: <strong>Facebook Marketplace</strong>. The inventory model is channel-agnostic —
        eBay, OfferUp, or a Shopify-style storefront can be added as channels without duplicating items.
      </p>
    </div>
  );
}

// Launch a PERSISTENT Facebook browser (login survives) and log in once, so real posting runs
// against an authenticated session. A create-listing run's auth pre-flight refuses a logged-out session.
function AuthProfileCard() {
  const [auth, setAuth] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    fetch(`${API}/api/training/sessions`).then((r) => r.json()).then((rows) => {
      const fb = (rows || []).filter((s) => (s.domain_id || "").startsWith("facebook") && s.status === "active");
      if (fb[0]) setSessionId(fb[0].id);
    }).catch(() => {});
  }, []);

  const check = useCallback(async (sid) => {
    const id = sid ?? sessionId; if (!id) return;
    try { setAuth(await api(`/api/runtime/auth_status?training_session_id=${id}&tab_url=facebook.com`)); }
    catch (e) { setMsg(String(e.message || e)); }
  }, [sessionId]);

  const launch = useCallback(async () => {
    setBusy(true); setMsg(""); setAuth(null);
    try {
      const s = await jpost("/api/facebook/session");
      if (s?.id) { setSessionId(s.id);
        setMsg("Chrome opened at facebook.com — log in once (do any 2FA/checkpoint by hand). This profile stays signed in.");
        setTimeout(() => check(s.id), 2500);
      } else setMsg(s?.detail || "Could not launch.");
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [check]);

  const authed = auth?.authed;
  const badge = authed === true ? { t: "Signed in", c: "#3fb950" }
    : authed === false ? { t: "Not signed in", c: "#f85149" } : { t: "Unknown", c: "#8b949e" };

  return (
    <div className="panel" style={{ padding: "12px 14px", borderLeft: "3px solid #58a6ff" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div><strong>Sign in once</strong>
          <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>Real posting needs the channel browser logged in.</span></div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: badge.c, fontWeight: 600, fontSize: 13 }}>● {badge.t}</span>
          <button className="btn btn-primary" disabled={busy} onClick={launch}>{busy ? "Launching…" : "Launch persistent FB browser"}</button>
          <button className="btn" disabled={!sessionId} onClick={() => check()}>Check sign-in</button>
        </div>
      </div>
      {msg && <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>{msg}</div>}
      {authed === false && auth?.guidance && <div style={{ marginTop: 6, color: "#f0883e", fontSize: 12 }}>{auth.guidance}</div>}
    </div>
  );
}
