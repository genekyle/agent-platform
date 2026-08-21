"""Every JS blob that resolves a TARGET must carry the shared tells.

`__findAll` (frame-aware matching) lives in `app/js_common.py` and is injected into each blob by
substituting a `__WIDGET_TELLS__` placeholder. A blob that calls `__findAll` without it is not a
syntax error at import time — it is a page-side `ReferenceError` at CALL time, which comes back as
`Runtime.evaluate` returning no value, which the endpoints render as a bare `outcome: "error"`
with an EMPTY detail. It reads exactly like a stale recipe blaming the page.

CHECK_GROUP_JS shipped that way (2026-08-11 → 2026-08-13), so the required-consent checkbox step
never ran on any tenant. It surfaced as a Workday signup that filled email/password/verify and
then refused to submit — which the operator reasonably read as "it couldn't find the Create
Account button". One missing line, invisible for two days, found only by driving it.

This is the enforcement point: the module is scanned as text, so a new blob cannot forget.
"""
import re

from app import protocols


def _blobs():
    return {name: value for name, value in vars(protocols).items()
            if name.endswith("_JS") and isinstance(value, str)}


def test_every_blob_that_calls_findall_has_its_tells_injected():
    offenders = [name for name, src in _blobs().items()
                 if "__findAll" in src and "const __findAll" not in src]
    assert not offenders, (
        f"{offenders} call __findAll without the shared tells — every call will throw "
        f"ReferenceError on the page and report a bare 'error'. Add `__WIDGET_TELLS__` to the "
        f"blob and substitute it at the bottom of protocols.py."
    )


def test_no_placeholder_is_left_unsubstituted():
    left = [name for name, src in _blobs().items() if "__WIDGET_TELLS__" in src]
    assert not left, f"{left} still contain an unsubstituted __WIDGET_TELLS__ placeholder"


def test_check_group_reports_a_missing_control_as_not_found():
    """`not_found` is what lets a caller tell "there is no such box" from "the box would not
    tick" — the distinction a consent step has to make, because tenants of one ATS differ on
    whether the box exists at all."""
    assert re.search(r"code:\s*'not_found'", protocols.CHECK_GROUP_JS)


# --------------------------------------------------------------- role/name target resolution
# `_resolve_ax_node` ranks exact -> leading -> anywhere. The middle tier is load-bearing: a field's
# accessible name is its label plus the widget's decoration ("State Select One Required"), so a
# prefix reaches the real control while a bare substring reaches any field whose VALUE contains the
# word. Live 2026-08-13: asking for "State" opened the COUNTRY prompt, because "state" sits inside
# "Country United States of America Required" and that node came first.
import asyncio
from unittest.mock import patch

import app.main_server as ms

_WORKDAY_MY_INFORMATION = [
    {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
    {"role": "button", "name": "State Select One Required", "backend_node_id": 2},
    {"role": "button", "name": "Phone Device Type Mobile Required", "backend_node_id": 3},
]


def _resolve(name, role=None, cands=None):
    rows = _WORKDAY_MY_INFORMATION if cands is None else cands

    async def _fake(**_kw):
        return rows

    with patch("app.observer.ax_proposer.propose_ax_candidates", _fake):
        return asyncio.run(ms._resolve_ax_node("http://x", None, None, role, name))


def test_a_label_that_leads_beats_one_that_merely_contains():
    assert _resolve("State") == 2, "'state' is inside 'United States' — the leading match wins"
    assert _resolve("Country") == 1
    assert _resolve("Phone Device Type") == 3


def test_an_exact_name_still_wins_outright():
    assert _resolve("State", cands=[
        {"role": "button", "name": "State Select One", "backend_node_id": 9},
        {"role": "button", "name": "State", "backend_node_id": 7}]) == 7


def test_a_substring_is_still_reachable_when_nothing_leads():
    assert _resolve("State", cands=[
        {"role": "button", "name": "Please choose your State now", "backend_node_id": 5}]) == 5


def test_a_name_that_leads_two_controls_is_refused_not_guessed():
    """The incident this rule is written from: on Workday's My Information "Country" LEADS both
    'Country State of Palestine Required' and 'Country Phone Code'. The first match won silently
    and changed the operator's Country out from under them, re-rendering the form localized.
    A tier with more than one candidate means the name does not identify a control."""
    assert _resolve("Country", cands=[
        {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
        {"role": "textbox", "name": "Country Phone Code", "backend_node_id": 4}]) is None


def test_an_unambiguous_name_still_resolves_next_to_its_neighbours():
    """The refusal must not swallow the ordinary case — 'Country Phone Code' names exactly one."""
    cands = [
        {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
        {"role": "textbox", "name": "Country Phone Code", "backend_node_id": 4}]
    assert _resolve("Country Phone Code", cands=cands) == 4
    assert _resolve("State", cands=cands + [
        {"role": "button", "name": "State Select One Required", "backend_node_id": 2}]) == 2


def test_a_capped_option_list_says_that_it_is_capped():
    """A CAPPED LIST THAT DOES NOT SAY SO IS READ AS THE WHOLE LIST.

    Live 2026-08-14, Boston Children's: a ~250-entry Country select censused 24 options whose only
    "United" entry was United Arab Emirates. The planner's stored answer ("United States") matched
    nothing in the list it was given, so Country silently stayed unanswered on a form one screen
    from Submit. The cap is right — nobody needs 250 strings in a census payload — but "not an
    option" and "not shown" have to be distinguishable, so every site that ships `options` ships
    the page's own total beside it.
    """
    import app.protocols as protocols
    src = protocols.__loader__.get_source("app.protocols")
    assert "options_truncated" in src and "option_count" in src
    # Every site that hands over a capped list hands over the tell with it.
    assert src.count("options: selectOptions(el)") == src.count("...optionMeta(el)")
    assert "options: selectOptions(el)}" not in src and "options: selectOptions(el)}" not in src


# --- the census's required-detection, pinned at the pattern level (2026-08-20/21) ---------------
#
# The census JS cannot run under pytest, but its two load-bearing regexes are plain enough to
# hold in Python's `re` with identical semantics. Each test pins BOTH the behaviour (against the
# literal pattern) and the presence of that pattern in the shipped source — edit one without the
# other and a test names the drift.

import re as _re


def test_the_label_required_anchor_tolerates_trailing_punctuation():
    """Paylocity writes "…(required)" and the bare `$` anchor filed the ONLY required control on
    step 2 as optional — scan_required then truthfully said "all required fields answered" over a
    page that was refusing (live 2026-08-19)."""
    pat = _re.compile(r"\brequired\s*[)\].:*]?\s*$", _re.I)
    assert pat.search("what makes you uniquely qualified for this role (required)")
    assert pat.search("Degree Select One Required")            # the Workday form still matches
    assert pat.search("Cover Letter required:")
    assert not pat.search("Required certifications")           # ABOUT requirements, not required
    assert not pat.search("required documents must be uploaded first")
    assert r"\brequired\s*[)\].:*]?\s*$" in protocols.SCAN_REQUIRED_JS


def test_the_bare_is_required_form_is_harvested_not_skipped():
    """"Email Address is required" carries no "field" prefix, so the Workday-shaped namedField
    regex missed it and both error passes `continue`d past it — the validator's exact testimony
    (four real blockers on a page where the census had invented thirty) was discarded. Line-
    anchored so prose merely containing the words is not swept in."""
    pat = _re.compile(r"^\s*(.{2,90}?)\s+is required(?: and must have a value)?\.?\s*$",
                      _re.I | _re.M)
    blob = "Errors found\nEmail Address is required\nMobile Number is required."
    assert [m.group(1).strip() for m in pat.finditer(blob)] == [
        "Errors found\nEmail Address", "Mobile Number"] or [
        m.group(1).strip() for m in pat.finditer(blob)] == ["Email Address", "Mobile Number"]
    # Prose containing the words, mid-sentence: no line-anchored match.
    assert not pat.search("explain why this information is required for the role today")
    assert "is required(?: and must have a value)?\\.?\\s*$" in protocols.SCAN_REQUIRED_JS
    # And the Workday form stays owned by namedField — the bare pass must skip it.
    assert "/^the field\\s/i" in protocols.SCAN_REQUIRED_JS


def test_capped_census_lists_say_they_are_capped():
    """The file's own rule (`options_truncated`), applied to the three lists that lacked it: a
    field past the cap has no address at all, and a capped list that does not say so is read as
    the whole list."""
    src = protocols.SCAN_REQUIRED_JS
    for flag in ("optional_truncated", "page_errors_truncated", "field_errors_truncated"):
        assert flag in src, f"{flag} missing from the census return"


_PEOPLEADMIN_POSTING = [
    {"role": "heading", "backend_node_id": 10,
     "name": " Bookmark this Posting  Print Preview |  Apply for this Job"},
    {"role": "link", "name": "Print Preview", "backend_node_id": 11},
    {"role": "link", "name": "Apply for this Job", "backend_node_id": 12},
]


def test_the_control_beats_its_container(monkeypatch):
    """PeopleAdmin's posting header is ONE candidate wearing THREE controls' names — a click at
    its centre landed on /print_preview (live 2026-08-19). Inside a tier, interactive candidates
    outrank containers; the leaf link wins even though the heading's name also matches."""
    assert _resolve("Apply for this Job", cands=_PEOPLEADMIN_POSTING) == 12
    # And with the role stated, exactly as the recipe drives it:
    assert _resolve("Apply for this Job", role="link", cands=_PEOPLEADMIN_POSTING) == 12


def test_a_tier_of_only_containers_answers_nothing(monkeypatch):
    """When the only match is a heading, handing it back invites the centre-click coin flip —
    unless the caller EXPLICITLY asked for that role, which is them owning an unusual target."""
    only_heading = [{"role": "heading", "name": "Apply for this Job", "backend_node_id": 20}]
    assert _resolve("Apply for this Job", cands=only_heading) is None
    assert _resolve("Apply for this Job", role="heading", cands=only_heading) == 20
