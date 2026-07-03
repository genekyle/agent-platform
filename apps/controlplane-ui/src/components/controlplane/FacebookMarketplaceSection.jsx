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
const jdelete = (path) => api(path, { method: "DELETE" });

const ITEM_STATUS_COLOR = {
  draft: "#8b949e", ready_to_post: "#3b82f6", queued: "#d29922", posting: "#d29922",
  posted: "#16a34a", active: "#16a34a", needs_attention: "#dc2626", sold: "#7c3aed",
  error: "#dc2626", archived: "#6e7681",
};
const TASK_STATUS_COLOR = {
  waiting: "#8b949e", running: "#d29922", posted: "#16a34a", failed: "#dc2626",
  skipped: "#6e7681", needs_review: "#ea580c",
};
const ITEM_STATUSES = ["draft", "ready_to_post", "queued", "posting", "posted", "active",
  "needs_attention", "sold", "error", "archived"];

function Pill({ value, colors = ITEM_STATUS_COLOR }) {
  if (!value) return <span className="muted">—</span>;
  const c = colors[value] || "#8b949e";
  return (
    <span style={{ color: c, border: `1px solid ${c}55`, background: `${c}18`, borderRadius: 20,
      padding: "1px 9px", fontSize: 11, fontWeight: 600, whiteSpace: "nowrap", textTransform: "capitalize" }}>
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

function Thumb({ photos, size = 34 }) {
  const src = (photos || [])[0];
  if (src) return <img src={src} alt="" style={{ width: size, height: size, objectFit: "cover", borderRadius: 6 }} />;
  return <div style={{ width: size, height: size, borderRadius: 6, background: "#e6e8eb",
    display: "grid", placeItems: "center", color: "#9aa0a6", fontSize: size * 0.5 }}>🖼</div>;
}

function SectionHeading({ title, right }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end",
      borderBottom: "2px solid var(--border, #d0d7de)", paddingBottom: 6, margin: "26px 0 12px" }}>
      <h3 style={{ margin: 0, fontSize: 16 }}>{title}</h3>
      {right}
    </div>
  );
}

const fmtTime = (t) => (t ? new Date(t).toLocaleString() : "—");

/** The Marketplace selling workspace — inventory-first, channel-agnostic. Default view is the
 *  single-page dashboard (cards → inventory → queue → activity); the nav sub-sections are
 *  focused deep-dives that reuse the same building blocks. */
export function FacebookMarketplaceSection({ section }) {
  if (section === "inventory") return <div className="section-body"><InventoryBody /></div>;
  if (section === "queue") return <div className="section-body"><QueueBody /></div>;
  if (section === "listings") return <ActiveListingsPanel />;
  if (section === "messages") return <MessagesPanel />;
  if (section === "activity") return <div className="section-body"><ActivityBody /></div>;
  return <DashboardPanel />;
}

/* -------------------------------------------------------------------------- */
/* Combined dashboard (the v1 single page)                                     */
/* -------------------------------------------------------------------------- */
function DashboardPanel() {
  return (
    <div className="section-body">
      <OverviewCards />
      <SectionHeading title="Inventory" />
      <InventoryBody />
      <SectionHeading title="Posting Queue" />
      <QueueBody />
      <SectionHeading title="Activity Log" />
      <ActivityBody compact />
    </div>
  );
}

function StatCard({ label, value, icon, accent }) {
  return (
    <div className="panel" style={{ padding: "12px 16px", borderTop: `3px solid ${accent}` }}>
      <div className="muted" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
        <span>{icon}</span> {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, marginTop: 2 }}>{value ?? 0}</div>
    </div>
  );
}

function OverviewCards() {
  const [ov, setOv] = useState(null);
  useEffect(() => {
    const load = () => api("/api/inventory/overview").then(setOv).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);
  const cards = [
    { label: "Total items", value: ov?.total_items, icon: "📦", accent: "#3b82f6" },
    { label: "Queued", value: ov?.queued, icon: "⏳", accent: "#d29922" },
    { label: "Active listings", value: ov?.active_listings, icon: "📡", accent: "#16a34a" },
    { label: "New responses", value: ov?.items_with_responses, icon: "💬", accent: "#0ea5e9" },
    { label: "Needs attention", value: ov?.needs_attention, icon: "⚠️", accent: "#dc2626" },
    { label: "Sold", value: ov?.sold, icon: "✅", accent: "#7c3aed" },
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12 }}>
      {cards.map((c) => <StatCard key={c.label} {...c} />)}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Inventory                                                                   */
/* -------------------------------------------------------------------------- */
const EMPTY_ITEM = { title: "", description: "", category: "", price: "", condition: "",
  pickup_location: "", photos: [], notes: "" };

function InventoryBody() {
  const [items, setItems] = useState([]);
  const [filters, setFilters] = useState({ status: "", category: "", search: "" });
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [drawerId, setDrawerId] = useState(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    const qs = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => { if (v !== "" && v != null) qs.set(k, v); });
    api(`/api/inventory/items?${qs}`).then((d) => setItems(d.items || [])).catch(() => {});
  }, [filters]);
  useEffect(() => { load(); }, [load]);

  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const queueSelected = useCallback(async () => {
    if (!selected.size) return;
    setMsg("");
    try {
      const r = await jpost("/api/inventory/queue", { item_ids: [...selected] });
      setMsg(`Queued ${r.count} item(s).`); setSelected(new Set()); load();
    } catch (e) { setMsg(String(e.message || e)); }
  }, [selected, load]);

  const rowAction = async (it) => {
    // context action mirroring the mockup: post if ready, else check responses if active
    if (it.internal_status === "active") { await jpost("/api/inventory/check-responses"); }
    else { await jpost("/api/inventory/queue", { item_ids: [it.id] }); }
    load();
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" placeholder="Search items…" value={filters.search}
            onChange={(e) => setFilters({ ...filters, search: e.target.value })} style={{ width: 180 }} />
          <select className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            <option value="">Status: All</option>
            {ITEM_STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>
          <input className="input" placeholder="Category" value={filters.category}
            onChange={(e) => setFilters({ ...filters, category: e.target.value })} style={{ width: 120 }} />
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
              <th></th><th>Photo</th><th>Title</th><th>Price</th><th>Status</th><th>FB Status</th>
              <th>Responses</th><th>Last checked</th><th>Actions</th>
            </tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(it.id)} onChange={() => toggle(it.id)} />
                  </td>
                  <td onClick={() => setDrawerId(it.id)} style={{ cursor: "pointer" }}><Thumb photos={it.photos} /></td>
                  <td onClick={() => setDrawerId(it.id)} style={{ cursor: "pointer", fontWeight: 600 }}>{it.title || <span className="muted">Untitled</span>}</td>
                  <td>{it.price ? `$${it.price}` : "—"}</td>
                  <td><Pill value={it.internal_status} /></td>
                  <td>{it.listing_status ? <Pill value={it.listing_status} /> : <span className="muted">Not posted</span>}</td>
                  <td style={{ textAlign: "center" }}>{it.response_count || 0}{it.unread_response_count ? ` (${it.unread_response_count} new)` : ""}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{fmtTime(it.last_checked_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn-sm" onClick={() => rowAction(it)}>
                      {it.internal_status === "active" ? "Check responses" : it.internal_status === "error" ? "Retry" : "Add to Queue"}
                    </button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && <tr><td colSpan={9} className="muted" style={{ padding: 18, textAlign: "center" }}>
                No items yet — click <strong>+ Add item</strong> to start your inventory.
              </td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {editing && <ItemForm item={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {drawerId && <ItemDrawer itemId={drawerId} onClose={() => setDrawerId(null)}
        onChanged={load} onEdit={(it) => { setDrawerId(null); setEditing(it); }} />}
    </>
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

  const act = useCallback(async (label, fn, close = false) => {
    setBusy(label);
    try { await fn(); if (close) onClose(); else await load(); onChanged?.(); }
    catch { /* surfaced in the activity log */ } finally { setBusy(""); }
  }, [load, onChanged, onClose]);

  const postNow = () => act("post", async () => {
    await jpost("/api/inventory/queue", { item_ids: [itemId] });
    await jpost("/api/inventory/queue/run?dry_run=true");
  });

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", zIndex: 40 }} />
      <div style={{ position: "fixed", top: 0, right: 0, height: "100%", width: 440, maxWidth: "92vw",
        background: "var(--surface-1, #fff)", borderLeft: "1px solid var(--border, #d0d7de)", zIndex: 50, overflowY: "auto", padding: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: 16 }}>{item?.title || "Item"}</strong>
          <button className="btn btn-sm" onClick={onClose} aria-label="Close">✕</button>
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

            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 16 }}>
              <button className="btn btn-sm" onClick={() => onEdit(item)}>Edit</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("queue", () => jpost("/api/inventory/queue", { item_ids: [itemId] }))}>Add to queue</button>
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={postNow}>{busy === "post" ? "Posting…" : "Post now"}</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("check", () => jpost("/api/inventory/check-responses"))}>Check responses</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("sold", () => jpost(`/api/inventory/items/${itemId}/sold`))}>Mark sold</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => act("archive", () => jpost(`/api/inventory/items/${itemId}/archive`))}>Archive</button>
              <button className="btn btn-sm" disabled={busy}
                onClick={() => { if (window.confirm("Delete this item?")) act("delete", () => jdelete(`/api/inventory/items/${itemId}`), true); }}>
                Delete
              </button>
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
function QueueBody() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api("/api/inventory/queue").then((d) => setQueue(d.queue || [])).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  const run = async (path, label, useSel) => {
    setBusy(true); setMsg("");
    try {
      const r = await jpost(path, useSel ? { task_ids: [...selected] } : undefined);
      setMsg(`${label} — ${Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(", ")}`);
      setSelected(new Set()); load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  };
  const toggle = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn btn-primary" disabled={busy} onClick={() => run("/api/inventory/queue/run?dry_run=true", "Ran queue")}>
          {busy ? "Running…" : "Run queue"}
        </button>
        <button className="btn" disabled={busy} onClick={() => run("/api/inventory/queue/retry", "Retried failed")}>Retry failed</button>
        <button className="btn" disabled={busy} onClick={() => run("/api/inventory/queue/clear", "Cleared completed")}>Clear completed</button>
        <button className="btn" disabled={busy || !selected.size} onClick={() => run("/api/inventory/queue/remove", "Removed", true)}>Remove selected</button>
        <span className="muted" style={{ fontSize: 12 }}>Posting is simulated until a channel is signed in (Settings).</span>
      </div>
      {msg && <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>{msg}</div>}

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th></th><th>#</th><th>Item</th><th>Status</th><th>Attempts</th><th>Last attempt</th><th>Error</th></tr></thead>
            <tbody>
              {queue.map((t) => (
                <tr key={t.id}>
                  <td><input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)} /></td>
                  <td>{t.position}</td>
                  <td>{t.item_title || t.item_id}{t.item_price ? ` · $${t.item_price}` : ""}</td>
                  <td><Pill value={t.status} colors={TASK_STATUS_COLOR} /></td>
                  <td style={{ textAlign: "center" }}>{t.attempts}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{fmtTime(t.last_attempt_at)}</td>
                  <td className="muted" style={{ fontSize: 12, color: t.error_message ? "#dc2626" : undefined }}>{t.error_message || "—"}</td>
                </tr>
              ))}
              {queue.length === 0 && <tr><td colSpan={7} className="muted" style={{ padding: 18, textAlign: "center" }}>
                Queue is empty — select items above and “Add to queue”.
              </td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Activity Log                                                                */
/* -------------------------------------------------------------------------- */
function ActivityBody({ compact }) {
  const [log, setLog] = useState([]);
  const [handoffs, setHandoffs] = useState([]);
  const load = useCallback(() => {
    api(`/api/inventory/log?limit=${compact ? 20 : 80}`).then((d) => setLog(d.log || [])).catch(() => {});
    if (!compact) api("/api/runtime/handoffs?open_only=true").then((d) => setHandoffs(d.handoffs || [])).catch(() => {});
  }, [compact]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const resolve = async (id) => { await jpost(`/api/runtime/handoffs/${id}/resolve`); load(); };

  const statusDot = { ok: "#16a34a", error: "#dc2626", info: "#0ea5e9" };
  return (
    <>
      {!compact && handoffs.length > 0 && (
        <div className="panel" style={{ marginBottom: 12, borderLeft: "3px solid #ea580c" }}>
          <div className="panel-header"><div>The loop needs you <span className="muted">({handoffs.length})</span></div></div>
          {handoffs.map((h) => (
            <div key={h.id} style={{ padding: "8px 12px", borderTop: "1px solid var(--border,#eee)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong style={{ fontSize: 13 }}>{h.why}</strong>
                <button className="btn btn-sm" onClick={() => resolve(h.id)}>Resolve</button>
              </div>
              <div className="muted" style={{ fontSize: 12 }}>{h.suggestion}</div>
            </div>
          ))}
        </div>
      )}
      <div className="panel">
        <div style={{ padding: "6px 4px" }}>
          {log.length === 0 && <p className="muted" style={{ padding: 14, margin: 0, textAlign: "center" }}>No activity yet.</p>}
          {log.map((e) => (
            <div key={e.id} style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "6px 12px",
              borderBottom: "1px solid var(--border,#f0f0f0)" }}>
              <span style={{ width: 7, height: 7, borderRadius: 7, background: statusDot[e.status] || "#8b949e", flex: "0 0 auto" }} />
              <span className="muted" style={{ fontSize: 12, width: 150, flex: "0 0 auto" }}>
                {new Date(e.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </span>
              <span style={{ fontSize: 13 }}>{e.message}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Active Listings + Messages (focused sub-sections)                           */
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
              {listings.length === 0 && <tr><td colSpan={8} className="muted" style={{ padding: 18, textAlign: "center" }}>No active listings yet. Post items from the queue.</td></tr>}
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
      </p>
      <div className="panel" style={{ marginTop: 8 }}>
        <div className="panel-header"><div>Responses <span className="muted">({withMsgs.length})</span></div></div>
        {withMsgs.length === 0 ? <p className="muted" style={{ padding: "12px 14px", margin: 0 }}>No buyer responses yet.</p> : (
          <table className="runs-table">
            <thead><tr><th>Item</th><th>Channel</th><th>Unread</th><th>Total</th><th>Last checked</th></tr></thead>
            <tbody>
              {withMsgs.map((l) => (
                <tr key={l.id}><td>{l.item_title}</td><td className="muted">{l.channel}</td>
                  <td style={{ textAlign: "center", color: l.unread_response_count ? "#ea580c" : undefined }}>{l.unread_response_count || 0}</td>
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

