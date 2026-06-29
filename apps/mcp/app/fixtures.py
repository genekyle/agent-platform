import json
from pathlib import Path
from typing import Any


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OBSERVER_FIXTURES_DIR = FIXTURES_DIR / "observer"
SOURCE_FIXTURES_DIR = FIXTURES_DIR / "sources"
GOLDEN_FIXTURES_DIR = FIXTURES_DIR / "golden"


def load_observer_fixture(name: str) -> dict[str, Any]:
    path = OBSERVER_FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_source_fixture(name: str) -> dict[str, Any]:
    path = SOURCE_FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_golden_fixture(name: str) -> dict[str, Any]:
    path = GOLDEN_FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_observer_fixtures() -> list[str]:
    return sorted(path.stem for path in OBSERVER_FIXTURES_DIR.glob("*.json"))
