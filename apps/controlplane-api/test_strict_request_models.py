"""A write endpoint must REJECT a key it doesn't know, not quietly drop it.

Regression for 2026-07-28: `PATCH /api/observations/{filename}` was sent
`{"page_state": "successfactors_create_account"}` — the real field is `observed_page_state` —
and answered `{"ok": true}` while writing nothing. The label was missing from the corpus and it
was caught only by querying the TrainingCapture row afterwards. On a labeling path a silent
no-op is worse than an error: the corpus quietly doesn't grow, and the `ok` is exactly the
evidence someone would cite that it did.
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient
from pydantic import BaseModel

import main
from schemas import StrictModel


client = TestClient(main.app)


def test_unknown_label_key_is_rejected_not_ignored():
    """The exact live call that lied: `page_state` instead of `observed_page_state`."""
    response = client.patch(
        "/api/observations/does-not-matter.json",
        json={"page_state": "successfactors_create_account"},
    )

    # 422 BEFORE the 404 — the body never validates, so the handler never runs.
    assert response.status_code == 422, response.text
    # And the error has to NAME the offending key, or it's just a different kind of dead end.
    assert "page_state" in response.text


def test_known_label_key_still_validates():
    """The forbid must not cost us the real field: `observed_page_state` gets past validation.

    Reaches the handler (404 on a filename with no trace), which is proof enough that the
    body itself was accepted.
    """
    response = client.patch(
        "/api/observations/does-not-matter.json",
        json={"observed_page_state": "successfactors_create_account"},
    )

    assert response.status_code == 404, response.text


def test_every_request_model_in_main_forbids_extras():
    """Structural guard: a NEW endpoint can't reintroduce the hole by subclassing BaseModel.

    Scoped to models defined in `main.py` — the write models in `schemas.py` (DomainWrite,
    GoalUpdate, TaskWrite, ScenarioUpdate, TrainingSessionCreate, ...) are the same exposure
    and are NOT covered yet; converting them means re-verifying their UI callers first.
    """
    offenders = []
    for name, obj in vars(main).items():
        if not inspect.isclass(obj) or not issubclass(obj, BaseModel):
            continue
        if obj.__module__ != "main":  # imported read/response schemas aren't ours to police
            continue
        if not issubclass(obj, StrictModel) or obj.model_config.get("extra") != "forbid":
            offenders.append(name)

    assert not offenders, f"request models silently drop unknown keys: {sorted(offenders)}"
