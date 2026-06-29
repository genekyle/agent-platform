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

---

*Add a principle here whenever a mental model proves load-bearing. Prefer encoding it as an invariant
in code and linking the enforcement point.*
