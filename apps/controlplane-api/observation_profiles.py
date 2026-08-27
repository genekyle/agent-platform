"""What to look at HERE, in what order — and what we could not see (SESSION 18).

THE OPERATOR'S OWN DESIGN NOTE, 2026-08-19, filed the day the census described a page whose main
feature it could not see: *"pages need an OBSERVATION profile — what to look at here — the way
they already need an interaction profile."* The census enumerates FORM FIELDS, so on Paylocity it
reported "7 required fields unanswered" and listed address inputs while the operator, who could
see the window, said: *"there is a giant module asking to upload a resume."* One `ax_scan` found
in a single call what the census could not say — plus a consent dialog sitting on top of it.

THE FAILURE IS NEVER A WRONG ANSWER, IT IS A CONFIDENT SILENCE. Measured, in one week:
  * a six-step wizard read as one screen from Submit (the page said "Step 1 of 6"; 08-19)
  * "all required fields answered" on a step whose only content was one required textarea (08-19)
  * 35 invented requirements against the ~6 the site named when asked (08-19)
  * "3 answer(s) — No, No, No" as question labels while the AX tree held every full radiogroup
    name (08-23)
  * six sightings of widget families whose accessible names are empty, each rescued ad hoc

So this module does two things and neither of them is a new reader:

1. **ORDER the readers we already have**, per page-KIND, so a page is not described by whichever
   one speaks first. Dialogs and consent banners are read before the form beneath them, because a
   modal that eats the click makes every fact under it irrelevant. The generic tier carries an
   unknown ATS (the TAM precedent); platform entries may only SHARPEN an order, never replace it.
2. **Say what could not be seen.** Truncated option lists, frames we did not enter, controls with
   no accessible name, values a reader is structurally blind to. Silence reads as absence, and
   absence reads as "nothing there" — which is how a résumé-upload module, a consent dialog and a
   wizard counter were all missing from one page's description at once.

It does NOT suppress a witness or decide anything. Every reader still speaks; this says which to
believe first and which questions nobody asked.
"""
from __future__ import annotations

from typing import Any, Optional

import apply_landing as al

#: The readers, in the order a human would use them. Names are descriptive, not endpoints — the
#: profile is advice to a caller that already has these readings in hand.
DIALOGS = "dialogs"                  # role=alertdialog/dialog, consent banners, overlays
WIZARD = "wizard_position"           # the page's own "Step 1 of 6" / percent meter
VALIDATION = "validation"            # the site's own complaints — the authority on required-ness
UPLOADS = "upload_modules"           # file inputs and the buttons painted over them
REQUIRED = "required_fields"         # the form census
TARGET = "target_control"            # the control this rung came to drive

#: A page-kind's reading order. THE DIALOG ALWAYS COMES FIRST, on every kind: a dialog is the only
#: reader whose finding invalidates all the others (a modal over the Apply button makes the form
#: beneath it unreachable, and every "the click reported ok and nothing moved" verdict this repo
#: has recorded on an entry screen was one).
_GENERIC_ORDER: dict[str, tuple[str, ...]] = {
    al.JOB_POSTING:       (DIALOGS, TARGET, WIZARD),
    al.JOB_LIST:          (DIALOGS, TARGET),
    al.ACCOUNT_GATE:      (DIALOGS, VALIDATION, REQUIRED, TARGET),
    al.APPLICATION_FORM:  (DIALOGS, WIZARD, VALIDATION, UPLOADS, REQUIRED, TARGET),
    al.REVIEW:            (DIALOGS, VALIDATION, REQUIRED, TARGET),
    al.CONFIRMATION:      (DIALOGS,),
    al.GONE:              (DIALOGS,),
}
_FALLBACK_ORDER: tuple[str, ...] = (DIALOGS, WIZARD, VALIDATION, UPLOADS, REQUIRED, TARGET)

#: PLATFORM SHARPENING — additive only, exactly like `submission_verifier.ATS_HINTS`. An entry may
#: promote a reader for a platform we have MEASURED; it can never be required, and an unknown ATS
#: keeps the generic order (the TAM precedent: posting→submitted in one pass, no entry at all).
#: Each line cites the drive that earned it.
_PLATFORM_FIRST: dict[str, dict[str, tuple[str, ...]]] = {
    # 2026-08-19: the form opens BEHIND an "Apply with resume" modal offering to autofill from an
    # upload, and the résumé parse then multiplies the form (7 -> 35 required on one upload).
    "paylocity": {al.APPLICATION_FORM: (DIALOGS, UPLOADS, WIZARD, VALIDATION, REQUIRED, TARGET)},
    # 2026-08-24: two identical "Apply Now" controls, one off-screen; and the upload is a hidden
    # input behind a painted button, which is why UPLOADS outranks the census here too.
    "cornerstone": {al.JOB_POSTING: (DIALOGS, TARGET, WIZARD),
                    al.APPLICATION_FORM: (DIALOGS, UPLOADS, WIZARD, VALIDATION, REQUIRED, TARGET)},
    # 2026-08-20: a geo/law-triggered consent modal ("We Care About Your Privacy", NH SB 255) sits
    # over the Apply button and appears or vanishes by operator location — never assumed present
    # OR absent, always looked for first.
    "schoolspring": {al.JOB_POSTING: (DIALOGS, TARGET),
                     al.ACCOUNT_GATE: (DIALOGS, VALIDATION, REQUIRED, TARGET)},
}


def reading_order(kind: str, platform: str = "") -> tuple[str, ...]:
    """What to look at here, first to last. An unknown kind gets the full generic sweep — the
    honest answer for a screen nobody has named is 'look at everything', never 'look at nothing'."""
    per_platform = _PLATFORM_FIRST.get((platform or "").strip().lower(), {})
    if kind in per_platform:
        return per_platform[kind]
    return _GENERIC_ORDER.get(kind, _FALLBACK_ORDER)


# --- WHAT WE COULD NOT SEE ----------------------------------------------------------------------
# Each entry is `{reader, what, detail}`. The rule they all serve: *"we did not look" and "there
# is nothing there" must never render alike* — the tri-state `page_meta.has_next` already keeps
# for pagination, and `Search.filters` for provenance, now applied to perception itself.

def read_gaps(*, census: Optional[dict[str, Any]] = None,
              candidates: Optional[list[dict[str, Any]]] = None,
              frames: Optional[list[dict[str, Any]]] = None,
              content_source: str = "") -> list[dict[str, str]]:
    """Everything the readings in hand are structurally blind to, named."""
    gaps: list[dict[str, str]] = []

    census = census or {}
    # ALL THREE ROW LISTS, and the row's name key is `field` — the census emits
    # `unanswered`/`answered`/`optional` and never a `fields` key, and a ~250-entry Country select
    # is just as likely to sit in `answered` as in `unanswered`. (The first draft of this read
    # `census["fields"]` and `row["label"]`, so it would have found nothing and, where it did,
    # called every field "a field".)
    for bucket in ("unanswered", "answered", "optional"):
        for row in (census.get(bucket) or []):
            if not isinstance(row, dict):
                continue
            shown = row.get("options")
            total = row.get("option_count")
            truncated = row.get("options_truncated")
            if not isinstance(shown, list):
                continue
            if truncated or (isinstance(total, int) and total > len(shown)):
                gaps.append({
                    "reader": REQUIRED, "what": "option list truncated",
                    "detail": (f"{row.get('field') or 'a field'} ({bucket}): {len(shown)} of "
                               f"{total if isinstance(total, int) else 'unknown'} options read — "
                               f"the answer may be past the end (2026-08-21: 'Other Foreign "
                               f"Educational Institution' was twelve entries past the sample)")})
    # The census's OWN cap flags. Real names, carried end to end as of 2026-08-27 — they were
    # computed on 08-21 and dropped twice (mcp handler, then the census projection), so nothing
    # could tell a complete reading from a capped one.
    for flag, says in (
            ("optional_truncated", "the optional-field list hit its 40-row cap"),
            ("page_errors_truncated", "the page-level error list hit its 6-message cap"),
            ("field_errors_truncated", "the per-field error list hit its 8-message cap")):
        if census.get(flag):
            gaps.append({"reader": REQUIRED, "what": flag, "detail": says})

    # A control with no accessible name cannot be addressed by name — six sightings in one week
    # (Workday's questionnaire textareas, Indeed's self-ID radios, Cornerstone's contact boxes).
    # Position mapping is the named route out; saying so is what turns a silent failure into one.
    nameless = sum(1 for c in (candidates or [])
                   if isinstance(c, dict) and not (c.get("name") or "").strip())
    if nameless:
        gaps.append({
            "reader": TARGET, "what": "controls with no accessible name",
            "detail": (f"{nameless} candidate(s) carry an empty name — they cannot be addressed "
                       f"by name, and proximity resolution has landed on a NEIGHBOUR's question "
                       f"stably (2026-08-25). Address by node id or by measured position.")})

    # A frame we did not read is a page we did not read. `pick_content` classifies exactly ONE
    # document and returns its source as `id`/`name`/"frame"/"top" — so a frame is identified the
    # way THAT function identifies it, and its text is `text`, not a `text_len` nobody sets. (The
    # first draft matched on `url`/`text_len` and could never have fired.)
    for fr in (frames or []):
        if not isinstance(fr, dict):
            continue
        src = fr.get("id") or fr.get("name") or "frame"
        body = fr.get("text") or ""
        if content_source and str(src) != str(content_source) and len(body.strip()) > 200:
            gaps.append({"reader": REQUIRED, "what": "an unread frame with real text",
                         "detail": (f"frame {str(src)[:60]!r} carries {len(body)} chars nobody "
                                    f"classified — the content frame was {content_source!r}")})

    # THE DIALOG, WHICH LEADS EVERY READING ORDER. A dialog reading arrives with the census as of
    # 2026-08-27; before that no reading a drive takes before acting looked for one at all. The
    # tri-state is the point: a modal FOUND is the headline, a page checked and clear is quiet,
    # and a page NOBODY CHECKED says so — those last two must never render alike.
    if census:
        found = [d for d in (census.get("dialogs") or [])
                 if isinstance(d, dict) and d.get("visible")]
        if found:
            top = max(found, key=lambda d: d.get("area") or 0)
            gaps.append({
                "reader": DIALOGS, "what": "a dialog is on top of this page",
                "detail": (f"{top.get('role') or 'dialog'}"
                           + (" (modal)" if top.get("modal") else "")
                           + f": {(top.get('text') or '').strip()[:120]!r} — nothing beneath it "
                             f"can be clicked until it is dealt with")})
    elif candidates is not None or frames is not None:
        gaps.append({"reader": DIALOGS, "what": "nobody checked for a dialog",
                     "detail": ("this reading carried no dialog scan, so a modal over the target "
                                "would be invisible to it — not a clean page, an unasked question")})

    # THE STRUCTURAL ONES — true of every page, and the reason a clean census is not a clean page.
    if candidates is not None:
        gaps.append({"reader": TARGET, "what": "checked state",
                     "detail": "ax_scan carries no checked/selected state: for a radio or a "
                               "checkbox, pixels are the only witness this system has (2026-08-23)"})
    if census:
        gaps.append({"reader": REQUIRED, "what": "correctness",
                     "detail": "a census confirms ANSWEREDNESS, never correctness — it cannot "
                               "know a radio holds the opposite of the operator's answer "
                               "(2026-08-21, a sponsorship question left on Yes)"})
    return gaps


def describe(*, kind: str, platform: str = "", page_text: str = "",
             census: Optional[dict[str, Any]] = None,
             candidates: Optional[list[dict[str, Any]]] = None,
             frames: Optional[list[dict[str, Any]]] = None,
             content_source: str = "") -> dict[str, Any]:
    """The observation report for one page: what to look at, what the page says about its own
    position, and what could not be seen. Pure — every reading is passed in."""
    order = reading_order(kind, platform)
    return {
        "kind": kind or al.UNKNOWN,
        "platform": platform or "",
        "looked_at": list(order),
        # The page's own statement of where it is, which `_STEPPER` has parsed since the
        # confirmation guard was written and thrown away every time (2026-08-27).
        "wizard": al.wizard_position(page_text or ""),
        "could_not_see": read_gaps(census=census, candidates=candidates, frames=frames,
                                   content_source=content_source),
        "profile_source": ("platform" if kind in _PLATFORM_FIRST.get(
            (platform or "").lower(), {}) else "generic"),
    }
