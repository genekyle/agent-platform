# SESSION 19 — one identity per control: every address goes through the AX door

_Written 2026-08-26. Pick this up cold; read `docs/PLAN_generalization_v1.md` §0 class 4 and §2
P3 first, then the 2026-08-25 (second) LEARNINGS entry — its four-resolvers table is this
session's spec._

## The problem in one paragraph

Four addressing paths hold four notions of what identifies a control, and each failed live on a
control the AX layer resolved correctly every time it was used directly (08-25, measured):
`apply_prompt_select` by prompt field-name (blind to an `aria_listbox`), `check_group` by its own
label derivation (`no_option` while `n_boxes: 1`), `/execute`'s selector path by label proximity
(three empty-named textareas resolved to a NEIGHBOR's question — stably), and value-writes at
`.value` (invisible to widgets with no `onChange`). Worse, the paths interact: on Paylocity the
census name and the resolver name **each rejected the other and neither worked** (08-19); on
PeopleAdmin one heading wore three links' names and the container won (08-19); on Cornerstone the
off-screen twin of a duplicate name won (08-24); `/select_prompt`'s selector path had a 2-tuple
unpack bug and **had never once run** (08-24). And commit-bearing clicks default to JS
`.click()`, which is untrusted — eleven failed mechanisms on one Workday date field before one
trusted pointer gesture landed first try (08-25).

## The work

**1. One door.** All addressing converges on the AX resolver — role + accessible name →
`backend_node_id`, with the rules it already enforces (EXACT → LEADING → anywhere with ambiguity
as refusal; interactive beats container; **visible beats off-screen** — add this tiebreak, it is
the Cornerstone lesson). Human labels, census names, and DOM ids are ALIASES resolved through it;
a refusal names every alias it tried, so the two-name deadlock becomes one legible refusal
instead of two mutually-exclusive rejections.

**2. The exemption list, written down.** Paths that genuinely cannot go through the door enter an
explicit exemption table with a reason — the S14 route-inventory pattern applied to addressing:
position mapping (empty-AX-name families, from S18), file inputs resolved to the INPUT not the
button (08-24), `/probe`. **That list IS the finding.** Anything not on it that addresses a node
some other way is a test failure.

**3. Trusted gestures by default.** Commit-bearing clicks go through coordinate mode
(`Input.dispatchMouseEvent`, real press/release). The untrusted-click recognizer stops being a
retry and becomes the default; JS `.click()` demotes to the exemption list for the cases that
need it. The 08-25 rule inverted: don't diagnose eleven ways to write a value into a widget whose
problem is that it never received a gesture.

## Then drive, and let the drive prove it

Re-drive a stored deadlock shape live — Paylocity's identity fields by name (the 08-19 deadlock),
or the MACOM upload (input-not-button). Then one ordinary Indeed/Workday form step to prove the
common path did not regress. Every address in the drive journals which door it went through.

## Definition of done

* The four failure modes from the 08-25 table each have a pinned test through the ONE door
  (or a listed exemption).
* The exemption table exists in code with reasons, asserted by a test the way S14 asserts
  `@journaled`.
* Visible-beats-off-screen tiebreak landed with the Cornerstone shape as its test.
* A live drive fills a previously-deadlocked form by name, and the trail shows the door used.
* `docs/LEARNINGS.md` entry, including what ended up on the exemption list and why.

## What NOT to do

* **Do not rewrite the driver or the protocol layer.** This is §6 *finished* (one door), not
  replaced. The stage→commit widget protocols stay; they get their addresses from the door.
* **Do not delete the escape hatches.** `/probe` and position mapping are exemptions with names,
  not shame — an unnamed workaround is the thing this session ends.
* **Do not "fix" `check_group`'s read-back here.** That is S20's evidence contract; this session
  is identity only. One change per attempt (§13).
