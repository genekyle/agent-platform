"""Which INSTANCE of an ATS is this? — the axis a hostname cannot carry.

`ats_registry.classify_ats` answers *which vendor*. It cannot answer *which employer's tenant*,
and on 2026-08-20 a pass over the transition corpus showed why that matters: **every vendor
encodes tenancy differently**, so counting instances by hostname silently undercounts half of them.

    workday        cswg.wd1.myworkdayjobs.com        /CS_Careers/job/...      SUBDOMAIN
    peopleadmin    une.peopleadmin.com               /postings/26341          SUBDOMAIN
    icims          careers-odysseyconsult.icims.com  /jobs/8308/...           SUBDOMAIN
    cornerstone    bc.csod.com                       /ux/ats/careersite/...   SUBDOMAIN
    paylocity      recruiting.paylocity.com          /Recruiting/Jobs/Details/4382310/Employer-Slug   PATH
    brassring      sjobs.brassring.com               ?partnerid=..&siteid=..  QUERY
    linkedin       www.linkedin.com                  —                        NONE

We had driven Paylocity for TWO employers and the host axis reported one instance. That is the
whole bug this module exists to fix.

MEASURED vs ASSUMED is marked per rule, because the registry's own standard is that a promise the
ladder acts on must come from a measurement. A rule marked `assumed` is a starting guess from the
vendor's documented URL shape and should be re-marked the first time a real drive confirms it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

SUBDOMAIN, PATH_INDEX, PATH_REGEX, QUERY_PARAM, HOSTNAME, NONE = (
    "subdomain", "path_index", "path_regex", "query_param", "hostname", "none")


@dataclass(frozen=True)
class TenantRule:
    """How to pull the employer's tenant slug out of one vendor's URLs."""
    style: str
    #: subdomain: labels to drop from the right before taking the first (e.g. wd1.myworkdayjobs.com)
    spec: Any = None
    measured: bool = False
    why: str = ""


#: Keyed by `ats_id`. Absent vendor -> `None`, and the caller falls back to the hostname, which is
#: correct for a single-tenant host and honest about being a fallback.
RULES: dict[str, TenantRule] = {
    "workday": TenantRule(SUBDOMAIN, None, measured=True,
                          why="cswg / solutionhealth / eversource .wd1.myworkdayjobs.com, corpus 25-32"),
    "peopleadmin": TenantRule(SUBDOMAIN, None, measured=True, why="une.peopleadmin.com, session 32"),
    "icims": TenantRule(SUBDOMAIN, None, measured=True,
                        why="careers-odysseyconsult.icims.com and jobs-joslin.icims.com"),
    "cornerstone": TenantRule(SUBDOMAIN, None, measured=True, why="bc.csod.com, Boston College"),
    "successfactors": TenantRule(QUERY_PARAM, "company", measured=True,
                                 why="career5.successfactors.eu is shared; ?company= names the tenant"),
    "paylocity": TenantRule(
        PATH_REGEX, r"/Recruiting/Jobs/(?:Details|Apply)/\d+/([^/?#]+)", measured=True,
        why="recruiting.paylocity.com is shared; the employer slug follows the requisition id. "
            "NOTE the Apply URL often omits the slug, so a flow can see the tenant on one screen "
            "and not the next — resolve it once at the posting and carry it."),
    "brassring": TenantRule(QUERY_PARAM, "partnerid", measured=True,
                            why="sjobs.brassring.com is shared; partnerid+siteid identify the tenant"),
    "greenhouse": TenantRule(PATH_INDEX, 0, measured=False, why="boards.greenhouse.io/<tenant>"),
    "lever": TenantRule(PATH_INDEX, 0, measured=False, why="jobs.lever.co/<tenant>"),
    "smartrecruiters": TenantRule(PATH_INDEX, 0, measured=False, why="smartrecruiters.com/<tenant>"),
    "ashby": TenantRule(PATH_INDEX, 0, measured=False, why="jobs.ashbyhq.com/<tenant>"),
    "workable": TenantRule(SUBDOMAIN, None, measured=False, why="<tenant>.workable.com"),
    "taleo": TenantRule(SUBDOMAIN, None, measured=False, why="<tenant>.taleo.net"),
    "jobvite": TenantRule(PATH_INDEX, 0, measured=False, why="jobs.jobvite.com/<tenant>"),
    "adp": TenantRule(NONE, None, measured=False, why="workforcenow.adp.com is shared; tenant unmeasured"),
    "phenom": TenantRule(SUBDOMAIN, None, measured=False,
                         why="employers run it on their OWN careers subdomain — the host IS the tenant"),
    "appvault": TenantRule(SUBDOMAIN, None, measured=True, why="aholddelhaizeapply.appvault.com"),
    "indeed_quick_apply": TenantRule(NONE, None, measured=True, why="one Indeed; the employer is the job"),
    "linkedin_easy_apply": TenantRule(NONE, None, measured=True, why="one LinkedIn"),
    # An employer's own careers site: the host is the employer, definitionally.
    # HOSTNAME, not SUBDOMAIN: `careers.solutionhealth.org` is SolutionHealth, and taking the
    # leading label would have made the tenant "careers" — which every employer shares.
    "company_site": TenantRule(HOSTNAME, None, measured=True,
                               why="no vendor host to strip — the whole hostname identifies the employer"),
}

#: Suffixes to strip before taking the leading label, so `cswg.wd1.myworkdayjobs.com` -> `cswg`
#: rather than the bare first label of a two-label host.
_KNOWN_SUFFIX_DEPTH = {
    "myworkdayjobs.com": 3,   # <tenant>.wd1.myworkdayjobs.com
    "myworkday.com": 3,
}


def _subdomain(host: str) -> str:
    host = (host or "").lower().strip(".")
    if not host:
        return ""
    labels = host.split(".")
    for suffix, depth in _KNOWN_SUFFIX_DEPTH.items():
        if host.endswith(suffix) and len(labels) >= depth:
            return labels[0]
    # A bare registrable domain (`indeed.com`) has no tenant label to take.
    return labels[0] if len(labels) > 2 else ""


def tenant_of(url: str, ats_id: str) -> tuple[str, str]:
    """(tenant, how) for one URL — `how` names the rule so a wrong answer is traceable.

    Returns `("", "no-url")` rather than guessing when there is nothing to read. An empty tenant is
    a legitimate answer for a single-tenant vendor and must not be papered over with the hostname.
    """
    if not url:
        return "", "no-url"
    p = urlparse(url)
    host = (p.hostname or "").lower()
    rule = RULES.get(ats_id)
    if rule is None:
        return host, "fallback:hostname"
    if rule.style == NONE:
        return "", "none"
    if rule.style == HOSTNAME:
        return host, "hostname"
    if rule.style == SUBDOMAIN:
        return _subdomain(host), "subdomain"
    if rule.style == PATH_INDEX:
        segs = [s for s in p.path.split("/") if s]
        idx = int(rule.spec or 0)
        return (segs[idx].lower() if len(segs) > idx else ""), f"path[{idx}]"
    if rule.style == PATH_REGEX:
        hit = re.search(str(rule.spec), p.path or "", re.I)
        return (hit.group(1).lower() if hit else ""), "path_regex"
    if rule.style == QUERY_PARAM:
        vals = parse_qs(p.query or "").get(str(rule.spec)) or []
        return (vals[0].lower() if vals else ""), f"query[{rule.spec}]"
    return "", f"unknown-style:{rule.style}"


def instance_key(ats_id: str, tenant: str) -> str:
    """The stable id for one ATS instance. Single-tenant vendors collapse to the vendor itself."""
    ats_id = (ats_id or "unknown").strip().lower()
    tenant = (tenant or "").strip().lower()
    return f"{ats_id}:{tenant}" if tenant else ats_id
