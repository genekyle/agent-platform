# Interaction layers — and why Facebook login keeps reopening the same wound

This note exists because the same regression keeps getting re-litigated session after session
inside one imperative endpoint, instead of being captured once. If you are here to "fix FB login
again," read this first — the fix is almost certainly *routing*, not another patch to the endpoint.

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

## FB login is hardcoded to Layer B — that's the bug

`channel_browser.py` sets `login_path: "/facebook_login"` for `facebook_marketplace`, and
`channel_login` in `apps/controlplane-api/main.py` (~line 3912) POSTs straight to that endpoint
**every time**. Login never gets Layer A's node-based path. So the regression is structural: login
was special-cased onto a hardcoded-selector fast-path that **bypasses the resilient layer**.

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

## The durable fix (direction, not yet done)

1. **Route FB login through Layer A**: observe the login wall via the AX tree, find the email /
   password fields and the Log In control by **role / accessible-name**, drive them by node. Delete
   (or reduce to a thin shim over Layer A) the hardcoded-selector endpoint.
2. **Land the learnings in `facebook_recipe.py`, not in an endpoint.** That file is meant to be the
   distilled, teacher-refined state machine for this domain (button→div, React-controlled inputs, the
   human gates: checkpoint / 2FA / captcha). The whole teacher→distill loop exists for exactly this.
   When login teaches us something, it belongs there — where the next session can see it — not as a
   one-off imperative patch the next session can't.

The meta-rule: **prefer the AX/node interaction layer over bespoke DOM workarounds, and capture
domain quirks in the distilled recipe.** See PRINCIPLES.md §6.
