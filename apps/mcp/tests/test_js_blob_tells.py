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
