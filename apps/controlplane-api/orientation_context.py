"""What do we already know, asked at the moment it would change what we do (SESSION 17).

THE CLASS THIS CLOSES, counted to EIGHT instances in the ten-session retrospective: *the fact
existed and nothing asked.* The registry note predicting Paylocity's upload modal, on file and
unread at the moment it mattered (08-19). Cornerstone's "rendered twice — drive the VISIBLE one",
written 08-11, rediscovered by screenshot on 08-24 — **in an entry `classify_ats` had already
loaded to name the platform**. Credentials in the vault while the flow opened a second account
row for the same employer (08-24). `tab_claims` unconsulted while classify read a parked tab.
Every producer existed; the deciding seam never asked.

WHAT THIS IS, AND WHAT IT IS NOT. It is a COMPOSED READ over authorities that already exist —
one function, no new tables, no retrieval, no cache. It is not a knowledge base: §15 says compose
reads at call time and never copy a fact out of its authority, because a copy is tomorrow's stale
snapshot (the `account_handoff` lesson, which took two sessions to learn).

THREE RULES IT KEEPS:

* **Cues are ADVISORY, never decision inputs.** The extraction below reads our own prose with
  word matching. That is imprecise on purpose: its output is a sentence shown to an operator and
  written into the trail, so a miss costs noise, never a wrong act. Nothing branches on a cue.
* **Silence is reported, never rendered as absence.** Every authority that had nothing to say is
  named in `silent`. An empty context that reads like a clean bill of health is the exact failure
  `ats_brief` was built to stop, one layer up.
* **Consultation must be VISIBLE or it did not happen.** `cite()` renders the one sentence a rung
  writes into its rationale. A fact that changed nothing cites nothing — the trail shows use, not
  ceremony.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: What each rung is about to do, in the vocabulary our own notes are written in. Used ONLY to
#: pick which sentences of a note to surface — never to decide anything.
_CONCERNS: dict[str, tuple[str, ...]] = {
    "open_pane": ("apply", "button", "control", "click", "card", "pane", "link", "visible",
                  "rendered", "twice", "modal", "dialog", "overlay", "door"),
    "enter_apply": ("apply", "button", "control", "click", "link", "visible", "rendered",
                    "twice", "modal", "dialog", "overlay", "redirect", "wrapper", "door"),
    "account": ("account", "sign in", "sign-in", "login", "log in", "credential", "password",
                "register", "create", "wall", "sso", "identifier", "profile"),
    "verify_email": ("verify", "verification", "email", "code", "confirm", "link", "factor"),
    "advance": ("field", "form", "required", "upload", "resume", "select", "dropdown", "step",
                "wizard", "continue", "next", "save", "question", "date"),
    "submit": ("submit", "confirmation", "review", "success", "received"),
    "classify": ("posting", "requisition", "url", "host", "tenant", "redirect", "wrapper"),
}
_MAX_CUES = 3
#: A cue longer than this is a paragraph wearing a full stop, and surfacing it is how a note
#: becomes wallpaper — which is how the Cornerstone note got skipped in the first place.
_MAX_CUE_CHARS = 220

#: THE UNIT IS A CLAUSE, NOT A SENTENCE, and that is a measurement rather than a preference. The
#: first cut of this split on sentence ends and dropped anything over 260 chars — which threw away
#: the exact line it was built to surface, because the real Cornerstone note packs the whole
#: finding into ONE 300-character sentence: "…its apply control is a plain button named 'Apply
#: Now' (rendered twice — masthead and footer — so drive the VISIBLE one), and the masthead
#: carries…". Our notes are written that way throughout. So a cue is the clause AROUND the match.
_CLAUSE_EDGE = re.compile(r"[.;:()]|\s—\s|,\s")


def _clause_around(text: str, at: int) -> str:
    """The readable clause containing position `at`, snapped to punctuation on both sides."""
    left = 0
    for m in _CLAUSE_EDGE.finditer(text, 0, at):
        left = m.end()
    right = len(text)
    m = _CLAUSE_EDGE.search(text, at)
    if m:
        right = m.start()
    return text[left:right].strip(" ,;:—()")


def note_cues(note: str, rung: Optional[str]) -> list[str]:
    """The clauses of a registry note that bear on what THIS rung is about to do.

    With no rung (or an unmapped one) the note's opening clauses answer "what is this platform",
    which is the classify-time question. Returns [] for an empty note — and the caller reports
    that as silence rather than as nothing to know.
    """
    text = " ".join((note or "").split())
    if not text:
        return []
    concerns = _CONCERNS.get(rung or "", ())
    if not concerns:
        opening = [_clause_around(text, m.start()) for m in re.finditer(r"\S+", text[:400])][:1]
        return [c[:_MAX_CUE_CHARS] for c in opening if c]

    low = text.lower()
    hits: list[tuple[int, str]] = []
    for concern in concerns:
        start = 0
        while (i := low.find(concern, start)) >= 0:
            clause = _clause_around(text, i)
            if clause and len(clause) <= _MAX_CUE_CHARS:
                hits.append((i, clause))
            start = i + len(concern)
    # A clause SHOUTING at the reader is the operative one — our notes put the instruction in caps
    # ("drive the VISIBLE one", "record it from a measurement, NEVER an inference"), the same
    # convention `prompt_escape` already relies on to tell an instruction from prose.
    seen: set[str] = set()
    ordered: list[str] = []
    for _, clause in sorted(hits, key=lambda h: (not _shouts(h[1]), h[0])):
        if clause not in seen:
            seen.add(clause)
            ordered.append(clause)
    return ordered[:_MAX_CUES]


def _shouts(clause: str) -> bool:
    return any(len(w) >= 4 and w.isupper() for w in re.findall(r"[A-Za-z]+", clause))


def orientation_context(db, *, url: str, rung: Optional[str] = None,
                        company: Optional[str] = None, job_id: Optional[str] = None,
                        tab_claims: Optional[dict[str, Any]] = None,
                        page: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Everything already on file that bears on this step, composed at call time.

    Every section degrades to `None` and names itself in `silent` — never to a fabricated empty.
    Nothing here raises: an orientation that can take the drive down is worse than one that is
    quiet, and each source is best-effort behind its own guard.
    """
    consulted: list[str] = []
    silent: list[str] = []
    out: dict[str, Any] = {"url": url, "rung": rung}

    # --- WHICH PLATFORM, FROM THE URL ALONE, BEFORE ANYTHING THAT NEEDS A DATABASE -------------
    # The first cut took `ats_id` from `ats_brief`, which needs a db — so on any db trouble the
    # REGISTRY NOTE went silent too, even though naming a platform from its host is pure and
    # free. That is the cheapest and most valuable authority here gated behind the most fragile
    # one, and it failed exactly that way in test. Classify first, independently; the brief's own
    # id is then a cross-check, not the source.
    ats_id = "unknown"
    try:
        import ats_registry as _reg
        ats_id = _reg.classify_ats(url) or "unknown"
    except Exception:
        ats_id = "unknown"

    # --- what the ATS tables know (the existing composer; it already keeps its denominators) ---
    brief: dict[str, Any] = {}
    try:
        import ats_brief
        brief = ats_brief.brief(url, db)
    except Exception:                                   # a hint must never take a drive down
        brief = {}
    ats_id = brief.get("ats_id") or ats_id
    out["ats_id"] = ats_id
    if brief:
        out["headline"] = brief.get("headline")
        out["auth_promise"] = (brief.get("vendor") or {}).get("auth")
        out["known"] = brief.get("known")
        consulted.append("ats_brief")
    else:
        out["headline"] = None
        silent.append("ats_brief")

    # --- THE REGISTRY NOTE: the eight-instance class, and `brief()` never returned it ----------
    # `classify_ats` loads this entry to name the platform and hands the prose straight back to
    # the shelf. Scoping it to the rung is what makes it readable at the moment it matters.
    cues: list[dict[str, str]] = []
    try:
        import ats_registry as reg
        entry = reg.get_ats(ats_id) or {}
        for text in note_cues(entry.get("notes") or "", rung):
            cues.append({"source": "registry_note", "text": text,
                         "why": f"this platform's note, on what the {rung or 'classify'} rung does"})
    except Exception:
        pass
    out["cues"] = cues
    consulted.append("registry_note") if cues else silent.append("registry_note")

    # --- IS THERE ALREADY AN ACCOUNT? asked BEFORE the wall renders, not after ------------------
    # 2026-08-24: `ats_odyssey_consulting_icims` sat in the store WITH credentials while the flow
    # opened `ats_odyssey_systems_consulting_group_ltd_icims` (pending, none). Operator: *"we had
    # the creds on file, should've checked there first."* Matching is deliberately loose across
    # legal-suffix noise, because that difference is what split the two rows.
    account = None
    if company:
        try:
            import ats_accounts
            account = ats_accounts.find_existing(db, company=company, ats_id=ats_id)
        except Exception:
            account = None
    out["account"] = account
    consulted.append("account_store") if account else silent.append("account_store")

    # --- WHOSE TAB IS THIS? (2026-08-24: a park leaves its tab alive, and classify read it) ----
    claim = None
    if tab_claims and job_id:
        for target, rec in (tab_claims or {}).items():
            holder = rec.get("job_id") if isinstance(rec, dict) else rec
            if holder and holder != job_id:
                claim = {"target": target, "held_by": holder,
                         "warning": "another job holds a tab here — claim by identity, never by "
                                    "'the newest apply-ish tab'"}
                break
    out["tab_conflict"] = claim
    consulted.append("tab_claims") if claim else silent.append("tab_claims")

    # --- CLAIMS ABOUT THIS SURFACE THAT THE WORLD HAS BEEN DRIVEN PAST (S16) --------------------
    stale: list[dict[str, str]] = []
    try:
        import world_facts as wfm
        host = (url or "").split("//")[-1].split("/")[0].lower()
        for entry in wfm.staleness_report().get("outdriven", []):
            if any(h in host or host in h for h in entry["surface"]["hosts"] if h):
                stale.append({"id": entry["id"], "claim": entry["claim"],
                              "outdriven_by_days": entry["outdriven_by_days"],
                              "recheck": entry["recheck"]})
    except Exception:
        stale = []
    out["stale_claims"] = stale[:2]
    consulted.append("world_facts") if stale else silent.append("world_facts")

    # --- WILL THIS STOP ON A HUMAN? -------------------------------------------------------------
    # `apply_requirements.blockers()` takes an iterable of Observations, and NOTHING PERSISTS
    # THEM — `observe()` builds them per page and they die with the request (verified 2026-08-27;
    # the 08-20 audit said the same). So the honest answer here is the auth promise, which IS
    # stored, plus a named gap. Silently returning [] would say "nothing will stop you", which is
    # the one thing this must never say.
    out["blockers"] = {
        "from_auth_promise": ["account"] if out.get("auth_promise") == "account" else [],
        "from_requirements_ledger": None,
        "gap": ("apply_requirements observations are produced per page and never stored, so the "
                "requirements axis cannot answer here yet — this is the auth promise only"),
    }
    # --- WHAT TO LOOK AT ON THIS PAGE, AND WHAT WE COULD NOT SEE (SESSION 18) ------------------
    # The half of orientation that comes from the page in front of us rather than from memory.
    # Only when the caller HAS readings — an observation report assembled from nothing would be
    # the confident silence this is built to end.
    observation = None
    if page:
        try:
            import observation_profiles as _op
            observation = _op.describe(
                kind=page.get("kind") or "", platform=page.get("platform") or "",
                page_text=page.get("text") or "", census=page.get("census"),
                candidates=page.get("candidates"), frames=page.get("frames"),
                content_source=page.get("content_source") or "")
        except Exception:
            observation = None
    out["observation"] = observation
    consulted.append("observation_profile") if observation else silent.append("observation_profile")

    out["consulted"] = consulted
    out["silent"] = silent
    return out


def cite(ctx: dict[str, Any]) -> str:
    """The one sentence a rung writes into its rationale — or "" when nothing was learned.

    Deliberately empty when every authority was silent: a trail that records "consulted, nothing
    known" on every crank trains the reader to skip the line, and the line is the whole point.
    """
    if not ctx:
        return ""
    parts: list[str] = []
    for cue in ctx.get("cues") or []:
        parts.append(cue["text"])
    acct = ctx.get("account")
    if acct:
        parts.append(f"an account for this employer is already on file "
                     f"({acct.get('account_id')}, {acct.get('status') or 'unknown status'})")
    if (ctx.get("tab_conflict") or {}).get("held_by"):
        parts.append(f"another job ({ctx['tab_conflict']['held_by']}) holds a tab here")
    if ctx.get("auth_promise") == "account":
        parts.append("expect an account wall — it will stop for the operator")
    for claim in ctx.get("stale_claims") or []:
        parts.append(f"claim '{claim['id']}' is {claim['outdriven_by_days']}d outdriven — "
                     f"treat it as unverified")
    obs = ctx.get("observation") or {}
    wiz = obs.get("wizard")
    if wiz and wiz.get("of"):
        # The page's OWN statement of how far this goes. Worth a line of its own because the
        # shared cadence's "at most 1 screen from Submit" was optimistic by five screens twice.
        parts.append(f"the page says step {wiz['step']} of {wiz['of']}")
    elif wiz and wiz.get("percent") is not None:
        parts.append(f"the page's own meter reads {wiz['percent']}%")
    gaps = obs.get("could_not_see") or []
    if gaps:
        # The COUNT here, not the text: the gaps are for reading on the card, and a rationale that
        # recites five caveats is one nobody finishes.
        parts.append(f"{len(gaps)} thing(s) this reading is blind to (see the card)")
    if not parts:
        return ""
    return "consulted: " + "; ".join(parts)
