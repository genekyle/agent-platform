"""Test isolation for the append-only corpora.

Found 2026-07-20: the suite had been writing into the LIVE journals. `decision_journal.jsonl`
went from 45 real rows to 282 in a single working session, because `run_controller` journals by
design and nothing redirected it — so ~84% of the "corpus" was fixture traffic on fake routes
(`smartapply.indeed.com/x`). Every measurement taken off that file was wrong, including the
mined-taxonomy counts that motivated the supervisor.

`INTERACTION_ARTIFACTS_DIR` already existed as the override; there was simply no conftest to set
it. This is that conftest. It is session-scoped and autouse, so no test can opt out by forgetting.
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
