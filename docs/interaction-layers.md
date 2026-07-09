# Interaction layers — and why Facebook login keeps reopening the same wound

> **STATUS (2026-07-08): FIXED.** The bespoke `/facebook_login` endpoint described below as "Layer B"
> was **deleted** (commit `6775499`); FB login now runs on the resilient AX/node layer. This note is
> kept as the *case study* for why we drive through the AX layer — the history below is the argument,
> not a live bug. If you are here to "fix FB login again," the fix is almost certainly a change to the
> **recipe** (`facebook_recipe.py`) or the **routing**, never a new hardcoded-selector endpoint.

This note exists because the same regression kept getting re-litigated session after session
inside one imperative endpoint, instead of being captured once.

## There are two interaction layers

**Layer A — the AX / node driver (the resilient one).**
`apps/mcp/app/executor/driver.py` + `apps/mcp/app/observer/ax_proposer.py`.
It reads the **accessibility tree**, finds an element by **role / accessible-name**, gets a
`backend_node_id`, and drives it **by node**: `DOM.resolveNode` → `callFunctionOn` `.click()` /
`.focus()` + `insertText`. Because it addresses elements by role/name and acts by node id, it
**survives DOM reshuffles** — `<button>` becoming `<div role=button>`, class churn, layout shifts.
This is the "CDP-AX" interaction layer the whole system is built on.

**Layer B — the bespoke `/facebook_login` endpoint (the brittle one).**
`apps/mcp/app/main_server.py` (`facebook_login`, ~line 931).
It **does not touch the AX tree**. It runs `document.querySelector` with **hardcoded CSS
selectors** — `input[name=email]`, `input[name=pass]`, `button[name=login]` — via
`Runtime.evaluate`, then `insertText` + a coordinate click from `getBoundingClientRect`. Every
assumption about Facebook's DOM is baked into these 60 lines.

## FB login WAS hardcoded to Layer B — that was the bug (now fixed)

Until 2026-07-08, `channel_browser.py` set `login_path: "/facebook_login"` for `facebook_marketplace`,
and `channel_login` POSTed straight to that endpoint **every time**. Login never got Layer A's
node-based path — the regression was structural: login was special-cased onto a hardcoded-selector
fast-path that **bypassed the resilient layer**. That routing is gone (`login_path` removed,
`_drive_login_form` now scans → matches by role/name → drives by `backend_node_id`).

## It's the same wound reopening — read the last three commits

- `e805a62` (Jul 2): created `/facebook_login`; comment claimed the selectors were "stable on
  facebook.com/login for years."
- `09311ed` (Jul 8): "clicks the real Log In button (FB ships it as a div)" — `button[name=login]`
  broke because FB now renders Log In as `<div role=button>`. Patched with a text-match fallback.
- `e996d4f` (Jul 8): "fills React inputs via CDP `Input.insertText`" — the old per-char
  `dispatchKeyEvent` + native `.value` set didn't update React's controlled state, so React wiped
  the values on the next render and the form submitted empty → reload to a blank form.
  **Caveat:** this fix was validated read-only; full real-credential login was *never confirmed*.

Each is a reactive patch to the same function, each triggered by an FB DOM change. **Layer A would
not have cared about `button`→`div`,** because it finds by role/name and drives by node id.

## Whose fault — Facebook's or ours? Both, and they compound

Facebook genuinely changes and is "deliberately hostile to automation" (see the header of
`facebook_recipe.py`). That's the *trigger*. But the reason each change *breaks us* is that we built
and kept extending Layer B, which hardcodes FB's DOM. The login flow never "reverted to Python" — it
**never used Layer A**. A bespoke fast-path for a small known form felt simpler and more
controllable than the full observe→propose→drive loop; that trade bought simplicity and paid it back
in brittleness.

## The durable fix — done (2026-07-08, commit `6775499`)

1. **FB login now runs through Layer A.** The login wall is observed via the AX tree (`/ax_scan`),
   the email / password fields and the Log In control are found by **role / accessible-name**
   (`facebook_recipe.match_login_fields`), and each is driven by `backend_node_id` through the
   humanized driver. The hardcoded-selector endpoint was **deleted**, not shimmed.
2. **The learnings landed in `facebook_recipe.py`, not an endpoint.** button→div and React-controlled
   inputs (`Input.insertText`) plus the human gates (checkpoint / 2FA / captcha) live in that
   distilled recipe — where the next session can see them — via the teacher→distill loop.

The meta-rule (now proven): **prefer the AX/node interaction layer over bespoke DOM workarounds, and
capture domain quirks in the distilled recipe.** See PRINCIPLES.md §6 and
`LEARNINGS.md` (2026-07-08 FB-login entry).
