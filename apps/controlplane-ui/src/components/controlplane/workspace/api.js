// Tiny fetch helpers shared by the cockpit components. One place for the base URL and
// JSON plumbing so every workspace panel talks to the API the same way.
export const API = import.meta.env.VITE_API_BASE_URL;

async function unwrap(r) {
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({})))?.detail;
    throw new Error(detail || `HTTP ${r.status}`);
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
