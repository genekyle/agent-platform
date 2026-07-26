# PLAN — the staleness detector

**Status: PROTOTYPE, built 2026-07-26 (operator-directed). The levels are guesses; the evidence is
real.** This doc says what exists, what it is deliberately not doing yet, and the one measurement
that turns it from a gauge into a decision-maker.

---

## 0. The question nothing answered

Three modules sit next to this one and none of them answer freshness:

| module | question |
|---|---|
| `perception/` | **where** are we — which state is this |
| `controller/reach.py` | **can we touch it** — do the controls exist |
| `controller/unexpected.py` | is this **where we expected** — did we land somewhere else |
| **`perception/staleness.py`** | **is what we are looking at still true**, and what does that cost |

A drive can pass all three on a view that went stale twenty minutes ago. This session produced
three of those in one afternoon — a session left open two days whose auth rung read a tab from
another site, a Chrome relaunched windowless still holding a profile, and a submit that raced a
navigation. None were perception faults, and no amount of witness accuracy would have caught them.

Operator, 2026-07-26: *"one of the states should be … a datapoint that should always be attached
… staleness could probably be a datapoint that remains like 'safe' or 'normal' but staleness
conditions can depend on cookie timing, time since last action etc."*

## 1. Shape: a datapoint, not a gate

Staleness rides along with **every** observation as `Bundle.staleness`, the way `belief` does. It
**advises and gates nothing** — deliberately, while the thresholds are guesses. Four verdicts:

```
CONTINUE : operable, carry on
REFRESH  : reload in place — cheap
RENEW    : reloading cannot repair this (logged out, session gone) — fresh state
HANDOFF  : we cannot SEE it well enough to judge — never guess a remedy from a blind reading
```

### The one rule that is safety, not heuristic

**A refresh is destructive when the page holds unsaved work.** A half-filled Workday application
is exactly the case. So `holds_unsaved_work` downgrades REFRESH → CONTINUE and RENEW → HANDOFF, at
every level, always. Freshness is never worth more than work, and the operator — not the detector
— decides whether to abandon typed answers.

`LiveActuator` sets that flag on a landed write intent (`set_text`, `select_option`, `set_date`,
`check_group`, `upload`) and clears it on navigation. `click` is deliberately excluded: it usually
commits or navigates rather than staging, and counting it would suppress the refresh remedy on
every page we ever touch.

## 2. Signals (prototype)

| signal | direction | today |
|---|---|---|
| `blind` | — | outranks everything → HANDOFF |
| `logged_in` | false is bad | false → RED / RENEW |
| `idle_s` | bigger worse | time since **we** last acted |
| `page_age_s` | bigger worse | time since the tab last navigated |
| `cookie_ttl_s` | smaller worse | **inert** — nothing reads cookies yet |

`None` means **not measured** and is recorded in `unmeasured`. It never scores as fresh — the same
rule the checkpoint ladder enforces one layer up, where an unknown must not read as an all-clear.

## 3. What has to be measured — the actual research

`THRESHOLDS` in `perception/staleness.py` is **one table of guesses**, kept in one place so
calibration is a diff to a table rather than a hunt through branches. `RULES_VERSION` stamps every
journaled row so rows written under different guesses are never pooled into one fit.

The prototype's real job is to **journal the raw ages**, not the level:

```
DecisionRecord.staleness_idle_s
DecisionRecord.staleness_page_age_s
DecisionRecord.staleness_level / _verdict / _rules
```

against `outcome` and `verified` in the same row. That makes the research question answerable from
drives we are already doing:

> **For each signal, at what value does the next action's failure rate rise?**

Rough shape of the analysis, once there are enough rows: bucket by `staleness_idle_s`, compute
P(outcome != ok) per bucket, look for the knee. Same for `page_age_s`. A signal with no knee is a
signal to drop — that is a real result, not a failure.

**Do not promote staleness into a gate, or into `bundle_to_prompt`, before that measurement.**
Putting an unmeasured level in front of the reasoner changes the feature contract on the strength
of a guess and makes every row journaled before it incomparable.

## 4. Known gaps, recorded so they are not re-derived

- **Cookie TTL is not read anywhere.** `cookie_expires_at` is always `None` today; the signal is
  inert until a CDP `Network.getCookies` / `Storage.getCookies` probe lands. It is wired so that
  landing it is a one-line change at the call site.
- **`idle_s` measures OUR inactivity, which says nothing about what the SITE did.** A server-side
  session timeout can fire at any idle value. This is the signal most likely to have no knee.
- **No site-specific priors.** Workday, Greenhouse and Indeed almost certainly have different
  session lifetimes; the table is global. Per-ATS thresholds are the obvious next refinement once
  there is enough data to fit even one curve.
- **Nothing consumes the verdict yet.** The session-control panel and the controller loop both
  observe without reading `staleness`. Wiring a consumer is easy and deliberately deferred: a
  remedy driven by a guessed threshold is worse than no remedy.
