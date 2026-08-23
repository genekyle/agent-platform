"""Which mail belongs to which ATS — one table, one row per ATS, read in both directions.

`senders_for` answers the errand's question: which inbox rows could plausibly be THIS ATS's
verification mail for THIS company. `classify_sender` answers the outcome matcher's: which ATS
sent this mail. One module for both directions so the two can never drift into separate,
disagreeing tables (PLAN_verify_email_leg.md tandem seam #1 and seam ruling 4 — created by the
verify-leg session with `classify_sender` minimal; the outcome-matcher session extends it and
folds its interim mail-domain table into `ATS_MAIL_DOMAINS` at rebase).

A row has two columns, per ruling 4:
  * **site** — the apply-destination hosts, DERIVED from `ats_registry`'s catalogue at read time.
    Not copied here: a second copy of the host list is a second place that can be wrong about it.
  * **mail** — the domains the ATS actually SENDS from, where those differ from (or are absent
    from) the site catalogue. This module's own column, `ATS_MAIL_DOMAINS`.

Why this exists at all: `errands._sender_hints` derives its hint from the caller's registry
domain_id (`indeed_jobs` → `indeed`), which is right when the caller IS the brand that sends the
mail and wrong for the account rung — "Boston Children's Hospital on BrassRing" gets its code from
`@trm.brassring.com`, and no suffix-stripping of a domain_id produces `brassring.com`. The
knowledge that does is the registry's host catalogue plus this module's mail column, which is why
hints are derived here and handed to the errand explicitly rather than taught to its fallback.

Hints are DOMAINS (plus the company phrase), matched by the errand as substrings of
`sender + " " + subject` lowercased — a domain hint matches the sender address precisely
("brassring.com" in "Talentsuite@trm.brassring.com"), and the company phrase catches the mail
whose sender is the employer's own address while the subject still names them.
"""

from __future__ import annotations

from typing import Any, Optional

import ats_registry
import errands

#: The MAIL column — one row per ATS, the shape the whole tandem folds into (ruling 4): the
#: outcome-matcher session merges its interim `ATS_MAIL_DOMAINS` INTO this dict at rebase, so keep
#: it exactly `{ats_id: (domains,)}`. Starts empty on purpose: the site catalogue covers the
#: common case (vendors mail from the domains they serve on), and a GUESSED mail domain is the
#: kind of plausible-wrong hint that matches somebody else's code. Rows are added when a sender is
#: MEASURED (the verify seam also writes instance-scoped `verification_sender` characteristics,
#: which `senders_for` prefers over anything here).
ATS_MAIL_DOMAINS: dict[str, tuple[str, ...]] = {}

#: A company hint shorter than this is not a hint — "GE" as a substring matches "message",
#: "manage", and half the inbox. The freshness window and the ambiguity refusal soften a miss;
#: nothing softens a wrong code typed into a live signup.
_MIN_COMPANY_HINT = 4


def _site_domains(ats_id: str) -> list[str]:
    """The registry's host catalogue for one ATS, filtered to strings that can match a sender
    address. Entries like `wd1.` are URL substrings, not domains — a sender hint has to be able
    to appear in `user@host`, and a bare label that short would over-match subjects."""
    ats = ats_registry.get_ats(ats_id) or {}
    out: list[str] = []
    for host in ats.get("hosts") or ():
        h = str(host).strip().lower()
        if not h or h.endswith(".") or "." not in h:
            continue
        out.append(h)
    return out


def domains_for(ats_id: str) -> dict[str, tuple[str, ...]]:
    """One ATS's row, both columns — the single read both directions go through. `mail` is this
    module's own column; `site` is derived from the registry catalogue at read time."""
    key = (ats_id or "").strip().lower()
    return {"mail": tuple(ATS_MAIL_DOMAINS.get(key, ())),
            "site": tuple(_site_domains(key))}


def _measured_senders(ats_id: str, db: Any) -> list[str]:
    """Sender domains this ATS has actually been SEEN to mail from — the instance-scoped
    `verification_sender` characteristics the verify seam writes on a successful code match.
    Preferred over both table columns (the 08-21 consult-side rule): a measurement outranks a
    constant."""
    if db is None:
        return []
    try:
        import models
        rows = (db.query(models.AtsCharacteristic)
                .filter_by(ats_id=(ats_id or "").lower(), kind="auth", key="verification_sender")
                .all())
        return [str(r.value).strip().lower() for r in rows if str(r.value or "").strip()]
    except Exception:  # noqa: BLE001 — hints are best-effort; the table still stands
        return []


def senders_for(ats_id: str, company: Optional[str] = None,
                instance_domain: Optional[str] = None, db: Any = None) -> list[str]:
    """Hint strings for the fetch_login_code errand, for one (ATS, company) verification mail.

    Order is preference: measured senders, then the mail column, then the site catalogue, then
    the company phrase and the instance's own domain. The errand ORs them (`_matches_caller`), so
    order does not change what matches — it changes what the evidence's `matched_on` cites, and
    citing a measurement over a constant is the more diagnosable answer.

    Falls back to `errands.domain_stem` when nothing else derives — the old suffix-stripping path,
    demoted from competing default to last resort exactly as the plan prescribes.
    """
    row = domains_for(ats_id)
    hints: list[str] = []
    hints += _measured_senders(ats_id, db)
    hints += list(row["mail"])
    hints += list(row["site"])
    if company and len(company.strip()) >= _MIN_COMPANY_HINT:
        hints.append(company.strip().lower())
    if instance_domain and "." in str(instance_domain):
        hints.append(str(instance_domain).strip().lower())
    if not hints:
        stem = errands.domain_stem(ats_id)
        if stem:
            hints.append(stem)
    # Dedupe, order preserved — the first hit is the one the evidence cites.
    seen: set[str] = set()
    return [h for h in hints if h and not (h in seen or seen.add(h))]


def classify_sender(from_address: str) -> Optional[str]:
    """The same table read backwards: which ATS does this sender address belong to, or None.

    MINIMAL by contract (seam ruling 4): suffix-match the address's domain against each ATS row —
    mail column first, then site — nothing else. The signature stays `(from_address) -> ats_id |
    None`; richer attribution, if ever needed, is a second function, not a wider one. The outcome
    matcher extends the TABLE (its interim domains fold into `ATS_MAIL_DOMAINS`), employer-domain
    and display-name heuristics belong to that session's lane, not here.
    """
    addr = str(from_address or "").strip().lower().rstrip(">")
    if "@" not in addr:
        return None
    domain = addr.rsplit("@", 1)[1].split()[0].strip()
    if not domain:
        return None
    for ats in ats_registry.ATS_PLATFORMS:
        row = domains_for(ats["ats_id"])
        for host in row["mail"] + row["site"]:
            if domain == host or domain.endswith("." + host) or host.endswith("." + domain):
                return ats["ats_id"]
    return None
