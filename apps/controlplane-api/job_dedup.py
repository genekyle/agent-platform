"""Are these two postings the same job? — the matcher, and the merge it drives.

Two different questions are asked of this module and they deserve different answers:

    "has this been applied to?"   → applied_index.check()   (reads; a false yes SKIPS a job)
    "are these one job?"          → propose_matches()        (writes; a false yes HIDES a job)

Both are wrong in the same direction, so both use the primitives below, and both treat the weak
tier as advisory. `applied_index` imports from here rather than keeping its own copy — the
similarity fixes recorded below apply to *every* place the question gets asked, which is the
point of having one implementation.

--------------------------------------------------------------------------------------
What the real corpus proved (measured 2026-07-30, 355 rows)
--------------------------------------------------------------------------------------
The original scorer used containment with a `min()` denominator, so the SHORTER title only had
to be a subset of the longer. Run over live data it produced 31 above-threshold pairs of which
roughly 3 were real. The failures were not random; they were three specific mechanisms:

    'Software Engineer'      == 'Senior Data Integration Software Engineer'   (1.00)
        A generic two-word title is a subset of every richer title at that employer.
        → FIX: containment is only allowed when the shorter title carries at least
          MIN_CONTAINMENT_TOKENS distinctive words. Below that, fall back to Jaccard.

    'Senior Associate - Data Governance Analyst' == 'Senior Associate - Data Analyst'  (1.00)
        The extra word is INTERLEAVED, and it changes the role. Contrast the case containment
        exists to protect — 'Healthcare Data Analyst' vs 'Healthcare Data Analyst - BIDMC,
        OBGYN Quality' — where the extra words are APPENDED and are just a department.
        → FIX: containment requires the shorter title to appear as a CONTIGUOUS run in the
          longer one. Decoration is appended; a qualifier is interleaved.

    'Financial Analyst III'  == 'Financial Analyst II'                        (1.00)
        The seniority numeral was being deleted before comparison, because the token filter
        dropped words of two characters or fewer — erasing exactly the distinction the old
        docstring claimed the filter preserved.
        → FIX: levels are extracted BEFORE filtering and compared separately. Two different
          levels veto the match outright; they are, definitionally, two requisitions.

--------------------------------------------------------------------------------------
Which tiers are allowed to act
--------------------------------------------------------------------------------------
    requisition      a req id shared by both records            → MERGED automatically
    same_posting     one engine, one employer, one role, one
                     place, no disagreement about pay           → MERGED automatically
    identical_title  same employer and role, but off two boards
                     or with the place unstated                 → PROPOSED, operator decides
    blank_company    identical title, one side scraped no
                     employer                                   → PROPOSED, operator decides
    fuzzy_title      same employer, similar title               → PROPOSED, operator decides

Two tiers merge on their own and both are evidence rather than inference. `requisition` is the
same id on both records. `same_posting` is every field of the card agreeing, off ONE board —
measured 2026-07-30, Indeed served Bristol County Savings Bank's 'Human Resources Data Analyst'
five times inside one result page, five ids, one requisition, identical employer, title, address
and pay. A board re-rendering its own card is not two jobs, and asking a human five times whether
it is teaches them to stop reading the queue.

Everything below those two enqueues a `JobMatch` for review, on the rule `applied_index` already
lived by: **a near-miss that silently hides a job the operator picked is worse than one that
asks.** What decides the boundary is whether the fields CORROBORATE each other or merely fail to
contradict — see `duplicate_tier`. A field that positively disagrees ends the comparison outright:
Liberty Mutual's 'Principal SAP PaPM Configuration Engineer' is posted in Boston AND in
Portsmouth, and those two are not a near-miss to be queued, they are two jobs.

Rejections are remembered. A pair the operator has called different is never proposed again.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from deps import as_aware, utcnow
from models import Application, ApplicationEvent, Job, JobMatch, ObservedJob

# --------------------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------------------

#: Words that carry no signal about WHICH role a posting is, so they must not prop up a match.
#: Without this, 'Senior Analyst - Boston' and 'Analyst - Boston' share most of their words.
_NOISE = frozenset({
    "the", "and", "for", "with", "our", "you", "your", "job", "jobs", "role", "position",
    "opening", "opportunity", "full", "time", "part", "remote", "hybrid", "onsite", "new",
})

#: How a board decorates a place without changing which place it is. Measured 2026-07-30: Indeed
#: writes the SAME office four different ways — 'Boston, MA', 'Hybrid work in Boston, MA',
#: 'Boston, MA (Remote)', 'Burlington, MA (Hybrid)'. Comparing these raw makes one posting look
#: like two; the arrangement is not the address.
_LOCATION_PREFIXES = ("hybrid work in ", "remote work in ", "hybrid remote in ", "temporarily remote in ")
_LOCATION_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*")
_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")

#: Company suffixes only. Kept separate from `_NOISE` because these are noise in a COMPANY name
#: and meaningful in a TITLE — 'Health' is nothing in 'Beth Israel Lahey Health' and is the whole
#: subject in 'Health Data Analyst'. Folding the two lists together loses that.
_COMPANY_NOISE = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "company", "group", "holdings", "the", "and",
    "co", "plc", "lp", "llp", "sa", "ag", "nv", "gmbh",
})

#: Seniority written the many ways ATS platforms write it, mapped to one word so 'Sr.' and
#: 'Senior' stop reading as different roles.
_SENIORITY_ALIASES = {
    "sr": "senior", "snr": "senior", "senior": "senior",
    "jr": "junior", "junior": "junior",
    "principal": "principal", "staff": "staff", "lead": "lead",
    "associate": "associate", "assistant": "assistant", "intern": "intern",
    "director": "director", "manager": "manager", "head": "head", "chief": "chief",
    "vp": "vp", "avp": "vp", "svp": "vp",
}

#: The seniority words that describe RANK rather than function. 'Manager' and 'Director' are
#: excluded on purpose: they are the job, not a modifier of it, and are compared as content.
_RANK_WORDS = frozenset({"senior", "junior", "principal", "staff", "lead", "associate",
                         "assistant", "intern"})

#: Level numerals as they appear in titles: 'Analyst II', 'Engineer 3', 'AVP II'.
_LEVELS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
           "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def _raw_words(text: str) -> list[str]:
    """Lowercased alphanumeric words, in order. Punctuation becomes a space rather than being
    deleted, because the SAME title arrives with an en-dash on Indeed and a hyphen on Workday."""
    return "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()


def level_ranks(title: str) -> set[int]:
    """The seniority numerals in a title: 'Financial Analyst III' → {3}.

    Extracted before the token filter runs, because the filter drops short words and would
    otherwise erase 'II' — the measured bug that made 'Analyst II' and 'Analyst III' identical.
    """
    return {_LEVELS[w] for w in _raw_words(title) if w in _LEVELS}


def rank_words(title: str) -> set[str]:
    """The seniority modifiers in a title, normalised: 'Sr. Data Analyst' → {'senior'}."""
    return {_SENIORITY_ALIASES[w] for w in _raw_words(title)
            if w in _SENIORITY_ALIASES and _SENIORITY_ALIASES[w] in _RANK_WORDS}


def title_tokens(title: str) -> list[str]:
    """The content words of a title, in order, with rank words normalised and levels removed.

    Order is preserved because the contiguity test depends on it — see `title_similarity`.
    Levels are dropped here and compared separately, so 'Analyst II' and 'Analyst III' have
    IDENTICAL content and are distinguished purely by the level veto. That keeps the two signals
    from cancelling each other out.
    """
    out: list[str] = []
    for w in _raw_words(title):
        if w in _LEVELS:
            continue
        w = _SENIORITY_ALIASES.get(w, w)
        if w in _NOISE or len(w) < 3:
            continue
        if out and out[-1] == w:      # 'Analyst, Analyst' from a doubled scrape
            continue
        out.append(w)
    return out


def normalize_company(name: str) -> str:
    """A company name reduced to its identifying words, order-independent. 'Beth Israel Lahey
    Health' and 'Beth Israel Lahey Health, Inc.' are one employer; the suffix is noise."""
    words = {w for w in _raw_words(name) if len(w) > 1 and w not in _COMPANY_NOISE}
    return " ".join(sorted(words))


def normalize_location(text: str) -> str:
    """A place reduced to the place, with the board's decoration stripped off.

    Order-preserving, unlike `normalize_company`, because a location is a hierarchy and sorting its
    words would make 'Boston, MA' and 'MA, Boston' the same string for no reason. The three things
    removed are the three things measured in the corpus, and none of them names a different office:

        'Hybrid work in Boston, MA'   → 'boston ma'      (an arrangement, not an address)
        'Burlington, MA (Hybrid)'     → 'burlington ma'  (the same, in a parenthesis)
        'Taunton, MA 02780'           → 'taunton ma'     (a zip one scrape carried and another did not)

    Returns '' for anything that names no place — the empty string means UNKNOWN here, never
    'nowhere', which is why every caller below asks `locations_conflict` rather than `!=`.
    """
    s = (text or "").strip().lower()
    for prefix in _LOCATION_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = _LOCATION_PARENTHETICAL.sub(" ", s)
    s = _ZIP.sub(" ", s)
    return " ".join(_raw_words(s))


def locations_conflict(a: str, b: str) -> bool:
    """Do these two places positively disagree?

    False when either side is blank. A sighting that scraped no location is UNKNOWN, and unknown is
    not evidence of difference — measured: DEKA Research's back-end engineer sits in the corpus
    twice, once with 'Manchester, NH 03101' and once with nothing at all, and it is one job.
    """
    na, nb = normalize_location(a), normalize_location(b)
    return bool(na) and bool(nb) and na != nb


def locations_agree(a: str, b: str) -> bool:
    """Do these two places positively AGREE? Both must be known. The strictly stronger test, and
    the one an unattended merge is held to — see `duplicate_tier`."""
    na, nb = normalize_location(a), normalize_location(b)
    return bool(na) and na == nb


def normalize_pay(text: str) -> str:
    """A pay string reduced to the numbers and the period it is quoted over.

    Deliberately crude — this is a corroborating signal, not an identity. 'From $60,000 a year' and
    'From $60,000 a year' must match; nothing here tries to decide whether $60,000/yr equals
    $28.85/hr, because two cards for one requisition are rendered by the same board from the same
    field and do not disagree that way.
    """
    return " ".join(_raw_words(text))


def pay_conflicts(a: str, b: str) -> bool:
    """Do these two pay quotes positively disagree? Blank on either side is unknown, not different:
    Indeed omits pay on most cards, and both-blank is the commonest shape of a real duplicate."""
    na, nb = normalize_pay(a), normalize_pay(b)
    return bool(na) and bool(nb) and na != nb


# --------------------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------------------

#: How many distinctive words the shorter title must carry before containment is trusted at all.
#: Two-word titles ('Business Analyst', 'Software Engineer') are subsets of half the postings at
#: a large employer, so they are scored by overlap instead. Measured: this single guard removes
#: the majority of the false pairs found in the 355-row corpus.
MIN_CONTAINMENT_TOKENS = 3

#: Asymmetric rank ('Data Engineer' vs 'Senior Data Engineer') — the same function at a different
#: grade, which is two requisitions. Penalised rather than vetoed so a genuine pair whose rank was
#: simply omitted on one board can still reach the review queue if everything else lines up.
_RANK_MISMATCH_FACTOR = 0.6
#: One title states a level and the other omits it. Weaker evidence of difference than a rank
#: mismatch, but still evidence.
_LEVEL_ONE_SIDED_FACTOR = 0.7


def _contiguous(short: list[str], long: list[str]) -> bool:
    """Does `short` appear as an unbroken run inside `long`?

    This is the whole distinction between decoration and qualification:

        ['healthcare','data','analyst'] in ['healthcare','data','analyst','bidmc','obgyn']  → True
        ['senior','associate','data','analyst'] in ['senior','associate','data',
                                                    'governance','analyst']                → False

    The first is the same job with a department appended. The second is a different job with a
    word inserted. Set containment cannot tell them apart; order can.
    """
    if not short or len(short) > len(long):
        return False
    first = short[0]
    for i in range(len(long) - len(short) + 1):
        if long[i] == first and long[i:i + len(short)] == short:
            return True
    return False


def title_similarity(a: str, b: str) -> float:
    """How likely two titles name the same requisition, 0..1. See the module docstring for the
    three real failures this scoring is shaped around."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0

    # Hard veto: two stated levels that differ are two requisitions, whatever else matches.
    la, lb = level_ranks(a), level_ranks(b)
    if la and lb and la != lb:
        return 0.0

    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= MIN_CONTAINMENT_TOKENS and _contiguous(short, long):
        score = 1.0
    else:
        sa, sb = set(ta), set(tb)
        score = len(sa & sb) / len(sa | sb)      # Jaccard: no free ride for being short

    ra, rb = rank_words(a), rank_words(b)
    if ra != rb:
        score *= _RANK_MISMATCH_FACTOR
    if bool(la) != bool(lb):
        score *= _LEVEL_ONE_SIDED_FACTOR
    return score


#: Below this, two titles at the same employer are not worth the operator's attention. Raised
#: from the original 0.75 alongside the scoring fixes: at 0.75 the repaired scorer still surfaced
#: 'Senior Associate - Data Analyst' against '- Data Governance Analyst' (0.80).
FUZZY_TITLE_THRESHOLD = 0.85


# --------------------------------------------------------------------------------------
# One posting, or two? — the rule every UNATTENDED decision is held to
# --------------------------------------------------------------------------------------
#
# There are two places in this file where nothing asks a human first: `resolve_sighting` points a
# freshly-scraped card at an existing job, and `scan_and_record` auto-merges a tier in
# `AUTO_MERGE_TIERS`. Those were written at different times and disagreed about the same question —
# measured 2026-07-30, and the disagreement went both ways at once:
#
#   * `resolve_sighting` attached on employer + title alone, with NO location test. Four genuinely
#     distinct pairs were already silently collapsed in the live table, Liberty Mutual's 'Principal
#     SAP PaPM Configuration Engineer' in Boston and in Portsmouth among them — two real postings
#     rendered as one row, and no `JobMatch` written, so nothing recorded that a choice was made.
#   * `compare` was stricter in the other direction: identical title at one employer only ever
#     PROPOSED, so Indeed serving one Bristol County Savings Bank requisition five times could
#     never collapse unattended even though every field on all five cards was identical.
#
# So the rule lives here once and both callers ask it. An unattended decision is held to the same
# evidence wherever it is made, which is the only version of this that stays true as either caller
# is edited later.

@dataclass(frozen=True)
class Posting:
    """The fields that decide identity, read off either a `Job` or an `ObservedJob`.

    A tiny view rather than a protocol, because the two rows spell the same facts differently —
    `Job.source_platforms` is a list, `ObservedJob.platform` is one string — and the comparison
    should not have to know which kind of row it was handed.
    """

    company_norm: str
    title: str
    location: str
    salary: str
    platforms: frozenset[str]


def posting_of(row: Any) -> Posting:
    """Read a `Job` or an `ObservedJob` as a `Posting`."""
    platforms = getattr(row, "source_platforms", None)
    if platforms is None:
        platforms = [getattr(row, "platform", "")]
    return Posting(
        company_norm=getattr(row, "company_norm", None) or normalize_company(getattr(row, "company", "")),
        title=row.title or "",
        location=row.location or "",
        salary=getattr(row, "salary", "") or "",
        platforms=frozenset(p for p in (platforms or []) if p),
    )


def one_role(a: Posting, b: Posting) -> bool:
    """Same employer, and a title naming the same role at the same grade.

    Levels are compared separately from content because `title_tokens` deletes them — that split is
    what keeps 'Financial Analyst II' and 'Financial Analyst III' apart, and Elbit America has
    exactly that pair at one address in Merrimack, correctly on file as two jobs.
    """
    if not a.company_norm or a.company_norm != b.company_norm:
        return False
    ta, tb = title_tokens(a.title), title_tokens(b.title)
    return bool(ta) and ta == tb and level_ranks(a.title) == level_ranks(b.title)


def field_conflict(a: Posting, b: Posting) -> Optional[str]:
    """A field on which these two POSITIVELY disagree, said in words, or None.

    Separate from the role test because the two are not interchangeable evidence. A different role
    is a reason not to merge two rows in the first place; a different address is a reason to doubt
    an attachment that already happened, whatever the titles say. `split_conflicting_sightings`
    needs the second without the first, and merging them into one predicate is what made its first
    version offer to undo a merge a human had approved.
    """
    if locations_conflict(a.location, b.location):
        return f"different location ({a.location} / {b.location})"
    if pay_conflicts(a.salary, b.salary):
        return f"different pay ({a.salary} / {b.salary})"
    return None


def conflict_reason(a: Posting, b: Posting) -> Optional[str]:
    """Why these two cannot be one posting, said in words, or None if nothing rules it out.

    A reason rather than a bool because it is written into `JobMatch.evidence` and read back by the
    operator in the review queue — a merge that cannot explain itself is one nobody can check
    (PRINCIPLES §10). Absence of a reason is NOT proof of sameness; it is the floor an unattended
    decision has to clear, and `duplicate_tier` asks for more.
    """
    if not one_role(a, b):
        return "different role"
    return field_conflict(a, b)


def duplicate_tier(a: Posting, b: Posting) -> Optional[str]:
    """The strongest tier these two earn on their own fields, or None.

        same_posting     one engine, one employer, one role, locations AGREE, pay does not
                         disagree  → strong enough to merge unattended
        identical_title  the same role at the same employer, but the corroboration is missing or
                         the two came off different boards → proposed, operator decides

    The engine has to be the same one for the auto tier, and that is the point rather than an
    incidental filter: this tier exists because a single board re-renders one requisition under
    several ids inside one result page. Two boards agreeing is a different (and weaker) claim about
    a wider world, so it keeps going to the queue.

    Location must positively AGREE here, where `conflict_reason` only asks that it not disagree.
    Attaching a card to a job it resembles is cheap and reversible; writing a merge tombstone
    between two established rows is neither, so it is made to show its evidence rather than merely
    fail to find any against itself.
    """
    if conflict_reason(a, b) is not None:
        return None
    one_engine = len(a.platforms | b.platforms) == 1 and bool(a.platforms)
    if one_engine and locations_agree(a.location, b.location):
        return "same_posting"
    return "identical_title"


# --------------------------------------------------------------------------------------
# Requisition ids
# --------------------------------------------------------------------------------------

#: A requisition id as ATS platforms write them. Matched against both records' urls and tenant ids.
_REQ_PATTERNS = (
    re.compile(r"\b(jr[-_]?\d{4,})\b", re.I),            # Workday: JR88822
    re.compile(r"\bgh_jid=(\d{4,})\b", re.I),            # Greenhouse
    re.compile(r"/jobs?/(\d{3,})(?:/|\b)", re.I),        # iCIMS: /jobs/3915/
    re.compile(r"\breq[-_]?(\d{4,})\b", re.I),           # generic REQ-12345
)


def requisition_ids(*texts: Optional[str]) -> set[str]:
    """Every requisition id readable out of these urls / ids, lowercased and punctuation-free
    (so 'JR-88822' and 'jr88822' are one id).

    Measured 2026-07-30: only 2 of 355 sightings yield anything here, because we store the
    Indeed url and never the ATS url we land on. `Job.requisition_id` is where the apply
    epilogue is meant to write the real one back — until it does, this tier stays near-dead.
    """
    out: set[str] = set()
    for text in texts:
        for pat in _REQ_PATTERNS:
            for m in pat.finditer(str(text or "")):
                out.add(re.sub(r"[-_]", "", m.group(1)).lower())
    return out


# --------------------------------------------------------------------------------------
# Canonical keys
# --------------------------------------------------------------------------------------

def mint_job_key(seed: str) -> str:
    """A stable canonical key derived from a sighting id.

    Deterministic so the backfill is idempotent — re-running it resolves the same sighting to the
    same job instead of minting a second one. Opaque so that nothing downstream is tempted to
    parse a company or title back out of it: the key identifies the job, it does not describe it.
    """
    return "job_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def resolve_key(db: Session, job_key: str, *, _depth: int = 0) -> Optional[str]:
    """Follow merge tombstones to the job that is actually alive.

    Every external reference — a Gmail match, a tracker row, a bookmarked URL — goes through here,
    which is why merges tombstone instead of deleting. The depth cap is paranoia about a cycle
    that the merge path already prevents; it degrades to "stop here" rather than hanging a request.
    """
    seen: set[str] = set()
    key = job_key
    while key and _depth < 16:
        if key in seen:
            return key
        seen.add(key)
        row = db.get(Job, key)
        if row is None:
            return None
        if not row.merged_into_key:
            return row.job_key
        key = row.merged_into_key
        _depth += 1
    return key


# --------------------------------------------------------------------------------------
# Match proposals
# --------------------------------------------------------------------------------------

#: Tiers trustworthy enough to merge without asking. Deliberately short — see module docstring.
#: `same_posting` earned its place on measured evidence rather than on the argument that it sounds
#: safe: it is every field of the card agreeing, off one board, and the four tiers below it are all
#: still proposals.
AUTO_MERGE_TIERS = frozenset({"requisition", "same_posting"})


@dataclass
class MatchProposal:
    """Two jobs that may be one, and the case for it."""

    kept_key: str
    folded_key: str
    tier: str
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)

    @property
    def auto(self) -> bool:
        return self.tier in AUTO_MERGE_TIERS


def compare(a: Job, b: Job) -> Optional[MatchProposal]:
    """The case, if any, that these two canonical jobs are the same posting.

    Which row survives is decided here rather than by the caller, and it is always the OLDER one:
    the merge direction has to be stable, or the same pair proposes twice under two orderings and
    the review queue fills with mirror images of itself.
    """
    if a.job_key == b.job_key:
        return None
    keep, fold = (a, b) if (as_aware(a.first_seen_at), a.job_key) <= (as_aware(b.first_seen_at), b.job_key) else (b, a)

    shared_reqs = (requisition_ids(a.requisition_id, a.canonical_url)
                   & requisition_ids(b.requisition_id, b.canonical_url))
    if shared_reqs:
        return MatchProposal(keep.job_key, fold.job_key, "requisition", 1.0,
                             [f"requisition {sorted(shared_reqs)[0]}"])

    # A sighting whose company cell failed to parse. Measured 2026-07-30: 9 of 355 rows, and every
    # one of them duplicated a job we already had WITH its employer — 'EPIC Caboodle/Clarity Report
    # Writer' sat in the table twice, once as SolutionHealth and once as nobody. Comparing titles
    # across all employers would be reckless; comparing them when one side names no employer at all
    # is just reading the scrape correctly. Identical titles only, and it still asks.
    if bool(a.company_norm) != bool(b.company_norm):
        if title_tokens(a.title) and title_tokens(a.title) == title_tokens(b.title):
            # The NAMED row survives, overriding the usual oldest-wins rule. Oldest-wins exists to
            # make the direction stable, and it still is — but here the two rows are not equally
            # good records, and keeping the one that knows the employer is obviously right.
            named, blank = (a, b) if a.company_norm else (b, a)
            return MatchProposal(named.job_key, blank.job_key, "blank_company", 1.0,
                                 [f"identical title ({named.title})",
                                  f"one side scraped without an employer; the other says {named.company}"])
        return None

    if not a.company_norm or a.company_norm != b.company_norm:
        return None

    pa, pb = posting_of(a), posting_of(b)
    evidence = [f"same employer ({keep.company})"]
    platforms = sorted(pa.platforms | pb.platforms)
    if len(platforms) > 1:
        evidence.append(f"seen on {', '.join(platforms)}")

    if one_role(pa, pb):
        tier = duplicate_tier(pa, pb)
        if tier is None:
            # Same role, but a field positively disagrees — two requisitions, not a near-miss.
            # Returning None here is what keeps Liberty Mutual's Boston and Portsmouth postings out
            # of the queue entirely, rather than merely out of the auto tier.
            return None
        evidence.insert(0, f"identical title ({keep.title})")
        if locations_agree(pa.location, pb.location):
            evidence.append(f"same location ({normalize_location(keep.location)})")
        if normalize_pay(pa.salary) and normalize_pay(pa.salary) == normalize_pay(pb.salary):
            evidence.append(f"same pay ({keep.salary})")
        if tier == "same_posting":
            evidence.append(f"one engine ({platforms[0]}) serving the same card more than once")
        return MatchProposal(keep.job_key, fold.job_key, tier, 1.0, evidence)

    score = title_similarity(a.title, b.title)
    if score < FUZZY_TITLE_THRESHOLD:
        return None
    if locations_conflict(pa.location, pb.location):
        return None
    evidence.append(f"title overlap {score:.0%}: {a.title!r} / {b.title!r}")
    return MatchProposal(keep.job_key, fold.job_key, "fuzzy_title", score, evidence)


def _pair_id(x: str, y: str) -> tuple[str, str]:
    return (x, y) if x <= y else (y, x)


def _candidate_groups(rows: list[Job]) -> list[list[Job]]:
    """Bucket jobs so that any two that could be duplicates land in a bucket together.

    Two buckets, not one, and the second is not optional. Grouping by employer alone is the
    obvious move and it quietly disables the requisition tier — the ONE tier trusted enough to
    merge unattended — because a shared requisition is precisely the evidence that survives the
    company being spelled 'BILH' on one board and 'Beth Israel Lahey Health' on the other. That
    is the exact pair the tier was written for, so it gets its own bucket keyed on the id itself.

    A third bucket keys on the title itself, and is emitted ONLY when it contains a job with no
    employer — the scrape-miss case in `compare`. Without it those rows sit in a bucket of one
    (they match no company) and their duplicate is never even considered.
    """
    by_company: dict[str, list[Job]] = {}
    by_req: dict[str, list[Job]] = {}
    by_title: dict[tuple[str, ...], list[Job]] = {}
    for r in rows:
        by_company.setdefault(r.company_norm or f"\x00{r.job_key}", []).append(r)
        for req in requisition_ids(r.requisition_id, r.canonical_url):
            by_req.setdefault(req, []).append(r)
        tokens = tuple(title_tokens(r.title))
        if tokens:
            by_title.setdefault(tokens, []).append(r)

    title_groups = [g for g in by_title.values()
                    if len(g) > 1 and any(not r.company_norm for r in g)]
    return [g for g in (*by_company.values(), *by_req.values(), *title_groups) if len(g) > 1]


def recount_job(db: Session, job: Job) -> Job:
    """Recompute a job's tallies and provenance from the sightings that point at it.

    Derived from the rows rather than incremented alongside them, because incremented counters
    drift the moment anything merges — and the first version of this did exactly that, adding one
    sighting's `seen_count` to another's and producing a job "sighted 38 times" that had two
    sighting rows. A number nobody can explain is worse than no number.
    """
    sightings = list(db.scalars(select(ObservedJob)
                                .where(ObservedJob.canonical_job_key == job.job_key)).all())
    job.sighting_count = len(sightings)
    job.seen_count = sum((s.seen_count or 1) for s in sightings)
    job.source_platforms = sorted({s.platform for s in sightings if s.platform})
    if sightings:
        job.first_seen_at = min(as_aware(s.first_seen_at) for s in sightings)
        job.last_seen_at = max(as_aware(s.last_seen_at) for s in sightings)
    return job


def recount_all(db: Session) -> int:
    """Recount every live job. Cheap, idempotent, and the repair for any drift."""
    jobs = list(db.scalars(select(Job).where(Job.merged_into_key.is_(None))).all())
    for j in jobs:
        recount_job(db, j)
    db.commit()
    return len(jobs)


def propose_matches(db: Session, *, limit: int = 500) -> list[MatchProposal]:
    """Scan live jobs for duplicates.

    `company_norm` is a stored, indexed column for this: two jobs at different employers are
    almost never the same job, so the quadratic comparison happens inside one company's handful
    of rows rather than across the table. The requisition bucket is the deliberate exception.
    """
    rows = list(db.scalars(select(Job).where(Job.merged_into_key.is_(None))).all())
    known = _decided_pairs(db)
    seen_pairs: set[tuple[str, str]] = set()
    out: list[MatchProposal] = []

    for group in _candidate_groups(rows):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                pair = _pair_id(group[i].job_key, group[j].job_key)
                # A pair can sit in both buckets; compare it once.
                if pair in known or pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                proposal = compare(group[i], group[j])
                if proposal is not None:
                    out.append(proposal)
                    if len(out) >= limit:
                        return out
    return out


def _decided_pairs(db: Session) -> set[tuple[str, str]]:
    """Pairs already ruled on — merged or rejected — so neither is proposed a second time.

    Keeping rejections is the load-bearing half: without it, every scan re-asks the operator about
    the same two jobs they already said were different, and the queue becomes noise they stop
    reading.
    """
    return {_pair_id(m.kept_key, m.folded_key)
            for m in db.scalars(select(JobMatch)).all()}


def scan_and_record(db: Session, *, limit: int = 500) -> dict[str, int]:
    """Propose, auto-merge what is certain, enqueue the rest. Commits once.

    The one entry point worth calling from a route or a sweep: it leaves the database with fewer
    duplicate jobs and a review queue holding exactly the decisions a human still owes.
    """
    merged = queued = 0
    for p in propose_matches(db, limit=limit):
        # Re-resolve: an earlier merge in this same pass may have tombstoned one side.
        kept, folded = resolve_key(db, p.kept_key), resolve_key(db, p.folded_key)
        if not kept or not folded or kept == folded:
            continue
        record = JobMatch(kept_key=kept, folded_key=folded, tier=p.tier,
                          score=p.score, evidence=list(p.evidence))
        if p.auto:
            apply_merge(db, kept, folded)
            record.status, record.decided_by, record.decided_at = "merged", "auto", utcnow()
            merged += 1
        else:
            queued += 1
        db.add(record)
    db.commit()
    return {"merged": merged, "queued": queued}


# --------------------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------------------

def apply_merge(db: Session, kept_key: str, folded_key: str) -> Job:
    """Fold one job into another: sightings repoint, fields fill gaps, the loser gets a tombstone.

    Nothing is destroyed. The folded row keeps its key and its history and gains a pointer, so a
    reference another domain saved before the merge still resolves (`resolve_key`). The merge is
    therefore reversible by clearing one column, which matters the first time it is wrong.
    """
    kept, folded = db.get(Job, kept_key), db.get(Job, folded_key)
    if kept is None or folded is None or kept.job_key == folded.job_key:
        raise ValueError(f"cannot merge {folded_key!r} into {kept_key!r}")

    for s in db.scalars(select(ObservedJob)
                        .where(ObservedJob.canonical_job_key == folded.job_key)).all():
        s.canonical_job_key = kept.job_key
    db.flush()

    # Prefer the value we have over the value we lack; never overwrite a filled field. The one
    # ranked choice is the description — an ATS-sourced JD is fuller than a results-pane scrape.
    for attr in ("salary", "location", "requisition_id", "ats", "canonical_url", "notes", "title"):
        if not getattr(kept, attr, None) and getattr(folded, attr, None):
            setattr(kept, attr, getattr(folded, attr))
    # Company moves as a PAIR — the display name and the normalised key must never disagree, or the
    # job stops being findable by the employer it now claims. Its own branch for that reason, and
    # because the `blank_company` tier exists precisely to merge a row that has no employer: the
    # operator can flip the merge direction, and doing so must not drop the only name we have.
    if not kept.company_norm and folded.company_norm:
        kept.company, kept.company_norm = folded.company, folded.company_norm
    if _description_rank(folded) > _description_rank(kept):
        kept.description, kept.description_source = folded.description, folded.description_source

    # Counts and dates come from the repointed sightings, not from arithmetic on the two rows —
    # the merge has just changed what the truth is, so re-read it. Also self-heals a job whose
    # tallies were already wrong before the merge.
    recount_job(db, kept)
    if kept.status in ("new", "") and folded.status not in ("new", ""):
        kept.status = folded.status

    _merge_applications(db, kept, folded)

    folded.merged_into_key = kept.job_key
    folded.updated_at = utcnow()
    kept.updated_at = utcnow()
    return kept


#: How much to trust a description by where it came from. Anything not listed is a board's own
#: detail pane ('indeed_pane', 'linkedin_pane', …) and ranks 1 — boards truncate and reformat, so
#: the same posting read off the ATS itself always wins.
_DESCRIPTION_RANK = {"ats": 3, "manual": 2}


def pane_source(platform: Optional[str]) -> str:
    """Provenance label for a description read off a board's detail pane.

    Platform-derived rather than the hardcoded 'indeed_pane' it started as: LinkedIn descriptions
    land in the same column, and a corpus that cannot say WHICH board a JD was read from cannot
    later tell you that one of them truncates.
    """
    return f"{platform or 'search'}_pane"


def _description_rank(job: Job) -> int:
    if not (job.description or "").strip():
        return 0
    return _DESCRIPTION_RANK.get(job.description_source or "", 1)


def _merge_applications(db: Session, kept: Job, folded: Job) -> None:
    """Move the folded job's application onto the kept one — or, if both had one, fold the
    timelines together.

    Both sides having an application is exactly the situation the operator hit on Workday: applied
    once through the ATS, again through Indeed, because the two rows looked like two jobs. The
    events are what matter, so they all move onto the surviving application and the derived status
    is recomputed from the union.
    """
    kept_app = db.scalar(select(Application).where(Application.job_key == kept.job_key))
    folded_app = db.scalar(select(Application).where(Application.job_key == folded.job_key))
    if folded_app is None:
        return
    if kept_app is None:
        folded_app.job_key = kept.job_key
        return

    # Repoint the events, then EXPIRE the folded application's loaded collection before deleting
    # it. Both halves matter, and neither is obvious:
    #   * moving them with `folded.events.remove(ev)` marks each one an orphan, and `delete-orphan`
    #     deletes it on flush — appending to the other side does not call that off;
    #   * setting the foreign key alone leaves the events sitting in the stale loaded collection,
    #     so deleting the row cascades into them anyway.
    # Expiring forces a reload that correctly sees the collection as empty, and the delete is
    # then a delete of one row rather than a silent loss of the timeline being rescued.
    for ev in list(folded_app.events):
        ev.application_id = kept_app.id
    db.flush()
    db.expire(folded_app, ["events"])
    applied = [as_aware(d) for d in (kept_app.applied_at, folded_app.applied_at) if d]
    kept_app.applied_at = min(applied) if applied else None
    kept_app.via_platform = kept_app.via_platform or folded_app.via_platform
    kept_app.ats = kept_app.ats or folded_app.ats
    kept_app.notes = "\n".join(n for n in (kept_app.notes, folded_app.notes) if n) or None
    db.delete(folded_app)
    db.flush()
    refresh_application_status(db, kept_app)


# --------------------------------------------------------------------------------------
# Resolving a sighting to its canonical job
# --------------------------------------------------------------------------------------

def resolve_sighting(db: Session, sighting: ObservedJob) -> Job:
    """The canonical job for a scraped card, minting one if this is the first time we have seen it.

    Called after the sighting upsert, never during it: scraping must not block on matching, and a
    sighting whose job cannot be worked out is still worth keeping. Cheap by design — an exact
    key lookup, then one narrow company query — so a whole results page can be resolved per sweep.
    """
    if sighting.canonical_job_key:
        alive = resolve_key(db, sighting.canonical_job_key)
        if alive:
            if alive != sighting.canonical_job_key:
                sighting.canonical_job_key = alive
            return db.get(Job, alive)

    key = mint_job_key(sighting.job_id)
    existing = db.get(Job, key)
    if existing is not None:
        alive_key = resolve_key(db, key) or key
        sighting.canonical_job_key = alive_key
        return db.get(Job, alive_key)

    company_norm = normalize_company(sighting.company)
    reqs = requisition_ids(sighting.url, sighting.tenant_id, sighting.external_id)
    incoming = posting_of(sighting)
    for cand in _company_candidates(db, company_norm, reqs):
        if reqs and (reqs & requisition_ids(cand.requisition_id, cand.canonical_url)):
            return _attach(db, sighting, cand)
        # The SAME rule the auto-merge tier is held to, minus the demand for positive location
        # agreement: a fresh card that scraped no location is unknown, not elsewhere. What this
        # must never again do is attach on employer and title while ignoring the address — that is
        # how Liberty Mutual's Boston and Portsmouth postings became one row with no record of it.
        if conflict_reason(incoming, posting_of(cand)) is None:
            return _attach(db, sighting, cand)

    job = Job(
        job_key=key,
        company=sighting.company or "", company_norm=company_norm,
        title=sighting.title or "", location=sighting.location or "",
        canonical_url=sighting.url or "",
        ats=sighting.application_platform, salary=sighting.salary,
        description=sighting.description,
        description_source=pane_source(sighting.platform) if (sighting.description or "").strip() else None,
        source_platforms=[sighting.platform] if sighting.platform else [],
        first_seen_at=sighting.first_seen_at, last_seen_at=sighting.last_seen_at,
        status="applied" if sighting.application_status == "applied" else "new",
    )
    db.add(job)
    sighting.canonical_job_key = key
    db.flush()
    return recount_job(db, job)


def _company_candidates(db: Session, company_norm: str, reqs: set[str]) -> list[Job]:
    """Live jobs worth comparing a new sighting against: same employer, or sharing a requisition.

    Requisition-bearing rows are pulled in regardless of employer name, because the whole reason
    that tier exists is that the same job wears two different company spellings on two boards.
    """
    clauses = []
    if company_norm:
        clauses.append(Job.company_norm == company_norm)
    if reqs:
        clauses.append(Job.requisition_id.isnot(None))
    if not clauses:
        return []
    return list(db.scalars(select(Job).where(Job.merged_into_key.is_(None), or_(*clauses))).all())


def _attach(db: Session, sighting: ObservedJob, job: Job) -> Job:
    """Point a sighting at an existing job and let the job learn from it."""
    sighting.canonical_job_key = job.job_key
    for src, attr in ((sighting.salary, "salary"), (sighting.location, "location"),
                      (sighting.url, "canonical_url"), (sighting.application_platform, "ats")):
        if src and not getattr(job, attr, None):
            setattr(job, attr, src)
    if (sighting.description or "").strip() and not (job.description or "").strip():
        job.description, job.description_source = sighting.description, pane_source(sighting.platform)
    # Counts, platforms and dates are all re-derived from the sightings — see `recount_job`.
    db.flush()
    return recount_job(db, job)


def split_conflicting_sightings(db: Session, *, dry_run: bool = False) -> list[dict[str, str]]:
    """Detach sightings that positively disagree with the job they were attached to, and re-resolve.

    The repair for what the location-blind attach rule already did. Measured 2026-07-30 on the live
    table: seven jobs held sightings from more than one stated place, and four of those were real
    pairs in real different cities — Liberty Mutual (Boston / Portsmouth), Wellington Management
    (Boston / Needham), Husch Blackwell (Boston / Providence), Northwestern Mutual (Manchester /
    Newington). The other three were the same office written two ways and are left alone by
    `locations_conflict`, which is the whole reason that predicate is not `!=`.

    Not a merge and so not a tombstone: these rows were never two jobs that got folded together,
    they were one job a sighting should never have joined. Un-attaching restores a state that was
    always the truth, and re-resolution mints the key the sighting would have had.

    **It never reverses a decision anybody recorded.** A sighting whose own minted job row exists
    and carries a tombstone got where it is through a `JobMatch` — and the first version of this
    function duly offered to undo DEKA Research's blank-company merge, which the operator had
    approved by hand. What this repairs is the attachments that were never decisions at all: no
    `JobMatch`, no evidence, no row, nothing to review. A repair that can overwrite a human's answer
    is not a repair.

    Idempotent — after a pass nothing conflicts, so a second pass is a no-op.
    """
    out: list[dict[str, str]] = []
    jobs = list(db.scalars(select(Job).where(Job.merged_into_key.is_(None))).all())
    for job in jobs:
        sightings = list(db.scalars(select(ObservedJob)
                                    .where(ObservedJob.canonical_job_key == job.job_key)).all())
        if len(sightings) < 2:
            continue
        job_reqs = requisition_ids(job.requisition_id, job.canonical_url)
        # The job row is the anchor, except when its own location never got filled — then the
        # earliest sighting speaks for it, so a group does not fail to split merely because the
        # first card through the door scraped a blank cell.
        anchor = posting_of(job)
        if not normalize_location(anchor.location):
            earliest = min(sightings, key=lambda s: as_aware(s.first_seen_at))
            anchor = Posting(anchor.company_norm, anchor.title, earliest.location or "",
                             anchor.salary, anchor.platforms)
        for s in sightings:
            reason = field_conflict(anchor, posting_of(s))
            if reason is None:
                continue
            own = db.get(Job, mint_job_key(s.job_id))
            if own is not None and own.merged_into_key:
                continue                      # a recorded merge decision — not ours to undo
            if job_reqs and (job_reqs & requisition_ids(s.url, s.tenant_id, s.external_id)):
                continue                      # attached on a shared requisition, which outranks a field
            out.append({"job_key": job.job_key, "sighting": s.job_id, "reason": reason,
                        "title": job.title, "company": job.company})
            if not dry_run:
                s.canonical_job_key = None
        if not dry_run:
            db.flush()
            recount_job(db, job)
    if dry_run:
        return out
    db.flush()
    resolve_pending(db)
    db.commit()
    return out


def sync_description(db: Session, sighting: ObservedJob) -> Optional[Job]:
    """Push a sighting's freshly-read description (and salary / apply route) up to its job.

    The missing half of description capture, and the reason coverage looked stuck. Sightings are
    resolved to a canonical `Job` once, when they are first scraped — at which point most of them
    have no description, because the JD is read later by clicking INTO the card. Nothing then
    carried that text upward, so the board's own table filled up while the canonical table the
    dashboard reads stayed empty.

    Platform-agnostic on purpose: Indeed and LinkedIn both write their JD onto a sighting and both
    arrive here. The only thing the platform decides is the provenance label (`pane_source`), which
    is what later lets us ask which board truncates.

    Does not commit — the caller owns the transaction, same contract as the upsert.
    """
    if not sighting.canonical_job_key:
        return None
    alive = resolve_key(db, sighting.canonical_job_key)
    job = db.get(Job, alive) if alive else None
    if job is None:
        return None

    text = (sighting.description or "").strip()
    if text:
        incoming = _DESCRIPTION_RANK.get(pane_source(sighting.platform), 1)
        # A longer read of the same posting is a better read: the pane sometimes truncates on the
        # first click and fills in on a later one.
        if incoming > _description_rank(job) or (
                incoming == _description_rank(job) and len(text) > len((job.description or "").strip())):
            job.description = sighting.description
            job.description_source = pane_source(sighting.platform)
    if sighting.salary and not job.salary:
        job.salary = sighting.salary
    if sighting.application_platform and not job.ats:
        job.ats = sighting.application_platform
    return job


def resolve_pending(db: Session, *, batch: int = 1000) -> int:
    """Give every unresolved sighting a canonical job, WITHOUT committing.

    The no-commit half exists so a sweep can call this at the end of its own upsert and keep
    committing once per page, which is the transaction boundary that made the multi-page sweep
    safe in the first place. Idempotent: an already-resolved sighting is skipped.
    """
    rows = list(db.scalars(
        select(ObservedJob).where(ObservedJob.canonical_job_key.is_(None))
        .order_by(ObservedJob.first_seen_at).limit(batch)).all())
    for s in rows:
        resolve_sighting(db, s)
        db.flush()
    return len(rows)


def resolve_all(db: Session, *, batch: int = 1000) -> int:
    """`resolve_pending`, committed. For routes and one-off backfills."""
    count = resolve_pending(db, batch=batch)
    db.commit()
    return count


def resolve_after_commit(db: Session, *, batch: int = 1000) -> int:
    """Resolve newly-written sightings, and never take a successful scrape down with it.

    Called by the scrape endpoints AFTER their own commit, so the sightings are already durable
    before this runs. That ordering is what makes swallowing the error the right call rather than
    a shrug: canonical rows are derived data, nothing is lost by rebuilding them a minute later
    from `/api/career_search/reindex`, and a live drive against a real account is far too
    expensive to throw away because an index pass tripped.
    """
    try:
        return resolve_all(db, batch=batch)
    except Exception:
        db.rollback()
        return 0


def backfill(db: Session) -> dict[str, int]:
    """Bring the canonical tables up to date with everything already in `observed_jobs`.

    The order is load-bearing: a sighting must have a job before it can have an application, the
    split must run after resolution so it sees every attachment, and duplicates must be found after
    every job exists or the scan only sees half the corpus. Fully idempotent, so it is safe to
    re-run after any sweep.
    """
    resolved = resolve_all(db)
    split = split_conflicting_sightings(db)
    recount_all(db)
    descriptions = sync_all_descriptions(db)
    applications = _backfill_applications(db)
    dedup = scan_and_record(db)
    return {"sightings_resolved": resolved, "sightings_split": len(split),
            "descriptions_synced": descriptions,
            "applications_created": applications, **dedup}


def sync_all_descriptions(db: Session) -> int:
    """Carry every sighting's description up to its canonical job. Idempotent.

    The catch-up for descriptions read before the canonical table existed, and the repair for any
    that slipped past the live sync.
    """
    synced = 0
    rows = db.scalars(select(ObservedJob).where(
        ObservedJob.canonical_job_key.isnot(None),
        func.coalesce(ObservedJob.description, "") != "")).all()
    for s in rows:
        if sync_description(db, s) is not None:
            synced += 1
    db.commit()
    return synced


def _backfill_applications(db: Session) -> int:
    """Mint an `Application` for every sighting already marked applied.

    The historical rows carry a status and a date and nothing else — no timeline, because until
    now there was nowhere to put one. Each becomes an application with a single `applied` event
    sourced to `agent`, which is honest about where the fact came from: the apply epilogue stamped
    it, not the operator. Everything after that first event is what the operator (and later Gmail)
    will add.
    """
    from application_events import ensure_application

    created = 0
    rows = db.scalars(select(ObservedJob).where(
        or_(ObservedJob.application_status.in_(("applied", "submitted")),
            ObservedJob.applied_at.isnot(None)))).all()
    for s in rows:
        key = resolve_key(db, s.canonical_job_key) if s.canonical_job_key else None
        if not key:
            continue
        if db.scalar(select(Application).where(Application.job_key == key)):
            continue
        ensure_application(db, key, applied_at=s.applied_at or s.last_seen_at,
                           via_platform=s.platform, ats=s.application_platform)
        job = db.get(Job, key)
        if job is not None and job.status == "new":
            job.status = "applied"
        created += 1
    db.commit()
    return created


# --------------------------------------------------------------------------------------
# Applications (imported here to keep the merge path self-contained)
# --------------------------------------------------------------------------------------

def refresh_application_status(db: Session, app: Application) -> Application:
    """Recompute the derived status from the event timeline. Defined in `application_events`;
    re-exported so the merge path does not import in a circle."""
    from application_events import reduce_status

    events = list(db.scalars(select(ApplicationEvent)
                             .where(ApplicationEvent.application_id == app.id)
                             .order_by(ApplicationEvent.occurred_at)).all())
    app.status = reduce_status(events)
    app.last_event_at = events[-1].occurred_at if events else app.applied_at
    return app


def summarize(items: Iterable[Any]) -> dict[str, int]:
    """Count by value — used by the routes for the small rollups the dashboard shows."""
    out: dict[str, int] = {}
    for it in items:
        key = str(it or "unknown")
        out[key] = out.get(key, 0) + 1
    return out
