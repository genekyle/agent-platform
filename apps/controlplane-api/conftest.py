"""Test isolation for the append-only corpora.

Found 2026-07-20: the suite had been writing into the LIVE journals. `decision_journal.jsonl`
went from 45 real rows to 282 in a single working session, because `run_controller` journals by
design and nothing redirected it — so ~84% of the "corpus" was fixture traffic on fake routes
(`smartapply.indeed.com/x`). Every measurement taken off that file was wrong, including the
mined-taxonomy counts that motivated the supervisor.

`INTERACTION_ARTIFACTS_DIR` already existed as the override; there was simply no conftest to set
it. This is that conftest. It is session-scoped and autouse, so no test can opt out by forgetting.

**And again on 2026-08-04, with the StepRunner** — the same bug, one directory over. Every
transition row in `output/transitions/session_1.jsonl` (45 of them) was fixture traffic: tab ids
`t0`/`t1`, `ax_count: 0`, beliefs at uncertainty 1.0 over a page no browser ever served. A
session's worth of analysis was spent diagnosing "the live AX scans ran dry" against a corpus
that had never seen a live drive. The lesson generalises past both incidents: **a NEW append-only
writer inherits nothing from this file — it must be routed here by hand, or it writes to
production the first time a test touches it.** The transitions corpus lives under
`settings.observer_artifacts_dir`, which this now redirects too.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_interaction_artifacts():
    """Point every journal/program write at a throwaway directory for the whole test session.

    Set BEFORE any test imports resolve a path, and restored after, so a developer running the
    suite in a shell that already had the variable set keeps their value.
    """
    previous = os.environ.get("INTERACTION_ARTIFACTS_DIR")
    with tempfile.TemporaryDirectory(prefix="interaction-test-artifacts-") as tmp:
        os.environ["INTERACTION_ARTIFACTS_DIR"] = tmp
        try:
            yield tmp
        finally:
            if previous is None:
                os.environ.pop("INTERACTION_ARTIFACTS_DIR", None)
            else:
                os.environ["INTERACTION_ARTIFACTS_DIR"] = previous


@pytest.fixture(scope="session", autouse=True)
def _isolate_observer_artifacts():
    """The same protection for `observer_artifacts_dir` — captures, screenshots, and the
    StepRunner's TRANSITION CORPUS all hang off it.

    The env var alone is not enough: `settings` is a module-level singleton constructed at import
    time, and pytest imports the app long before a session fixture runs. So the ATTRIBUTE is
    reassigned as well, which is what `step_runner._transitions_dir()` actually reads (it resolves
    the path per call, by design, so this takes effect immediately).
    """
    from settings import settings

    previous_env = os.environ.get("OBSERVER_ARTIFACTS_DIR")
    previous_value = settings.observer_artifacts_dir
    with tempfile.TemporaryDirectory(prefix="observer-test-artifacts-") as tmp:
        os.environ["OBSERVER_ARTIFACTS_DIR"] = tmp
        settings.observer_artifacts_dir = tmp
        try:
            yield tmp
        finally:
            settings.observer_artifacts_dir = previous_value
            if previous_env is None:
                os.environ.pop("OBSERVER_ARTIFACTS_DIR", None)
            else:
                os.environ["OBSERVER_ARTIFACTS_DIR"] = previous_env
