// Tiny fetch helpers shared by the cockpit components. One place for the base URL and
// JSON plumbing so every workspace panel talks to the API the same way.
export const API = import.meta.env.VITE_API_BASE_URL;

// FastAPI's 422 `detail` is a LIST of {loc, msg} dicts, not a string. Interpolating it raw
// rendered `[object Object]` in every error box, which is how a validation failure read to the
// operator as "nothing happened" — including the inert Submitted button (found 2026-08-19,
// explained 2026-08-20: job_id undefined -> pre-handler 422 -> unreadable detail).
function describeDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === "string") return d;
        const loc = (d?.loc || []).filter((p) => p !== "body").join(".");
        return loc ? `${loc}: ${d?.msg || JSON.stringify(d)}` : d?.msg || JSON.stringify(d);
      })
      .join("; ");
  }
  try { return JSON.stringify(detail); } catch { return String(detail); }
}

async function unwrap(r) {
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({})))?.detail;
    throw new Error(describeDetail(detail) || `HTTP ${r.status}`);
  }
  return r.json();
}

export const getJSON = (path) => fetch(`${API}${path}`).then(unwrap);

export const sendJSON = (path, method, body) =>
  fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(unwrap);

export const postJSON = (path, body) => sendJSON(path, "POST", body);
export const putJSON = (path, body) => sendJSON(path, "PUT", body);

// Mirror of domain_settings.approval_required on the server — the cockpit uses it to decide
// whether a task button runs immediately or asks first. `outwardFacing` marks the
// irreversible/published/sent actions (post a listing, submit an application, message, buy).
export function needsApproval(mode, outwardFacing) {
  if (mode === "manual") return true;
  if (mode === "supervised") return outwardFacing;
  return false; // autopilot
}

export const fmtTime = (t) =>
  t ? new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
