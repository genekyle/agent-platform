"""The Precedent Engine — the in-house decision layer (PLAN_inhouse_reasoner_v1 M0).

Every journaled decision and transition half becomes a vector; a decision is made by
retrieving the nearest precedents and letting them vote. This package owns:

- `embedder`  — the late-fusion embedding recipe (Apple Vision + NLEmbedding + hashed facets),
  all on-device, $0, no downloads.
- `store`     — the sqlite-vec vector store beside the corpus it indexes.
- `backfill`  — one-shot CLI: embed the historical corpus into the store.
- `evaluate`  — leave-one-session-out replay: the honest "are the educated guesses good" number.

The engine answers WHAT (intent) and WHERE-symbolically (role/name ref); grounding to a node
stays with the select stage and the AX layer (PRINCIPLES §6, §8 unchanged).
"""
