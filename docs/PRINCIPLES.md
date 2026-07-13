# Operating principles & invariants

The mental models this system is built to embody. They live here (not just in someone's head or a
chat) so we apply them **systematically** and don't re-derive or forget them. When a principle is
enforced by code, the enforcement point is named — a principle backed by an invariant beats a
principle backed by discipline.

---

## 1. State is context-bound — provenance travels with the data
A value in the blackboard (or anywhere) is **not self-validating**. It only means something relative
to the context that produced it: *which cadence run, whether the session was authenticated when it was
gathered, and when.* The same session is **not** the same cadence run, which is **not** an authorized
context. Data gathered out of context (e.g. a shortlist extracted while logged-out, or from a prior
run) can **look valid but isn't** — acting on it is a "thought-bubble" error.

- **Enforced by:** `SearchState` provenance fields (`cadence_run_id`, `run_started_at`,
  `gathered_authenticated`, `stale`), `start_cadence_run`, and `search_data_actionable` in
  `apps/controlplane-api/apply_state_store.py`. Triage/approve are valid **only** within a current,
  authenticated run; logging in does **not** retroactively bless logged-out data (it goes `stale`).

- **Live UI/session state goes stale too — refresh before you operate.** Beyond blackboard data, the
  browser itself decays with time: CDP `backend_node_id`s churn, forms/CSRF tokens and ATS sessions
  time out, "Did you apply?" prompts appear, and a tab you left minutes ago may have navigated away
  from where you left it. **Before driving a tab — or pressing anything that scans it (the Account
  Manager's Create-Account / ▶ Login button, which AX-scans the live form) — reload the tab and
  re-verify the expected state.** Never assume the tab is where you left it. This is cheap insurance
  against the "button did nothing / it clicked the wrong element / it closed the wrong tab" surprises
  (all seen live 2026-07-12). Treat stale live state as a first-class hazard, like stale data.

## 2. Authenticate before you automate
The agent must not run searches or task automation on a logged-out session. Login first, automate
second.
- **Enforced by:** the login gate — `auth_required` blocker + `proceed_decision` (`apply_state_store.py`),
  fed by the `/auth_state` probe each `session_state` cycle.

## 3. Reach states by clicking, like a human — URL-forcing is last-ditch
Default navigation is **clicking the on-page link/button**. Typing/forcing a URL is a fallback only,
because URL-jumping is flag-raising (bot-detection) behavior on a real account. Generalizes the older
"never URL-jump to job-detail pages / don't churn tabs" rule to **all** navigation.
- **Status:** policy now; to be baked into the driver/planner action selection (today `/navigate` is
  URL-based — use it only when there's no clickable path).

## 4. Capture per *state*, not per keystroke — and never capture secrets
The unit of training capture is a meaningful page **state** (the thing the L3 classifier / state-graph
learn from), not each button press. In credential flows we record **state identity only** (url path /
title / auth signals) and **never** screenshot or store passwords, codes, or field values.

## 5. Heuristics must be validated against real pages
Deterministic detectors (captcha active/passive, auth logged-in/out) are cheap but easy to get subtly
wrong; validate them against the actual live pages before trusting them. Burned twice already: a
reCAPTCHA `bframe` *present* ≠ *shown* (visibility probe), and `secure.indeed.com` host ≠ "in the auth
flow" (path-based check, since it also serves logged-in `/settings`).

## 6. Drive through the AX/node interaction layer — not bespoke DOM workarounds
There is one resilient way to act on a page: read the **accessibility tree**, find the element by
**role / accessible-name**, and drive it **by `backend_node_id`** (`DOM.resolveNode` →
`callFunctionOn`). It survives DOM reshuffles (`<button>`→`<div role=button>`, class churn, layout
shifts). A hardcoded-`querySelector` + coordinate-click fast-path feels simpler for a small known
form and is exactly why Facebook login has broken three times in a week — each FB DOM change is a
reactive patch to one brittle endpoint. When a flow breaks, **first ask which layer it's on**; don't
diagnose fields or build a one-off CDP script until you've confirmed it's even using the robust path.
Domain quirks (button→div, React-controlled inputs, the human gates) belong in the distilled recipe
(`facebook_recipe.py`) via the teacher→distill loop, **not** re-litigated in an imperative endpoint
the next session can't see.
- **Enforced by:** `apps/mcp/app/executor/driver.py` (Layer A) as the one drive path; FB login was
  routed through it on 2026-07-08 (commit `6775499`) — the bespoke `/facebook_login` endpoint and
  `channel_browser.py`'s `login_path` are **deleted**, and `facebook_recipe.match_login_fields` holds
  the domain quirks. See `docs/interaction-layers.md` and `docs/LEARNINGS.md`.

## 7. No single golden path — the golden path is the least-steps route through the UI in front of you
Accounts (and site variants) render **different UIs**, so a goal like "open Marketplace" has
different routes per account. Don't assume one fixed recipe spine works everywhere. Observe the
actual UI (CDP-AX `/ax_scan`), then take the **shortest available verified path** to the goal.
Recipes hold alternative routes; the state-transition graph accumulates them across accounts, and the
planner graph-searches it for the fewest-steps path. "Golden path" ≡ fewest steps for *this* UI —
verify each edge, never assume URL implies state.
- **Status:** policy now (discovered driving FB Marketplace across accounts); recipes/planner to hold
  per-variant routes + shortest-path selection over the observed state graph. See
  memory `project_per_account_ui_paths`.

---

*Add a principle here whenever a mental model proves load-bearing. Prefer encoding it as an invariant
in code and linking the enforcement point.*
