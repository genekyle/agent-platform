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


#: What a learned witness's testimony is worth in the fusion, before anyone has measured how
#: often it agrees with the deterministic ones ON THIS QUESTION. Half a vote: enough to break a
#: tie and to show up as dissent, never enough to overturn two witnesses that agree. The
#: docstring's promise is that learned observers join "with their own weight" and earn
#: calibration later — this is the honest starting value, not a measurement.
LEARNED_WEIGHT = 0.5


def perception_witnesses(belief: Optional[dict[str, Any]]) -> list["Witness"]:
    """The perception stack's belief, translated into testimony this fusion can hear.

    Two deliberate choices, both from measurements rather than taste:

    **They claim a PLATFORM, never a state.** Apple Vision runs ~93% on platform and ~55% on
    state; the fusion's vocabulary is platforms and kinds anyway. Asking each witness the
    question it is good at is the whole reason to have more than one.

    **A witness at the novelty ceiling ABSTAINS.** It still testifies — its detail is rendered,
    so "I have never seen this page" stays visible — but it casts no vote. Measured live
    2026-08-04: on a LinkedIn results page the visual witness said `fb_marketplace_seller_dashboard`
    and the DOM witness said `indeed_did_you_apply`, both at novelty 1.00. Those are honest
    "unknown page" signals, and letting them vote would have dragged a correct `linkedin` verdict
    down to `medium` on the strength of two witnesses that were announcing their own ignorance.
    Abstention is what a novelty score is FOR.
    """
    from interaction.belief import NOVELTY_CEILING
    from perception import facets as facets_mod

    out: list[Witness] = []
    for view in ((belief or {}).get("witnesses") or []):
        name = str(view.get("name") or "perception")
        label = str(view.get("label") or "")
        novelty = view.get("novelty")
        if not label:
            continue
        blind = novelty is None or float(novelty) >= NOVELTY_CEILING
        # FROM THE LABEL ALONE — never the url. `platform_for` prefers the live host when given
        # one, which would make this witness echo the `url` witness verbatim: two votes from one
        # source, manufacturing agreement out of a single fact. A second witness is only worth
        # having while it testifies from what IT saw.
        platform = "" if blind else facets_mod.platform_for(label)
        detail = (f"nearest known page is {label}"
                  + (f" (similarity {view.get('similarity')})" if view.get("similarity") is not None
                     else "")
                  + (f", novelty {novelty}" if novelty is not None else ""))
        if blind:
            detail += " — never seen anything like this, so it abstains"
        out.append(Witness(source=name, claim=platform or "", detail=detail,
                           weight=LEARNED_WEIGHT))
    return out


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
    any named ATS beats it regardless of count, which is precisely the branded-wrapper case.

    **Agreement is judged by FAMILY, the answer is reported at its finest grain.** Witnesses speak
    at different granularities about the same owner — the url witness reads a registry id
    (`indeed_quick_apply`), a learned witness reads its own label through `platform_for` (`indeed`)
    — and comparing those as raw strings scored real agreement as dissent, costing two confidence
    grades (`high` -> `low` on three concurring witnesses). So votes are tallied per
    `facets.family_of`, while the WINNER stays the most specific claim inside the winning family:
    `indeed_quick_apply` names the recipe that can drive the page, and `indeed` does not.

    What this deliberately does NOT do is manufacture agreement. Distinct families still dissent —
    including the two that matter most: `company_site` (the shrug) against any named vendor, and
    the live 2026-08-04 case where the visual witness answered `appvault` on an Indeed results
    page. Collapsing granularity is not the same as collapsing disagreement.
    """
    from perception import facets as facets_mod

    votes: dict[str, float] = {}                     # by family — who is being named
    grains: dict[str, dict[str, float]] = {}         # family -> {claim: weight}, how finely
    for w in witnesses:
        if not w.claim:
            continue
        fam = facets_mod.family_of(w.claim) or w.claim
        votes[fam] = votes.get(fam, 0.0) + w.weight
        grain = grains.setdefault(fam, {})
        grain[w.claim] = grain.get(w.claim, 0.0) + w.weight
    if not votes:
        return "", "low"
    named = {k: v for k, v in votes.items() if k not in ("company_site", "unknown", "")}
    pool = named if (prefer_specific and named) else votes
    family = max(pool, key=lambda k: pool[k])
    # A SHRUG CANNOT BE CONFIDENT. `company_site` means "no witness recognised an owner" — it wins
    # only when nothing better testified, and reporting it above `low` would dress ignorance as a
    # finding. Named platforms earn confidence through agreement; the shrug never does.
    if family in ("company_site", "unknown", ""):
        return family, "low"
    # The finest grain anyone claimed for this family, heaviest witness first (ties broken by name
    # so the verdict is reproducible). Falling back to the family name is the honest answer when
    # only coarse witnesses testified.
    leaves = {c: wt for c, wt in grains[family].items() if c != family}
    winner = max(sorted(leaves), key=lambda c: leaves[c]) if leaves else family
    agree = sum(1 for w in witnesses if w.claim and facets_mod.family_of(w.claim) == family)
    dissent = sum(1 for w in witnesses if w.claim and facets_mod.family_of(w.claim) != family)
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

    # LOST — the only branch reached with no recognised kind, and until 2026-08-05 the ONLY branch
    # whose entire plan was "hand it to a human". That was backwards in two ways. It offered a
    # driveable move on the page we understand best (a posting) and none at all on the page we
    # understand least, and it threw away what the fusion HAD established on the way down.
    #
    # A second look is the one recovery this module can honestly perform by itself: read-only,
    # cheap, and aimed at the two situations that actually resolve on one — a page with nothing to
    # read yet, and a landing still in flight on a host we do recognise. It is offered FIRST and
    # the escalation stays on the list underneath it, because the point is never to keep the
    # operator out of the loop; it is to stop calling them for a page that had not finished
    # loading.
    #
    # NAMED LIMIT: this is a pure function with no memory, so it cannot tell a first look from a
    # fifth and will keep offering the re-read. Nothing here loops on its own (`orient_action` is
    # one action per call, initiator-checked), and a verdict that repeats is already visible as an
    # unchanged fingerprint in the orientation corpus — which is where a "stop asking" rule should
    # be built from measurement, not guessed at here.
    steps: list[dict[str, Any]] = []
    known_platform = platform and platform not in ("company_site", "unknown", "")
    if kind == al.UNREADABLE:
        steps.append(_step(REORIENT, "Read the page again",
                           "there was nothing to read yet — that is usually a page caught "
                           "mid-load or mid-redirect rather than a page we have never met"))
    elif known_platform:
        steps.append(_step(REORIENT, "Read the page again",
                           f"we know this is {platform} and only the page is unrecognised — "
                           f"a second look settles a landing still in flight"))
    # THE TEACHER IS ALWAYS ON THE LIST — never removed, only ever moved down one. And we arrive
    # carrying what we did work out: a human handed "unrecognised page" starts from nothing, while
    # a human handed "unrecognised page, but it is Workday" starts from the useful half.
    steps.append(_step(ESCALATE, "Screenshot and hand to me",
                       (f"nothing recognises this page, though the host reads as {platform} — "
                        f"a human look beats a guess, and that much is worth saying out loud")
                       if known_platform else
                       "nothing recognises this page — a human look beats a guess"))
    return steps


def orient(url: str, page_text: str = "", frames: Optional[list[dict]] = None,
           apply_hrefs: Optional[list[str]] = None, *,
           rung: Optional[str] = None, company: str = "",
           ats_lookup: Optional[Callable[[str], Optional[str]]] = None,
           known_recipe: bool = False,
           recorded_kind: str = "",
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
    def _an(kind_name: str) -> str:
        """"an application form", "a job posting" — these strings are read by the operator."""
        words = kind_name.replace("_", " ")
        return f"{'an' if words[:1] in 'aeiou' else 'a'} {words}"

    mismatch = None
    needs = RUNG_NEEDS.get(rung or "", ())
    readable = kind not in (al.UNKNOWN, al.UNREADABLE)
    if needs and readable and kind not in needs:
        mismatch = {
            "rung": rung, "expected": list(needs), "observed": kind,
            "detail": (f"the `{rung}` rung needs a {' or '.join(k.replace('_', ' ') for k in needs)}, "
                       f"but the page is a {kind.replace('_', ' ')} — the recipe and the world have "
                       f"drifted apart; follow the plan, not the rung"),
        }
    # THE RECORD IS A CLAIM TOO, and until now it was the one claim nothing could contradict.
    # `RUNG_NEEDS` covers the generic rungs and NONE of the scripted spine, so a platform step
    # ("workday_my_information") declared no needs and was therefore never wrong: the panel showed
    # it beside an observed account gate and reported no mismatch at all (live, 2026-08-16, after
    # a refresh silently signed the session out).
    #
    # `recorded_kind` is the KIND the record's own state implies, translated by the caller — this
    # module stays pure and vocabulary-agnostic. Empty means NO CLAIM, never disagreement: a state
    # we cannot place is not evidence the record is wrong.
    #
    # Low confidence abstains. An unsure reading may not evict a record that a real action wrote;
    # the arbiter already says an abstention out loud rather than dropping it in silence.
    elif (recorded_kind and readable and recorded_kind != kind
            and confidence != "low"):
        mismatch = {
            "rung": rung, "expected": [recorded_kind], "observed": kind, "drift": True,
            "detail": (f"the record has us on {_an(recorded_kind)} and the window is showing "
                       f"{_an(kind)} — the world moved and the record did not, so the screen is "
                       f"the thing to believe"),
        }

    return Orientation(platform=platform, kind=kind, state=state, confidence=confidence,
                       headline=headline_for(kind, platform),
                       witnesses=witnesses, mismatch=mismatch,
                       plan=_short_plan(kind, platform, rung, known_recipe))
