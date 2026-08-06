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
┌────────────────────────────────────────────────────────────────────────────┐
│ session bar  #21 "data engineer" · Nashua NH · 50mi · Indeed · page 2  live │
├──────────────┬──────────────────────────────────┬──────────────────────────┤
│ RAIL         │ WORK SURFACE                     │ INSPECTOR                │
│ where we are │ what needs attention NOW         │ why this / why that      │
│              │                                  │                          │
│ ✓ Session    │  PAGE 2                          │ Rule applied             │
│   ready ·    │  Senior Data Engineer            │ Observed                 │
│   signed in  │  MFS Investment · indeed         │ Window                   │
│ ✓ Page 1     │                                  │ Confidence + witnesses   │
│   1 of 21    │  [ Work this step ]  or: …       │ Alternatives             │
│   picked     │                                  │ Evidence                 │
│ ▶ Page 2     │  ▸ End this application another…  │ Intended action          │
│   • reviewed │                                  │ Result                   │
│   • Senior…  │                                  │                          │
└──────────────┴──────────────────────────────────┴──────────────────────────┘
```

## 3. The grouping — the ladder's own shape (revised 2026-08-05, second pass)

The first cut imposed five fixed phases (Setup · Discover · Decide · Execute · Verify) — a textbook
pipeline the work does not walk. The checkpoint ladder's real shape is **a preamble climbed once,
then a cycle per results page**, and forcing every page through one shared "Decide" box meant page
2's choice would overwrite page 1's in the display — exactly the contextual clobbering this surface
exists to prevent. Operator-directed revision: the rail now mirrors the ladder itself.

| Group | Owns | Source |
|---|---|---|
| **Session** | `provisioned`, `authenticated`, `query_entered`, `radius_set`; clean-start and login legs; `operator_end` | `session_checkpoints.PREAMBLE` |
| **Page N** (one per page) | `page:N`, `select:N`, and — for the current page — every queued application with its mini-rungs and terminal flag | the rolling rungs + `apply_steps.PREFIX` |

A **past page collapses to its record** — what was decided there, from the select rung's own
evidence ("1 of 21 picked by operator") — never to a bare "done". The current page is the work.
There is still no end flag: the ladder stops when there is no next page, and `operator_end` is a
session-level stop.

The work-surface *moments* inside a page (read → choose → work each application → accounted for)
are focus kinds, not rail boxes — the rail records, the surface asks.

## 4. The one authoritative workflow state

`cockpit/lifecycle.js` is a pure function from the read model to the whole cockpit. **Nothing
downstream decides for itself whether a control belongs on screen.**

`deriveCockpit(panel, {picks}) -> { current, groups, focus, blocker, cycle }`

The focus resolves in priority order, and every branch is a fact about the world:

1. **a session/end blocker** — the truest statement available: the session is stopped, and where;
2. **an application in flight** — it holds the page open, so it *is* the work. This is the branch
   that resolves the screenshot: the ladder said "page 1 reviewed, next" while an application was
   mid-flight, and both were true — only one was the work;
3. results on screen — the page's decision (first time, or choosing again after the queue landed);
4. at the start line with nothing read yet;
5. still climbing — the preamble.

The current group follows from the focus's kind: page moments belong to `page:N`, everything else
to `session`. Scopes never mix: the current page's pick count is the local draft or its own select
rung's evidence — never `p.picks`, the session-wide approved list.

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

## 6a. Getting there — the cockpit is a page, not a tab (2026-08-05, third pass)

The cockpit began as one tab inside the domain workspace, which was wrong twice over. Practically:
it rendered under a breadcrumb, a hero, a sign-in card, an automation card and a ten-tab row, so
the operating surface for a live drive started a full screen down and read as a "lite" embed of
itself (operator: *"it looks like the way I accessed it is essentially a lite version"* — it was
the full thing, squeezed). Conceptually: **a session is not a property of a domain** — it is one
focused Chrome working one task, with cross-domain errands inside it (`PLAN_session_control_panel`
§1) — so reaching a session THROUGH a domain was the wrong door even when it worked.

Now: **Cockpit is a top-level destination** (global sidebar, second position) with the session as
its unit and its own tabs:

| Route | Meaning |
|---|---|
| `/cockpit` | follow the live session, whoever's it is |
| `/cockpit?domain=x` | follow domain x's active session (domain workspaces link here) |
| `/cockpit/:id` | pinned to one session — the URL is the choice, so it survives reload |
| `/cockpit/:id/journal` | that session's window record |

Cockpit-local tabs: **Live** (the three panes) and **Journal** (the window census + timeline from
`/api/session_control/{id}/windows` — the multi-window story that previously had no surface). The
picker spans EVERY domain's sessions, labelled `#id · domain · status`.

The domain workspaces keep a doorway — a right-aligned `Cockpit →` LINK in the tab row (an arrow,
because it navigates; it is not a content tab) — so the natural domain-first path still works while
the destination stops being an embed. The old `SessionControlPanel.jsx` is deleted; the keyed inner
lives at `cockpit/SessionCockpit.jsx` and the page at `cockpit/CockpitPage.jsx`.

## 6b. Session awareness

Operator-directed, same pass: *"different sessions may clobber each other in terms of contextual
history."* The mechanism is structural, not defensive:

- `CockpitPage` (outer) owns **which** session: it **polls** the session list (the old
  panel looked once on mount, so a session provisioned later never appeared), offers a picker
  across every domain's sessions, and marks a non-active session in the bar.
- `SessionCockpit` (inner) owns everything **about** one session, and is mounted with
  **`key={sessionId}`** — a session change unmounts it, so the panel read model, the `last_step`
  carry, the note draft, the pinned selection, the picker detour and the settle clock all die with
  the session they described. Without the key, `last_step` (deliberately carried across polls)
  would show session A's "ran the query" as session B's result.
- Picks were already safe: `useOrderedPicks` scopes its draft to `(session, page)` in localStorage.
- The bar shows **`#id`** — the session the cockpit narrates is never something to infer.
- A page turn (however initiated) resets the note draft, the detour and the selection: page 1's
  rationale must not ride into page 2's `/choose`.

## 7. Fixtures

`src/dev/cockpitFixtures.js` + `/cockpit-preview.html` render the three panes against captured
payloads with no API, no browser and no live session. Five states ship: the 2026-08-05 screenshot,
deciding a page, blocked on sign-in, nothing declared yet, and page 2 with page 1 walked (the
grouping case — a past page must collapse to its record, not a bare "done"). The harness counts the primaries on
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
- **Per-page application history.** A past page's applications are summarised only by the select
  rung's evidence — the read model keeps just the current page's queue. If the blackboard ever
  exposes past queues, past page groups can carry their full application records.
- The cockpit has not yet been driven against a **live** session — only fixtures. The read-model
  field shapes are taken from `_view()` in `routers/session_control.py`.
