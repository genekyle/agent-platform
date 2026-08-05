# The session cockpit

Status: **built 2026-08-05**, operator-directed. This document is the source of truth for the live
session control surface. It supersedes the layout in `PLAN_session_control_panel.md` §6 (the
top-to-bottom stack of five numbered sections), which the build outgrew.

`01`–`08` in this directory cover the shell and the *dashboards*. The session controller is neither:
it is an operating surface for a live drive, and it needed a grammar of its own. It did not have
one, and that is what went wrong.

---

## 1. What was wrong, measured

The old `SessionControlPanel.jsx` was 1,289 lines with **38 interactive controls** reachable on one
screen, **12 distinct API endpoints**, **12 top-level conditional cards** and 33 inline style
objects. The operator's 2026-08-05 screenshots ran to about 2.5 viewport-heights.

The panel had **no model of which moment the session was in.** Each capability had been added as its
own conditional card that decided for itself whether to render, so six surfaces could claim the
operator's attention simultaneously and none was labelled as the live one. On the screenshotted
state, the screen showed at once:

1. an **unsaved pick** for *Azure Data Engineer / Stellar IT*;
2. an **in-flight application** for a different job, *Senior Data Engineer / MFS Investment*;
3. a ladder rung asserting **"Page 1 picks made ✓"**, about neither of them.

Three answers to "what are we doing now". Operator, verbatim: *"too many buttons to press and too
much information and to a point where we don't even know what's going on."*

Seven things were said twice on that one screen — most sharply **"Work this step"**, the same label
on the same `/apply_step` endpoint, rendered in the arbitration band *and* again on the queue step,
both styled primary, about 700px apart. The band had been built precisely to end the
two-primary-buttons problem, and it shipped *beside* the button it was meant to replace. That is the
shape of every regression here: **the fix was correct and additive, and additive was the bug.**

Two counters lied. `Checkpoints 4/4` counted only the four preamble rungs while six were listed, so
it read as complete beside an unfinished list; and rung 5 (*Page 1 reviewed*, next) rendered above
rung 6 (*Page 1 picks made*, held).

One more, found while rebuilding and not visible in any single screenshot: in `ai-ops.css`,
`.btn-primary` sat **above** `.btn`/`.btn-sm` at equal specificity, so on the usual markup
(`class="btn btn-sm btn-primary"`) the later rule won and **every primary button in the product
rendered as a plain one.** A hierarchy that exists only in the JSX is not a hierarchy. Fixed by
moving the primary rules after the base rules.

## 2. The shape (operator-specified)

> Left: session rail — compact central stepper, grouped by lifecycle. Show only status, current
> step, and blockers. Completed steps collapse automatically.
> Center: work surface — the one thing the operator can act on now. Avoid showing every stage's
> controls here.
> Right: decision inspector — persistent "Why?" for the selected event or task.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ session bar   "data engineer" · Nashua, NH · 50mi · Indeed · page 1  live │
├──────────────┬──────────────────────────────────┬────────────────────────┤
│ RAIL         │ WORK SURFACE                     │ INSPECTOR              │
│ where we are │ what needs attention NOW         │ why this / why that    │
│              │                                  │                        │
│ ✓ Setup      │  EXECUTE                         │ Rule applied           │
│ ◉ Discover   │  Senior Data Engineer            │ Observed               │
│ ✓ Decide     │  MFS Investment · indeed         │ Window                 │
│ ▶ Execute    │                                  │ Confidence + witnesses │
│   • Senior…  │  [ Work this step ]  or: …       │ Alternatives           │
│ ○ Verify     │                                  │ Evidence               │
│              │  ▸ End this application another…  │ Intended action        │
│ page/cycle   │                                  │ Result                 │
└──────────────┴──────────────────────────────────┴────────────────────────┘
```

## 3. The lifecycle

Five phases: **Setup · Discover · Decide · Execute · Verify.**

| Phase | Owns | Source |
|---|---|---|
| Setup | `provisioned`, `authenticated`; clean-start, login legs | `session_checkpoints.PREAMBLE` |
| Discover | `query_entered`, `radius_set`, `page:N` | ditto + the rolling page rung |
| Decide | `select:N` — the results table and the ordered picks | `select_rung` |
| Execute | the apply queue: `open_pane` → `verify_identity` → `enter_apply` → `classify` → `account` → … | `apply_steps.PREFIX` |
| Verify | terminal flags; what landed and what it landed as | `apply_flag` / `SUBMIT_RUNG` |

Setup happens once. **Discover → Decide → Execute → Verify then cycles, once per results page**, and
Execute cycles again per application inside a page. The rail says so rather than drawing a straight
line the work does not walk. There is still no end flag: the ladder stops when there is no next page.

**Verify is honestly thin.** It is `current` only at `operator_end`; the rest of the time it is the
record of what landed. There is no pending-verification state in the data today, and inventing a
step for the symmetry of a five-box stepper would be a lie in the shape of a diagram.

## 4. The one authoritative workflow state

`cockpit/lifecycle.js` is a pure function from the read model to the whole cockpit. **Nothing
downstream decides for itself whether a control belongs on screen.**

`deriveCockpit(panel, {picks}) -> { current, phases, focus, blocker, cycle }`

The current phase resolves in priority order, and every branch is a fact about the world:

1. **a blocker** — the truest statement available: the session is stopped, and this is where;
2. **an application in flight** — it holds the page open, so it *is* the work. This is the branch
   that resolves the screenshot: the ladder said "page 1 reviewed, next" while an application was
   mid-flight, and both were true — only one was the work;
3. results on screen with the decision still open;
4. at the start line with nothing read yet;
5. still climbing — wherever the next rung lives.

A **focus** carries at most one `primary`, its `alternates`, and a `more` tail. A step we cannot
perform becomes `say`, never a button.

## 5. Registers

Everything on screen is exactly one of four things, and they must not read as peers:

- **Action** — the work surface. One primary, loudest thing on screen.
- **Object** — what you are acting on: the results table, the fill plan, the accordion, the account
  card. Lives inside the work surface; its own controls are never primary.
- **Evidence** — the inspector. Reasoning, witnesses, the window, staleness, the mini-step trail.
- **Record** — the rail. Status only, no actions at all.

## 6. Rules, with enforcement points

| Rule | Enforcement |
|---|---|
| One primary action on screen | Dev-time assertion in `SessionControlPanel.jsx` counts `.cockpit .btn-primary` after every render and warns. Allows 2 for a teacher proposal, where Correct is a deliberate peer of Go. |
| A new capability is a focus kind or an inspector row — **never a new top-level card** | `lifecycle.js` is the only place a phase or focus is defined; a card added elsewhere has nowhere to get its condition from. |
| The rail never acts | `SessionRail.jsx` renders no `onClick` but `onSelect`. |
| Say a fact once | The session bar owns query/where/radius/engine/page; the work surface never restates them. |
| `done` means done | A phase behind the current one with unfinished steps is `open`, never ticked — the fix for the `4/4` defect. |
| No inline styles | All of `cockpit.css`; the components carry none. |
| Nothing fabricated | The inspector renders an explicit *not measured* rather than a plausible sentence. Screenshots are declared unwired rather than faked. |

## 7. Fixtures

`src/dev/cockpitFixtures.js` + `/cockpit-preview.html` render the three panes against captured
payloads with no API, no browser and no live session. Four states ship: the 2026-08-05 screenshot,
deciding a page, blocked on sign-in, and nothing declared yet. The harness counts the primaries on
screen and shows the count.

**The screenshot state is kept deliberately.** It is the state the old panel could not narrate, and
any future change to this surface should be looked at against it first.

## 8. Owed

- **Screenshots in the inspector.** The operator asked for evidence/screenshots; the capture server
  writes them (`apps/mcp/app/artifacts.py`) but no session-scoped read endpoint exists. The
  inspector says so explicitly rather than leaving a gap.
- **Deduplicating the domain's tabs.** Indeed carries 10 tabs, of which *Session control*, *Live
  drive* and *Activity* are three views of the same live session. `03-information-architecture.md`
  specifies six. Not touched here — it is an IA change, not a cockpit change.
- **A verification step with something in it**, if and when the data supports one (§3).
- The cockpit has not yet been driven against a **live** session — only fixtures. The read-model
  field shapes are taken from `_view()` in `routers/session_control.py`.
