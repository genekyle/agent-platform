"""Route identification — the "deep end", where an Indeed apply leaves the engine.

The one outer-loop step in scope for this plan, and the one the operator called out: the outer
highway "assumes identifying the application route is solved", and it is not. An Indeed
`applystart` click lands on somebody else's site — Workday, Greenhouse, iCIMS, a branded wrapper,
or something we have never seen — and until we can answer *which site is this, and where is Apply*,
none of the per-ATS recipes downstream can start.

Two witnesses again, and here they are genuinely complementary rather than decoratively so:

  * **The URL** (`ats_registry.classify_ats`) is near-perfect when the host is one we know, and it
    answers `company_site` for everything else — including **branded wrappers**, where the employer
    serves a Workday or Greenhouse form from their own domain. That is not a rare edge: it is the
    KKR case already documented in `ats_registry`, and the reason the query-param tells exist.
  * **The pixels** (`perception`'s visual witness, via `bundle.belief`) were measured at **93–94%
    at PLATFORM level** and 55–58% at exact state. Platform is precisely the facet this module
    needs, and it is the facet a brand-new tenant preserves — the chrome is the vendor's.

So the interesting signal is the DISAGREEMENT: host says `company_site` while the screen looks like
Workday is a branded-wrapper tell, and one worth surfacing rather than averaging away.

Nothing here acts. It produces a scoreable `Decision` and a legible orientation, so that when the
drive parks at the deep end the teacher is answering a specific question rather than a shrug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from interaction.contract import Intent
from interaction.decision import Bundle, Decision

#: Controls that ENTER an application, most specific first. Deliberately excludes anything that
#: SUBMITS one: entering is reversible, submitting is not, and this module only ever proposes.
APPLY_CONTROLS: tuple[str, ...] = (
    "Apply on company site",
    "Apply for this job",
    "Start your application",
    "Apply now",
    "Easy Apply",
    "Apply",
)

#: Never proposed by this module, whatever a page calls them. A guard rather than a comment,
#: because "Submit application" contains "Apply" on some pages and the lexicon is substring-based.
NEVER_PROPOSE = ("submit", "send application", "confirm")

#: `classify_ats`'s answer when it does not recognise the host. Not a failure — a real state, and
#: exactly the one where the visual witness earns its place.
UNMAPPED = "company_site"

#: How much to trust an apply-control proposal. Below the acting floor by construction: entering
#: an ATS is a per-prospect decision the operator approves, never something a guess may do.
ORIENTATION_CONFIDENCE = 0.45


@dataclass
class Orientation:
    """Where we have landed, and how we know."""

    platform: str = ""                  # the fused answer
    url_platform: str = ""              # what the host/query tells say
    visual_platform: str = ""           # what the screen looks like
    agreement: str = "no_evidence"      # agree | split | one_sided | no_evidence
    branded_wrapper: bool = False       # host says unmapped, pixels name a vendor
    apply_control: str = ""             # the control that enters the application, if visible
    gaps: tuple[str, ...] = ()
    rationale: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "url_platform": self.url_platform,
                "visual_platform": self.visual_platform, "agreement": self.agreement,
                "branded_wrapper": self.branded_wrapper, "apply_control": self.apply_control,
                "gaps": list(self.gaps), "rationale": self.rationale}


def _visual_platform(belief: Optional[dict]) -> str:
    """The platform the VISUAL witness implies, read off its label's facets.

    Read from the witness rather than from the fused `belief.state`: the fused answer is mostly
    the DOM's, and the whole point here is a second opinion with a different failure mode. If the
    eyes were not consulted this turn (the cascade skips them when the ears are clear), there is
    simply no second opinion, and "no_evidence" is the honest report.
    """
    if not belief:
        return ""
    for witness in belief.get("witnesses") or ():
        if not isinstance(witness, dict) or not str(witness.get("name", "")).startswith("visual"):
            continue
        label = witness.get("label")
        if not label:
            return ""
        try:
            from perception.facets import platform_for
            return platform_for(str(label))
        except Exception:  # noqa: BLE001 — orientation must not depend on a fitted observer
            return ""
    return ""


def apply_control(identities: tuple[str, ...]) -> str:
    """The visible control that enters an application, or "".

    Returns the label AS RENDERED, not the lexicon entry that matched it — a proposal naming a
    control the page does not have is one nobody can approve as written.

    Public because `decide`'s phase rail needs the same answer on an `enter_apply` turn, and a
    second apply lexicon there would be a second opinion about which control enters a posting.
    """
    names = [ident.partition("|")[2].strip() for ident in identities or ()]
    # THE APPLY-DOOR JUDGEMENT, SHARED WITH THE RECIPE LAYER rather than re-learned here. This
    # matcher had the exact latent bug `advance_control` already fixed: a name the recipe's door
    # matcher refuses was reachable through this one. "Apply now Help" leads with the token and
    # out-lengths "Apply now" (MAPFRE, live 2026-08-14); employee doors out-length the candidate
    # door (C&S, 2026-08-13). The full door list is right HERE — unlike the advance lexicon,
    # beside an Apply control "save" and "with " really are disqualifiers. Lazy import, same
    # rule as decide's: orientation must still answer if the recipe layer is absent.
    try:
        from apply_recipe import GENERIC_CONTROL_EXCLUSIONS as _door_excluded
    except Exception:  # noqa: BLE001
        _door_excluded = ()
    names = [n for n in names if n and not any(bad in n.lower() for bad in NEVER_PROPOSE)
             and not any(bad in n.lower() for bad in _door_excluded)]
    for wanted in APPLY_CONTROLS:
        matches = [n for n in names if wanted.lower() in n.lower()]
        if matches:
            return max(matches, key=len)
    return ""


def orient(bundle: Bundle, *, page_hints: Optional[dict] = None) -> Orientation:
    """Which site is this, and where is Apply? PURE apart from the static registry lookup."""
    try:
        import ats_registry
        url_platform = ats_registry.classify_ats(bundle.url or "", page_hints)
    except Exception:  # noqa: BLE001
        url_platform = ""

    visual = _visual_platform(bundle.belief)
    identities = tuple(bundle.ax_identities or ())
    control = apply_control(identities)

    known_url = bool(url_platform) and url_platform not in (UNMAPPED, "unknown")
    branded = bool(visual) and visual not in ("generic", "company_site") and not known_url

    if known_url and visual:
        agreement = "agree" if visual == url_platform else "split"
    elif known_url or visual:
        agreement = "one_sided"
    else:
        agreement = "no_evidence"

    # The URL leads when it recognises the host: a host match is a fact, and the visual witness is
    # 93% — good, and not good enough to overrule a fact. It only LEADS where the URL has nothing,
    # which is exactly the branded-wrapper case it was brought in for.
    platform = url_platform if known_url else (visual or url_platform)

    gaps: list[str] = []
    if not identities:
        gaps.append("page:no-addressable-controls")
    if not known_url and not visual:
        gaps.append(f"platform:unidentified@{bundle.url or 'no-url'}")

    if branded:
        why = (f"the host is not one we recognise, but the screen looks like {visual} — a branded "
               f"wrapper serving {visual} from the employer's own domain")
    elif agreement == "split":
        why = (f"the host says {url_platform} and the screen looks like {visual}; the host is a "
               f"fact and leads, but the disagreement is worth recording")
    elif known_url:
        why = f"the host resolves to {url_platform}"
    elif visual:
        why = f"nothing in the URL identifies this; the screen looks like {visual}"
    else:
        why = "neither the URL nor the screen identifies this application system"

    return Orientation(
        platform=platform or "", url_platform=url_platform or "", visual_platform=visual,
        agreement=agreement, branded_wrapper=branded, apply_control=control,
        gaps=tuple(gaps), rationale=why,
        evidence=("url", "belief.witnesses", "ax_identities"))


def predict(bundle: Bundle, *, page_hints: Optional[dict] = None) -> Optional[Decision]:
    """The local prediction for a landing page — or None when this is not that kind of page.

    Returned as a `Decision` so it lands in the journal beside whatever the teacher does, which is
    what makes route identification measurable rather than anecdotal: over drives we can ask how
    often the local layers named the platform, and how often they found the Apply control.

    Always `escalate=True`. Entering an ATS is a per-prospect decision the operator approves; this
    proposes the click, it never takes it.
    """
    view = orient(bundle, page_hints=page_hints)
    if not view.apply_control:
        return None
    return Decision(
        intent=Intent.CLICK.value,
        params={"control": view.apply_control},
        confidence=ORIENTATION_CONFIDENCE,
        rung="recipe",
        rationale=(f"{view.rationale}; {view.apply_control!r} is the control that enters the "
                   f"application"),
        expected_next=tuple(bundle.expected_next),
        escalate=True,
        escalation_axis="unknown_state" if not bundle.state else "no_program",
        evidence=view.evidence,
    )
