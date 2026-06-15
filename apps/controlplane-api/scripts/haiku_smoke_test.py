#!/usr/bin/env python
"""One real Claude Haiku 4.5 call to confirm ANTHROPIC_API_KEY works.

This is the connectivity gate for the locked AX + Haiku grounding method:
run it once after pasting your key into apps/controlplane-api/.env. Green =
the key + SDK + model id are all good and we can build the SoM picker on top.

Usage (from repo root, with the project's .venv):
    .venv/bin/python apps/controlplane-api/scripts/haiku_smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

MODEL = "claude-haiku-4-5"  # do NOT append a date suffix to this id
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_env_file(path: Path) -> None:
    """Minimal .env loader so this runs without depending on python-dotenv."""
    import os

    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def main() -> int:
    import os

    _load_env_file(ENV_FILE)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("✗ ANTHROPIC_API_KEY is empty.")
        print(f"  Paste your key into {ENV_FILE} (the ANTHROPIC_API_KEY= line),")
        print("  or export it in your shell, then re-run this script.")
        return 1
    if not key.startswith("sk-ant-"):
        print("⚠ Key does not start with 'sk-ant-' — double-check you copied the whole key.")

    try:
        import anthropic
    except ImportError:
        print("✗ The 'anthropic' SDK is not installed in this interpreter.")
        print("  Run with the project venv: .venv/bin/python <this script>")
        return 1

    # Budget guard: every autonomous Claude call must pass the weekly cap first.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import anthropic_usage

        b = anthropic_usage.enforce_budget()
        print(f"  budget ok — ${b['spent_usd']:.4f} / ${b['limit_usd']:.2f} used this week")
    except Exception as e:
        # BudgetExceededError (or import issue) — in the real loop this escalates to a human.
        print(f"✗ {e}")
        return 1

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with exactly the word: pong"}],
        )
    except anthropic.AuthenticationError:
        print("✗ Authentication failed — the key was rejected. Is it active / not revoked?")
        return 1
    except anthropic.APIStatusError as e:
        print(f"✗ API error (status {e.status_code}): {e.message}")
        if e.status_code == 400 and "credit" in str(e.message).lower():
            print("  This usually means the account/workspace has no billing/credit set up yet.")
        return 1
    except Exception as e:  # noqa: BLE001 - surface anything unexpected cleanly
        print(f"✗ Unexpected error: {type(e).__name__}: {e}")
        return 1

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    print(f"✓ Haiku ({MODEL}) responded: {text!r}")
    print(f"  tokens — in: {resp.usage.input_tokens}, out: {resp.usage.output_tokens}")

    # Log this call so it shows up in the in-app API Usage panel (first data point).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import anthropic_usage

        row = anthropic_usage.record_from_response(resp, purpose="smoke_test")
        print(f"  logged to usage panel — cost ${row['cost_usd']:.6f} (purpose=smoke_test)")
    except Exception as e:  # noqa: BLE001
        print(f"  (usage logging skipped: {e})")

    print("  Key works. Ready to build the AX + Haiku SoM picker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
