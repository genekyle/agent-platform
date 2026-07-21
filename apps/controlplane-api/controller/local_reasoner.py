"""The local student's seat — a `DecisionReasoner` backed by a small model served on localhost.

This is the piece PRINCIPLES §9 has been holding open: the STUDENT rung, above Haiku-the-backstop.

**THE SEAT IS EMPTY. No model tested to date can sit in it on this hardware** (measured
2026-07-20 — see LEARNINGS). Two candidates were tried and both failed, for different reasons:

  * **Gemma 4 E2B** — does not fit. "2.3B effective parameters" is a COMPUTE figure; all 5.1B
    total params must be resident, so the Q4 build is **7.2 GB**, not the ~2 GB the spec sheets
    imply (E4B is 9.6 GB). On this 8 GB machine it took **50 s to emit one word** and grew the
    swapfile from 8 GB to 14.3 GB. Deleted.
  * **llama3.2:1b** — fits (1.3 GB) and is fast (~1.7 s/decision), and scores **0/4** on real
    bundle shapes: it collapses to `click` for everything and invents application answers.

Keep this module anyway. It is model-agnostic and fully tested against a fake transport, so the
day there is either hardware or a fine-tuned adapter, the seat is wired. `LocalReasoner(model=…)`
takes any tag. What it must NOT do is get pointed at a live drive on the strength of the default
below — which is why `student` is in `PROPOSE_RUNGS` from day one.

Three deliberate choices, each of which matters more than the model:

1. **The prompt surface is IDENTICAL to Haiku's.** `build_messages` and `_SYSTEM` are imported
   from `reason.py`, not re-written. `bundle_to_prompt` is the frozen feature contract, so a
   shadow comparison between this rung and Haiku is like-for-like, and every row journaled from
   either one trains the same distilled policy later. A "helpful" prompt tweak here would silently
   fork the corpus.

2. **Decoding is CONSTRAINED, not merely instructed.** `DECISION_JSON_SCHEMA` (already written for
   Haiku's structured output) is handed to the server as a decoding grammar, so the model is
   *structurally incapable* of emitting an intent outside the closed vocabulary or a key outside
   the closed param set. On a 2B model this is the single largest quality lever available, and it
   costs nothing. It does NOT replace `parse_decision` — the parser still runs, because a grammar
   constrains shape, not sense (it cannot stop a selector-shaped *value*, or a confidence of 1.0
   on a guess).

3. **$0, and no budget gate.** The whole point of the student is that it does not spend. The
   `last_cost_usd` attribute exists only so this is interchangeable with `HaikuReasoner` at the
   call site; it is always 0.0.

Failure is a journaled escalation, never a raise — same contract as every other reasoner. A local
server that is down, slow, or serving a model that ignores the grammar all degrade into "escalate
and say why", which the ladder already knows how to handle.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from controller.reason import (
    DECISION_JSON_SCHEMA,
    build_messages,
    parse_decision,
    reject_decision,
)
from interaction.decision import Bundle, Decision

logger = logging.getLogger("controller.local_reasoner")

#: The default local server. Ollama's OpenAI-ish chat route; llama.cpp's server speaks a
#: compatible-enough shape that swapping runtimes is a base-url change.
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

#: The last model MEASURED, not an endorsement — it scores 0/4 (see the module docstring). It is
#: the default so that re-running the baseline is one command; it is emphatically not a model to
#: point at a live drive. A tag, not a hardcoded architecture: a fine-tuned adapter or a hosted
#: E4B is a config change, not a code change.
DEFAULT_MODEL = "llama3.2:1b"

#: Context window to request. The Bundle prompt runs ~1–2k tokens; 8k leaves room for the lessons
#: block and a few-shot tail without paying for the model's full 256k (KV cache is RAM we do not
#: have). Raise this only with a measured reason.
DEFAULT_NUM_CTX = 8192

#: A (path, payload) -> response-dict callable — the one seam that makes this testable with no
#: model and no network, exactly like `LiveActuator.Transport`.
Transport = Callable[[str, dict], dict]


def _httpx_transport(base_url: str, *, timeout: float) -> Transport:
    """The live transport. Blocking POST — `decide()` is sync and runs in a threadpool."""
    import httpx

    base = base_url.rstrip("/")

    def _post(path: str, payload: dict) -> dict:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{base}{path}", json=payload)
            r.raise_for_status()
            return r.json() or {}

    return _post


def _flatten(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Anthropic content-block messages -> the flat {role, content} shape local servers expect.

    `build_messages` returns Anthropic's nested form because that is what the Haiku rung sends.
    Flattening HERE (rather than forking `build_messages`) is what keeps the two rungs reading
    the exact same bytes of `bundle_to_prompt`.
    """
    out: list[dict[str, str]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        else:
            text = str(content or "")
        out.append({"role": str(m.get("role", "user")), "content": text})
    return out


class LocalReasoner:
    """A DecisionReasoner served from localhost. Returns a Decision always — an escalation
    (with the reason) on transport/parse failure, never a raise and never a silent guess."""

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 timeout: float = 120.0, num_ctx: int = DEFAULT_NUM_CTX,
                 transport: Optional[Transport] = None) -> None:
        self.base_url = base_url
        self.model = model
        self.num_ctx = num_ctx
        self._post: Transport = transport or _httpx_transport(base_url, timeout=timeout)
        #: Always 0.0 — kept only for interface parity with HaikuReasoner at the call site.
        self.last_cost_usd = 0.0

    def __call__(self, bundle: Bundle) -> Decision:
        system, messages = build_messages(bundle)
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *_flatten(messages)],
            # The grammar. This is the lever: the closed Intent enum and the closed param key set
            # are enforced during DECODING, so an off-vocabulary intent is not merely rejected
            # after the fact — it cannot be generated.
            "format": DECISION_JSON_SCHEMA,
            "stream": False,
            # temperature 0: this is a policy, not a writer. Determinism also makes the shadow
            # comparison meaningful — a disagreement is a real disagreement, not a sample.
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }

        try:
            data = self._post("/api/chat", payload)
        except Exception as exc:  # noqa: BLE001 — a dead server is a handoff, not a crash
            logger.warning("local reasoner transport failed: %s", exc)
            return reject_decision(
                f"local model at {self.base_url} unreachable: {type(exc).__name__}: {exc}", bundle)

        text = ((data.get("message") or {}).get("content") or "").strip()
        if not text:
            return reject_decision("local model returned an empty message", bundle)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # With the grammar applied this should be unreachable; if it fires, the server ignored
            # `format` (wrong runtime version, or a model that does not support constrained
            # decoding) — which is worth saying plainly rather than papering over.
            return reject_decision(
                f"local model output was not JSON despite the grammar — is constrained decoding "
                f"supported by this runtime? ({exc})", bundle)

        decision = parse_decision(parsed, bundle)
        # `parse_decision` stamps rung="model" (it is shared with Haiku). Re-stamp so the corpus
        # can tell the student's rows from the backstop's — otherwise shadow agreement is
        # comparing a rung against itself.
        return Decision(
            intent=decision.intent, params=decision.params, confidence=decision.confidence,
            rung="student", rationale=decision.rationale, evidence=decision.evidence,
            expected_next=decision.expected_next, escalate=decision.escalate,
        )
