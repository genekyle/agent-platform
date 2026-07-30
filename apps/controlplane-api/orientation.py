"""Orientation — the always-on "where are we, and what 1–2 steps get us out" observer.

Operator-directed 2026-07-30, after two rungs in one day acted on where they ASSUMED the browser
was: *"i need the observer to fire and collect data … especially since we know third party
applications will always land us somewhere uncertain … use any and all context around it and any
tools to make the answer more accurate."*

The shape is a WITNESS FUSION, not a classifier call. Landing somewhere after an apply click is
exactly the uncertain case, and no single signal survives it alone — the URL lies on branded
wrappers, the content lies on iframe'd ATS, the recipe lies when the vendor redesigns. So every
cheap source testifies independently and the verdict is what they agree on:

    url        which ATS the host/params/path claim            (ats_registry)
    content    what KIND of page the text reads as             (apply_landing, frames included)
    signpost   where the page's own apply controls point       (the careers-front tell)
    memory     which ATS this employer used before             (company_ats, learned per drive)
    context    what the step was DOING when we landed here     (a prior, never a veto)
    <learned>  perception witnesses — screenshot/DOM similarity — join through `extra_witnesses`
               once trained; the fusion does not change shape when they arrive.

Confidence is AGREEMENT: every witness that concurs with the winning platform raises it, every
dissent lowers it and stays visible in the evidence rows. The verdict never hides a losing witness
— the dissent is often the finding (a `company_site` URL against an `appvault` signpost IS the
branded-wrapper diagnosis).

The MISMATCH flag is the safety catch this exists for: the current rung declares what kind of page
it needs (`account` needs a gate, `submit` needs a form), and when the observed kind disagrees the
verdict says so explicitly — so the panel renders the disagreement instead of the recipe's
assumption, and the planner gets a short way out (`plan`: 1–2 steps, because backtracking or
getting your bearings is never deep).

Pure: no I/O, no browser, no DB. Callers fetch the page; this reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import apply_landing as al

# --- what each rung assumes the screen is -------------------------------------------------------
#: The page kinds a rung can act on. This is the declaration that makes MISMATCH computable: a
#: rung that states its needs can be contradicted by an observation; one that does not is the
#: "brainless" stepping the operator called out. `enter_apply` and `open_pane` accept a posting;
#: `account` needs a gate; the fill and submit need the form.
RUNG_NEEDS: dict[str, tuple[str, ...]] = {
    "open_pane": (al.JOB_POSTING, al.JOB_LIST),
    "verify_identity": (al.JOB_POSTING,),
    "enter_apply": (al.JOB_POSTING,),
    "classify": (),                      # classify's whole job is not knowing yet
    "account": (al.ACCOUNT_GATE,),
    "submit": (al.APPLICATION_FORM,),
}

#: What an apply click plausibly lands on — the CONTEXT prior. Deliberately broad: a prior narrows
#: attention, it must never overrule a witness that actually looked.
EXPECTED_AFTER_APPLY: tuple[str, ...] = (
    al.JOB_POSTING, al.ACCOUNT_GATE, al.APPLICATION_FORM, al.JOB_LIST,
)


@dataclass
class Witness:
    source: str            # url | content | signpost | memory | context | <a learned witness>
    claim: str             # the platform or kind it asserts, "" when it abstains
    detail: str            # why, human-readable — rendered verbatim in the panel
    weight: float = 1.0    # learned witnesses may arrive with a calibrated weight

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "claim": self.claim, "detail": self.detail,
                "weight": self.weight}


#: WHAT TO CALL IT ON SCREEN. The operator asked to see "job landing page", not
#: `appvault_job_posting`: the card answers "where are we" to a person, and a state id is an
#: identifier for the corpus, not an answer to that question. Both are kept — `state` stays the
#: machine's, `headline` is the human's.
KIND_LABELS: dict[str, str] = {
    al.JOB_POSTING: "Job landing page",
    al.JOB_LIST: "Job listing page",
    al.ACCOUNT_GATE: "Account / sign-in wall",
    al.APPLICATION_FORM: "Application form",
    al.CONFIRMATION: "Submitted — confirmation page",
    al.GONE: "Posting is gone",
    al.UNREADABLE: "Unreadable page",
    al.UNKNOWN: "Unrecognised page",
}


def headline_for(kind: str, platform: str) -> str:
    """"Job landing page · AppVault" — the prediction, in the words a person would use."""
    label = KIND_LABELS.get(kind, "Unrecognised page")
    # NO OWNER ON A PAGE WE COULD NOT READ. `company_site` is the URL witness shrugging, and
    # appending "· employer's own site" to "Unreadable page" dresses two non-answers as one
    # finding. When the kind is unknown the headline says only that.
    if kind in (al.UNKNOWN, al.UNREADABLE):
        return label
    if platform and platform not in ("company_site", "unknown", ""):
        return f"{label} · {platform}"
    if platform == "company_site":
        return f"{label} · employer's own site"
    return label


@dataclass
class Orientation:
    platform: str                        # best fused answer for WHOSE page ("" = nobody knows)
    kind: str                            # best fused answer for WHAT KIND of page
    state: str                           # platform + kind composed (apply_landing.landing_state)
    confidence: str                      # high | medium | low
    headline: str = ""                   # the prediction in plain English, for the card
    witnesses: list[Witness] = field(default_factory=list)
    mismatch: Optional[dict[str, Any]] = None   # {rung, expected, observed, detail} when they disagree
    plan: list[dict[str, str]] = field(default_factory=list)   # 1-2 steps, each {action, why}

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "kind": self.kind, "state": self.state,
                "confidence": self.confidence, "headline": self.headline,
                "witnesses": [w.as_dict() for w in self.witnesses],
                "mismatch": self.mismatch, "plan": self.plan}


def _platform_witnesses(url: str, apply_hrefs: list[str], company: str,
                        ats_lookup: Optional[Callable[[str], Optional[str]]]) -> list[Witness]:
    """The who-owns-this-page witnesses, cheapest first. Each testifies alone."""
    from ats_registry import classify_ats

    out: list[Witness] = []
    by_url = classify_ats(url) if url else ""
    if by_url:
        out.append(Witness("url", by_url, f"the host/params read as {by_url}"))
    if apply_hrefs:
        by_sign = classify_ats(url or "", {"apply_hrefs": apply_hrefs})
        if by_sign and by_sign != by_url:
            out.append(Witness("signpost", by_sign,
                               f"the page's own apply control points at {by_sign} "
                               f"({apply_hrefs[0][:70]})"))
        elif by_sign:
            out.append(Witness("signpost", by_sign, "the apply control agrees with the host"))
    if company and ats_lookup is not None:
        remembered = ats_lookup(company) or ""
        if remembered:
            out.append(Witness("memory", remembered,
                               f"{company} used {remembered} on a previous drive"))
    return out


def _fuse(witnesses: list[Witness], *, prefer_specific: bool = True) -> tuple[str, str]:
    """(winner, confidence) from weighted agreement. `company_site` is a SHRUG, not a vendor —
    any named ATS beats it regardless of count, which is precisely the branded-wrapper case."""
    votes: dict[str, float] = {}
    for w in witnesses:
        if w.claim:
            votes[w.claim] = votes.get(w.claim, 0.0) + w.weight
    if not votes:
        return "", "low"
    named = {k: v for k, v in votes.items() if k not in ("company_site", "unknown", "")}
    pool = named if (prefer_specific and named) else votes
    winner = max(pool, key=lambda k: pool[k])
    # A SHRUG CANNOT BE CONFIDENT. `company_site` means "no witness recognised an owner" — it wins
    # only when nothing better testified, and reporting it above `low` would dress ignorance as a
    # finding. Named platforms earn confidence through agreement; the shrug never does.
    if winner in ("company_site", "unknown", ""):
        return winner, "low"
    agree = sum(1 for w in witnesses if w.claim == winner)
    dissent = sum(1 for w in witnesses if w.claim and w.claim != winner)
    if agree >= 2 and dissent == 0:
        return winner, "high"
    if agree >= 1 and dissent <= 1:
        return winner, "medium"
    return winner, "low"


#: The actions orientation can actually DRIVE, as opposed to advise. An action is offered as a
#: BUTTON only when we know how to perform it here — "if we've ever seen it before then we would
#: have actions" (operator, 2026-07-30). Everything else is still named, but as guidance, because a
#: button that does nothing is worse than a sentence that admits it.
PRESS_APPLY = "press_apply"
REORIENT = "reorient"
WORK_RUNG = "work_rung"
OPEN_JOB = "open_job"
ESCALATE = "escalate"

#: Which of those the executor implements. Kept explicit and SHORT for the same reason
#: `apply_steps.DRIVEN_PLATFORMS` is: recognising a situation is not the same as being able to act
#: in it, and offering a control we cannot honour is how a panel starts lying again.
DRIVEABLE = frozenset({PRESS_APPLY, REORIENT})


def _step(action_id: str, label: str, why: str) -> dict[str, Any]:
    return {"id": action_id, "label": label, "why": why,
            "driveable": action_id in DRIVEABLE}


def _short_plan(kind: str, platform: str, rung: Optional[str], known_recipe: bool) -> list[dict[str, Any]]:
    """The 1–2 step way out, each step a NAMED action the cockpit can render as a button.

    Small on purpose — the operator's words: "it's not that deep to back track or just get your
    bearings." Anything longer than two steps is the planner's job proper, not orientation's.
    """
    if kind == al.JOB_POSTING:
        return [_step(PRESS_APPLY, "Click Apply on this page",
                      "a posting is one click from the real application — nothing to sign into yet"),
                _step(REORIENT, "Re-check where we are",
                      "third-party applies land somewhere uncertain by definition")]
    if kind == al.ACCOUNT_GATE:
        return [_step(WORK_RUNG, "Work the account step",
                      f"{platform or 'this ATS'} wants an identity before it takes an application")]
    if kind == al.APPLICATION_FORM:
        return ([_step(WORK_RUNG, "Plan the fill",
                       "the form is the application — scan, fill, stop at Submit")]
                if known_recipe else
                [_step(WORK_RUNG, "Drive it attended",
                       f"no recipe has driven {platform or 'this platform'} end to end yet — "
                       f"capture every state on the way")])
    if kind == al.JOB_LIST:
        return [_step(OPEN_JOB, "Open the intended job",
                      "we are on a listing, not the job — clicking through is how a human gets there")]
    if kind == al.CONFIRMATION:
        return [_step(WORK_RUNG, "Record the submission",
                      "the page says it went through — write it down while it is on screen")]
    if kind == al.GONE:
        return [_step(WORK_RUNG, "Flag the job gone",
                      "the requisition outlived the posting; nothing here to apply to")]
    return [_step(ESCALATE, "Screenshot and hand to me",
                  "nothing recognises this page — a human look beats a guess")]


def orient(url: str, page_text: str = "", frames: Optional[list[dict]] = None,
           apply_hrefs: Optional[list[str]] = None, *,
           rung: Optional[str] = None, company: str = "",
           ats_lookup: Optional[Callable[[str], Optional[str]]] = None,
           known_recipe: bool = False,
           extra_witnesses: Optional[list[Witness]] = None) -> Orientation:
    """Fuse every witness into one verdict: where we are, how sure, and the way out.

    `extra_witnesses` is the seam the LEARNED observers join through — a perception witness that
    says "this screenshot sits nearest the workday_account_gate cluster" is appended like any other
    testimony, with its own weight, and confidence math does not change. That keeps the training
    story additive: the fusion works today on deterministic witnesses and gets sharper as trained
    ones earn their calibration, rather than waiting on them.
    """
    witnesses = _platform_witnesses(url or "", list(apply_hrefs or []), company, ats_lookup)

    # WHAT KIND of page — read from the content, frames included (the iCIMS lesson: the top
    # document is often the wrapper's marketing shell and the truth lives in the frame).
    text, source = al.pick_content(page_text or "", frames)
    landing = al.classify_kind(text, source=source)
    if landing.kind not in (al.UNKNOWN, al.UNREADABLE):
        witnesses.append(Witness("content", landing.kind,
                                 f"the {source or 'page'} text reads as a "
                                 f"{landing.kind.replace('_', ' ')} "
                                 f"({', '.join(list(landing.evidence)[:3])})"))
    else:
        witnesses.append(Witness("content", "", f"the page text answered {landing.kind}"))

    # The step's CONTEXT is a prior: it says which landings are plausible, never which one is true.
    if rung:
        expected = RUNG_NEEDS.get(rung, ())
        witnesses.append(Witness("context", "",
                                 f"the step is on `{rung}`"
                                 + (f", which needs a {' / '.join(k.replace('_',' ') for k in expected)}"
                                    if expected else ", which makes no assumption"), weight=0.0))

    for w in (extra_witnesses or []):
        witnesses.append(w)

    platform, confidence = _fuse([w for w in witnesses if w.source != "content"])
    kind = landing.kind
    # Content is the only KIND witness today; learned witnesses may add votes for kind later.
    kind_votes = [w for w in (extra_witnesses or []) if w.claim in al.KINDS]
    if kind in (al.UNKNOWN, al.UNREADABLE) and kind_votes:
        kind = max(kind_votes, key=lambda w: w.weight).claim

    state = al.landing_state(platform or "company_site", kind)

    # THE SAFETY CATCH. The rung said what it needs; the page said what it is. Disagreement is
    # surfaced, never resolved silently — resolving it is the plan's job, and the operator's call.
    mismatch = None
    needs = RUNG_NEEDS.get(rung or "", ())
    if needs and kind not in (al.UNKNOWN, al.UNREADABLE) and kind not in needs:
        mismatch = {
            "rung": rung, "expected": list(needs), "observed": kind,
            "detail": (f"the `{rung}` rung needs a {' or '.join(k.replace('_', ' ') for k in needs)}, "
                       f"but the page is a {kind.replace('_', ' ')} — the recipe and the world have "
                       f"drifted apart; follow the plan, not the rung"),
        }

    return Orientation(platform=platform, kind=kind, state=state, confidence=confidence,
                       headline=headline_for(kind, platform),
                       witnesses=witnesses, mismatch=mismatch,
                       plan=_short_plan(kind, platform, rung, known_recipe))
