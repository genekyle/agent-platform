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

---

## Layer A finds ELEMENTS. It does not model WIDGETS. (2026-07-15)

The two-layer picture above is right and incomplete. It answers *"how do I identify and drive one
control?"* — role + accessible name → `backend_node_id` → native drive. It has nothing to say about a
control that is really **several elements with a protocol between them**, and that gap is where the
Indeed distance pill quietly failed for weeks.

Be precise about what AX did and didn't give us, because it matters for what L3 can learn. With the
popup OPEN, AX sees the parts *perfectly well* — eight `li[role=option]`s **and** `button "Reset"` /
`button "Update"`. What it hands back for those buttons is exactly `{role, name, backend_node_id}`:
no `aria-controls`, no `owns`, **nothing encoding that `Update` COMMITS the listbox** or that
selecting an option merely *stages* it. AX gives you the **nouns**; the **protocol between them is
not in the tree** (and while the popup is closed, the footer isn't there at all — which is why a
scan taken at the wrong moment says the widget is just a pill and eight options).

So the old code did the Layer-A-shaped thing — "click the option" — got no error, and concluded the
widget was broken. It then reached for the two things we already reject (a coordinate click, then
React fiber internals), and finally hid the whole mess behind a silent URL rewrite that made every
caller *look* successful. Only a **screenshot** made the footer obvious to a human reading the page.

**Driving a widget as if it were an element is its own failure mode.** The fix is not a new way to
find things — Layer A is fine at that — it's a layer that models the *interaction sequence*:

> **Widget protocol layer** — identify the parts by SEMANTICS (ARIA roles/relationships + CSS), then
> drive the widget's own sequence natively, confirming at every step:
> **precondition → open → stage → confirm staged → commit → confirm from outside.**

It is the "in-betweener": above raw element-driving, below vision. No coordinates (they go stale the
instant a menu re-renders — ours landed outside and *dismissed* the popup). No framework internals
(Indeed's fibers moved). Just the widget's real contract, asserted rather than assumed.

Four environmental truths it has to encode, each of which cost a failed attempt:

| Truth | Why it bites |
| --- | --- |
| A popup will not render in a **hidden tab** | Same code passes from a probe that fronts the tab, fails from `/eval` which doesn't. Foregrounding is the *humane* path, not a trick — a human's tab is visible when they click. |
| **`.click()` does not focus** | A real mousedown focuses; the synthetic one doesn't. Without focus the widget's keyboard protocol is dead (`activeElement` stays `BODY`). `focus()` **then** `click()`. |
| The popup **dismisses on blur** | So it cannot survive HTTP round-trips. `open→select→commit` must run page-side in ONE evaluation. |
| The commit **destroys its own observer** | `Update` navigates, tearing down the execution context. `"Inspected target navigated"` IS the success signal; confirm from OUTSIDE. |

Lives in `apps/mcp/app/main_server.py` as `_POPUP_SELECT_JS` + `_popup_select()`, config-driven
(`opener_selector` / `option_selector` / `option_label` / `commit_names`) precisely so the next
staged-commit popup — a Workday prompt, an unknown ATS filter — reuses it instead of growing another
bespoke path. The per-step `log` it returns is the point: when a widget breaks, it says *which step*.

**And the fallback is now OFF by default.** `allow_url_fallback=false`. A silent fallback is exactly
how this stayed invisible: every caller still got its radius, so nobody ever learned the pill was
dead. A widget break must be LOUD. **The URL is confirmation, never the mechanism** — the same rule as
"click the link, don't jump to it" (PRINCIPLES §3), applied to filters.

### Does L3 need the widget layer? No — but it needs to SEE the states the widget moves through

Worth settling, because it's a natural worry ("L3 only knows AX-CDP — is the widget layer a second,
disconnected system?"). The two answer different questions and shouldn't be merged:

- **L3 is PERCEPTION** — *what state am I in?* It reads captures (AX sidecar + screenshot).
- **The widget layer is ACTUATION** — *how do I operate this control?* That's a **transition**
  concern: how to get from state A to state B. It belongs to the recipe + the `state_transition`
  (L4) model, which already learns from captured `observed → post_action` pairs.

Keeping them separate is right. L3 classifying "distance popup, option staged, not committed" is a
perfectly good *state*; it does not need to know that `Update` is what commits — that's the recipe's
job, and baking actuation into the classifier would just make it a worse classifier.

**But they must share a vocabulary, and that's where the real gap is — and it's a DATA gap, not an
architecture gap.** AX gives L3 everything it needs to *recognise* these states (the options carry
`selected`; the footer buttons are right there once open). What's missing is that **we have never
captured them**: no `distance_popup_open`, no `option_staged`. So L3 can't tell "I opened the popup"
from "I committed the filter", and the loop cannot verify its own progress through a multi-step
widget — which is precisely how a dead widget path went unnoticed.

The fix is the flywheel we already have: the protocol's per-step `log`
(`open` → `select/staged` → `commit`) names each intermediate state, so **capture + label at each
step** and both models get fed — L3 learns the states, L4 learns the verbs between them. That
generalizes: the same three states describe a Workday prompt and an unknown-ATS filter.

Two standing cautions when capturing those states: pin a `tab_url` that matches exactly ONE page
(see the 2026-07-15 capture entry — captures used to silently grab whichever tab was selected), and
a popup **dismisses on blur**, so a capture taken between steps may catch a *closed* widget. Capture
must run against the live open popup, not after a round-trip.

The open question this leaves: we found the `Update` footer by *looking at a picture* — AX listed the
button but never that it governs the listbox. Discovering a widget's **protocol** (as opposed to its
parts) may be exactly the narrow, expensive job the vision catchall should keep earning its place on
— not to click anything, but to state the contract once, so the cheap semantic layer drives it
forever after and L3 gets clean labels for the states along the way.
