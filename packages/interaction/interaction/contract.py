"""The frozen contract of the Interaction API (V1).

    The model says WHAT. The recipe says WHERE. The API says HOW. The journal says
    WHAT HAPPENED.

This module is the WHAT and the WHAT-HAPPENED: the closed verb vocabulary a model may
emit, and the discriminated outcome every endpoint must answer with. Keep it STABLE —
`journal.jsonl` rows are a training corpus, and a vocabulary that drifts mid-corpus
makes the rows before the drift untrainable. Bump INTENT_SCHEMA_VERSION on any change
to a member's MEANING; adding a new member is backwards-compatible (old rows stay valid).

--------------------------------------------------------------------------------------
Why an Intent layer when `select_stage.schema.ActionId` already exists
--------------------------------------------------------------------------------------
They are DIFFERENT ALTITUDES, not competitors. This was worth getting right: the repo
already carries three action vocabularies that don't agree (the frozen `ActionId` enum,
the DB's `ActionRegistry` seed which adds navigate/wait/press/any, and the executor's
driver which adds `upload`), and a fourth unrelated one would mean L4 trains on verbs
the selector can't emit.

  Intent   — a SEMANTIC operation on a FIELD.      select_option("Phone Device Type", "Mobile")
  ActionId — a PRIMITIVE operation on ONE NODE.    click(node 4821)

One intent EXPANDS into 1..N ActionIds (`intent_expands_to`). `select_option` is
[click opener, click option, click commit]; `set_text` is [clear, type]. So Intent sits
one layer ABOVE the frozen contract and does not replace it: the Haiku selector keeps
emitting ActionId, L4 learns to emit Intent, and the journal records both — which is what
makes the two vocabularies joinable rather than rival.

--------------------------------------------------------------------------------------
The outcome taxonomy is the anti-silent-success contract
--------------------------------------------------------------------------------------
Five of five bugs on 2026-07-15 were "reported success that didn't happen" (see
docs/PLAN_execution_api.md §1b). So `ok` means VERIFIED AT THE LAYER THAT COMMITS —
never "the call didn't throw". Every other member names WHICH step broke, which is
also precisely the intermediate-state vocabulary L3 lacks today (the loop cannot
currently verify its own progress through a multi-step widget).

Test for any new fallback: *if the primary silently fails, what does the caller see?*
If the answer is "success", it's the wrong fallback.
"""

from __future__ import annotations

import re
from enum import Enum

# Bumping this invalidates nothing at runtime (unlike SELECTOR_SCHEMA_VERSION, which
# busts a cache) — it marks a corpus boundary. Trainers should filter on it.
INTENT_SCHEMA_VERSION = "v1"


class Intent(str, Enum):
    """The closed verb vocabulary. The model emits ONLY these.

    No selectors, no JS, no backend_node_id — those are the recipe's and the API's
    business respectively. Small on purpose: the vocabulary IS the action space a local
    model has to learn, so every verb added here is a verb L4 must distinguish.
    """

    SET_TEXT = "set_text"              # field, value  (value="" IS the clear case)
    SELECT_OPTION = "select_option"    # field, value
    SET_DATE = "set_date"              # field, month, year, [day]
    CHECK_GROUP = "check_group"        # field, values[]
    UPLOAD = "upload"                  # field, path
    CLICK = "click"                    # control (semantic name)
    SCROLL = "scroll"                  # direction/amount — see the note below
    SUBMIT = "submit"                  # form
    DESCRIBE = "describe"              # field -> the widget's own account of itself
    OBSERVE = "observe"                # page -> one structured observation (see below)
    SCAN_REQUIRED = "scan_required"    # form -> what's required AND unanswered
    RESOLVE_ANSWER = "resolve_answer"  # question + options + canonical -> this widget's word
    PROBE = "probe"                    # DISCOVERY ONLY — raw JS. See the docstring below.


# SCROLL is not in docs/PLAN_interaction_api.md §3, and it is arguably a MECHANISM detail
# rather than a semantic goal — you don't intend to scroll, you intend to click something
# and the API scrolls it into view (driver._element_act already does exactly that). It is
# here anyway because pretending otherwise creates an unjournalable action: `scroll` is in
# the frozen ActionId enum, the loop emits it, and the 2026-07-15 drive used it constantly
# ("scroll-then-click the visible button"). A verb the system really emits and the
# vocabulary can't express is a hole in the corpus, not a purity win.
#
# OBSERVE is the read the teacher performed BY HAND, constantly: 4-5 probes per step to
# establish "where am I, what's unanswered, is anything blocking" before every decision. The
# 2026-07-17 live drive journaled 32 probes and most were exactly this. One structured verb
# replaces them — and it is the REASONER'S INPUT: observe() -> decide() -> act() is the loop
# a local model will eventually run, so the observation's shape is a training feature set,
# not just a convenience. Same admission test as SCROLL: a thing the system really does that
# the vocabulary can't express is a hole in the corpus.
#
# There is deliberately NO `clear` intent even though `clear:92` is the event log's second
# most common action: clearing IS set_text with an empty value. The `actions` column keeps
# the primitive, so nothing is lost — this is the Intent/ActionId altitude split doing its
# job rather than the vocabulary drifting down to the primitive layer.


# PROBE is the deliberate hole in the closed vocabulary, and it stays forever: a closed
# vocabulary cannot express the novel, and discovery meets novel contracts by definition.
# The discipline is not "never script" — it is "scripting is discovery, and discovery ENDS
# IN AN ENDPOINT". A probe is journaled like everything else (that is the entire reason it
# exists as an intent rather than as the un-logged /eval it replaces), so the gaps it
# reveals are visible in the corpus instead of dying with the session.
DISCOVERY_INTENTS = frozenset({Intent.PROBE})

# Read-only intents: they observe and change nothing, so a failure is cheap and they need
# no confirmation gate. Kept explicit because the gating logic must not have to guess.
READ_ONLY_INTENTS = frozenset({Intent.DESCRIBE, Intent.OBSERVE, Intent.SCAN_REQUIRED,
                               Intent.RESOLVE_ANSWER})


# --- what params each verb legitimately takes -----------------------------------------
# The shapes were documented in the Intent docstring above from day one and enforced NOWHERE.
# Found 2026-07-20 by putting a 1B model in the student seat: it emitted
# `click {field: "salary", value: "0"}` — a legal SHAPE (click is a real verb, `field` and
# `value` are real keys, confidence in range) that `parse_decision` happily accepted. It is not
# a harmless no-op: `LiveActuator` resolves a click as `control or name or VALUE`, so that
# decision would have clicked a control literally named "0" on a live job application.
#
# A JSON grammar constrains shape; only this table constrains SENSE. It is enforced on the
# MODEL rungs (student/Haiku) — compiled recipe programs don't pass through `parse_decision`,
# so rung 0 is unaffected.
#
# (`required`, `allowed`) — `allowed` is a superset of `required`. An empty `required` means the
# verb takes no mandatory addressing.
INTENT_PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    Intent.SET_TEXT.value:       (frozenset({"field"}),   frozenset({"field", "value"})),
    Intent.SELECT_OPTION.value:  (frozenset({"field"}),   frozenset({"field", "value"})),
    Intent.SET_DATE.value:       (frozenset({"field"}),   frozenset({"field", "month", "year", "day"})),
    Intent.CHECK_GROUP.value:    (frozenset({"field"}),   frozenset({"field", "values", "value"})),
    Intent.UPLOAD.value:         (frozenset({"field"}),   frozenset({"field", "path"})),
    # The one that bit us: a click addresses a CONTROL by its visible text. Never a field.
    #
    # `role` is OPTIONAL and defaults to `button` at the actuator. It is allowed because the
    # interaction layer's actual addressing is role + accessible name (PRINCIPLES §6) and a click
    # that cannot say the role is under-specified: everything that is not a button — a checkbox, a
    # link, a menuitem — becomes unaddressable through this verb. Live 2026-08-06: ticking Indeed's
    # "No work experience to add" through the teacher seam returned
    # `not_found: target not found by name (role=button)` on a checkbox that was plainly on the
    # page, and the only ways round it were a bespoke selector or a hand-driven click — both of
    # them the thing §6 exists to prevent.
    Intent.CLICK.value:          (frozenset({"control"}), frozenset({"control", "role"})),
    Intent.SCROLL.value:         (frozenset(),            frozenset({"direction", "amount"})),
    # SUBMIT takes `role` for the same reason, and still refuses to guess the control name.
    Intent.SUBMIT.value:         (frozenset(),            frozenset({"form", "control", "role"})),
    Intent.DESCRIBE.value:       (frozenset({"field"}),   frozenset({"field"})),
    Intent.RESOLVE_ANSWER.value: (frozenset({"field"}),   frozenset({"field", "value", "values"})),
    Intent.OBSERVE.value:        (frozenset(),            frozenset()),
    Intent.SCAN_REQUIRED.value:  (frozenset(),            frozenset()),
    Intent.PROBE.value:          (frozenset(),            frozenset({"script", "note"})),
}


def check_intent_params(intent: str, params: dict) -> str:
    """Why these params are wrong for this verb, or "" when they are fine. PURE.

    Returns a human-readable reason rather than a bool because the caller turns it into a
    journaled escalation — and "what exactly was wrong" is the training signal.
    """
    shape = INTENT_PARAMS.get(intent)
    if shape is None:
        return ""                       # unknown verb — the intent check upstream owns that
    required, allowed = shape
    keys = {k for k, v in (params or {}).items() if v is not None}
    missing = required - keys
    if missing:
        return (f"{intent} requires {sorted(required)} but got {sorted(keys) or 'nothing'}"
                f" (missing {sorted(missing)})")
    foreign = keys - allowed
    if foreign:
        return (f"{intent} does not take {sorted(foreign)} — it takes {sorted(allowed)}")
    return ""


class Outcome(str, Enum):
    """The discriminated result of an intent. `ok` is verified, not merely un-thrown."""

    OK = "ok"                        # verified at value_read_at -> continue
    NOT_FOUND = "not_found"          # field didn't resolve -> the recipe is stale, re-map
    AMBIGUOUS = "ambiguous"          # >1 match -> refuse; needs a more specific field
    NOT_OPENED = "not_opened"        # widget wouldn't open -> wrong opens_on? hidden tab?
    NOT_STAGED = "not_staged"        # clicked, never took -> protocol mismatch
    NOT_COMMITTED = "not_committed"  # staged, commit failed -> footer? navigation?
    NO_OPTION = "no_option"          # vocabulary miss -> /resolve_answer
    BLOCKED = "blocked"              # captcha / challenge / session -> escalate, never solve
    COMMITTED_UNCONFIRMED = "committed_unconfirmed"   # fired; this layer cannot see the result
    ERROR = "error"                  # unexpected exception — NOT a protocol outcome

# Two members are NOT in docs/PLAN_interaction_api.md §6. Both earned their place by being
# implemented rather than designed, which is the promotion rule working as intended:
#
# ERROR — every endpoint today ends in `except Exception: return {"ok": False, "detail": ...}`,
#   which maps to none of the eight protocol outcomes. Folding a websocket drop into
#   `not_found` would be the same lie the taxonomy exists to prevent: a mechanism failure
#   would read as a stale recipe and send us re-mapping selectors that were fine.
#
# COMMITTED_UNCONFIRMED — the plan's §6 assumes every endpoint CAN verify. The staged-commit
#   popup proves otherwise: clicking the footer's Update navigates, which tears down the very
#   execution context that would observe the result (_POPUP_SELECT_JS says so itself —
#   "THE COMMIT DESTROYS ITS OWN OBSERVER … CONFIRM FROM OUTSIDE"). Neither existing member is
#   honest there. `ok` is a silent success — if the commit quietly failed the caller would see
#   "success", which is precisely the §6 test for a wrong fallback. `not_committed` is the
#   opposite lie: a false negative makes a caller re-fire a commit that already worked, and a
#   double-fired commit is a double submit. So the third state is real and gets a name.
#   Caller's move: confirm from OUTSIDE the page (as /set_distance does via _read_radius —
#   "the URL is CONFIRMATION, never the mechanism"). `ok` is derived as `outcome == OK`, so
#   this correctly does NOT read as success.

#: Outcomes that mean "the page is not in the state the caller assumed" — i.e. re-observe
#: before retrying, don't just try harder.
STALE_STATE_OUTCOMES = frozenset({Outcome.NOT_FOUND, Outcome.NOT_OPENED, Outcome.AMBIGUOUS})


class WidgetType(str, Enum):
    """What a control IS, as reported by /describe_widget.

    The protocol endpoints dispatch on this instead of the caller knowing — which is what
    keeps the intent vocabulary small enough for a local model to speak. Every member is a
    shape observed on a live page; `unknown` routes to PROBE, and the probe's output is a
    new member here. This enum is therefore the flywheel's memory of widget shapes.
    """

    ARIA_LISTBOX = "aria_listbox"                # Workday State / Phone Device Type
    REACT_SELECT = "react_select"                # Greenhouse country/location/yes-no/months
    PROMPT_HIERARCHICAL = "prompt_hierarchical"  # Workday "How Did You Hear About Us?"
    NATIVE_SELECT = "native_select"              # a plain <select>
    RADIO_GROUP = "radio_group"                  # Indeed's employer-built radios
    CHECKBOX_GROUP = "checkbox_group"            # Greenhouse restrictions/languages
    SEGMENTED_DATE = "segmented_date"            # Workday spinbutton trio (calendar-only)
    MONTH_YEAR = "month_year"                    # Greenhouse month react-select + year input
    TEXT = "text"
    NUMBER = "number"
    FILE = "file"                                # a hidden <input type=file>
    UNKNOWN = "unknown"


#: How each widget's TRUTH is read. The single most valuable thing /describe_widget reports:
#: `.value` on a react-select holds transient search text and empties on blur, so verifying
#: there "confirmed" an empty field twice on 2026-07-15; Workday dates DISPLAY while the
#: model stays empty. The widget itself knows where its truth lives — nothing else has to.
VALUE_READ_AT: dict[WidgetType, str] = {
    WidgetType.ARIA_LISTBOX: "opener_label",
    WidgetType.REACT_SELECT: "[class*=singleValue]",
    WidgetType.PROMPT_HIERARCHICAL: "opener_label",
    WidgetType.NATIVE_SELECT: ".value",
    WidgetType.RADIO_GROUP: "aria-checked",
    WidgetType.CHECKBOX_GROUP: "checked",
    WidgetType.SEGMENTED_DATE: "aria-valuenow",
    WidgetType.MONTH_YEAR: "[class*=singleValue]",
    WidgetType.TEXT: ".value",
    WidgetType.NUMBER: ".value",
    WidgetType.FILE: "files.length",
    WidgetType.UNKNOWN: "",
}


#: Which ActionId primitives each Intent expands into — the bridge between this vocabulary
#: and the frozen `select_stage.schema.ActionId`. Values are ActionId *strings* rather than
#: the enum itself, because this package must not depend on the control plane (mcp imports
#: it too). Indicative, not prescriptive: the journal records what an intent ACTUALLY
#: expanded to; this is the expectation a diverging row is interesting against.
_EXPANSIONS: dict[Intent, tuple[str, ...]] = {
    Intent.SET_TEXT: ("clear", "type"),
    Intent.SELECT_OPTION: ("click", "click"),        # opener, option (+ click commit if footer)
    Intent.SET_DATE: ("click", "click"),             # opener, tile — or (type, type) segmented
    Intent.CHECK_GROUP: ("click",),                  # one click per value
    Intent.UPLOAD: ("upload",),
    Intent.CLICK: ("click",),
    Intent.SCROLL: ("scroll",),
    Intent.SUBMIT: ("submit",),
    Intent.DESCRIBE: (),                             # read-only
    Intent.OBSERVE: (),                              # read-only
    Intent.SCAN_REQUIRED: (),                        # read-only
    Intent.RESOLVE_ANSWER: (),                       # read-only (may cost a Haiku call)
    Intent.PROBE: (),                                # discovery
}


def intent_expands_to(intent: Intent | str) -> tuple[str, ...]:
    """The ActionId primitives this intent is expected to decompose into."""
    try:
        return _EXPANSIONS[Intent(intent)]
    except (KeyError, ValueError):
        return ()


#: The reverse bridge: which Intent best describes a bare ActionId primitive.
#: Needed because `/execute` is TIER 1 and polymorphic — it takes an `action_id`, not an
#: intent — yet its rows must still join to the intent vocabulary or the corpus splits in
#: two. Total over the driver's seven actions (driver.py:26); `clear` folds into SET_TEXT
#: because clearing IS setting empty, and the `actions` column keeps the primitive.
_ACTION_TO_INTENT: dict[str, Intent] = {
    "click": Intent.CLICK,
    "type": Intent.SET_TEXT,
    "clear": Intent.SET_TEXT,
    "select": Intent.SELECT_OPTION,
    "scroll": Intent.SCROLL,
    "submit": Intent.SUBMIT,
    "upload": Intent.UPLOAD,
}


def intent_for_action(action_id: str) -> Intent:
    """The Intent a bare ActionId primitive is journaled under. Unknown -> PROBE.

    Unknown routing to PROBE is deliberate: an action_id we don't recognise is, by
    definition, something being tried for the first time — which is what PROBE means, and
    it lands in the corpus as discovery to be promoted rather than as a silent mystery.
    """
    return _ACTION_TO_INTENT.get((action_id or "").strip().lower(), Intent.PROBE)


# --- Secrets ------------------------------------------------------------------------
# PRINCIPLES §4: capture per state, NEVER secrets. The journal records the `value` an
# intent carried, which makes it a password-shaped hole the moment someone drives a login
# through set_text. So redaction is applied BY THE JOURNAL, unconditionally, rather than
# trusted to each caller — the same reasoning as the rest of this API: an endpoint encodes
# the rule once and the caller cannot get it wrong.
_SENSITIVE_FIELD = re.compile(
    r"pass(word|wd|phrase)|^pwd|secret|token|api[_-]?key|ssn|social.?security|"
    r"credit.?card|card.?number|cvv|cvc|security.?code|routing|account.?number|"
    r"\botp\b|one.?time|2fa|mfa|verification.?code|auth.?code|pin\b",
    re.I,
)

_REDACTED = "[redacted]"
_MAX_VALUE = 120


def redact(value: str | None, *, field: str | None = None, sensitive: bool | None = None) -> str | None:
    """The value as it may be journaled.

    `sensitive=True` forces redaction; `sensitive=False` forces it off (an explicit caller
    override for a field whose NAME looks secret but whose value isn't — e.g. a "Security
    clearance" dropdown). Default: infer from the field name.

    A redacted value keeps its LENGTH (`[redacted:8]`) and nothing else — enough to debug
    "did the field receive anything at all" without storing the secret.
    """
    if value is None:
        return None
    hide = sensitive if sensitive is not None else bool(field and _SENSITIVE_FIELD.search(field))
    if hide:
        return f"[redacted:{len(value)}]" if value else _REDACTED
    return value if len(value) <= _MAX_VALUE else value[:_MAX_VALUE] + "…"
