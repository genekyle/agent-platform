# PLAN — the StepRunner: no rung marks itself complete

Adopted 2026-08-03, operator-authored. This supersedes the per-rung self-reporting shape wherever
the two disagree. The diagnosis it answers, in the operator's words: *"our process rested solely on
the recipe and every action counted as a positive flag that would then move us onto the next step
without confirming or verifying what has actually happened… having no observer or reasoner cuts the
eyes and the brain off from the whole system, which is our root product."*

The finding that triggered it (LEARNINGS 2026-07-30 (6) and (7)): the perception stack existed —
two calibrated witnesses, a live seam, a transition trainer — and the ladder path the operator
actually drives never called any of it. The work is connection, not construction.

## 1. One mandatory StepRunner

Every action passes through:

    observe before
    → classify current state
    → choose action
    → define expected result
    → execute
    → observe after
    → verify
    → commit, recover, or escalate

**A rung cannot mark itself complete.** The rung's own outcome is a *claim*; the verifier's reading
of the world is what settles it.

## 2. Local observation stack — cheapest evidence first

    URL + DOM + accessibility tree
    → deterministic diff
    → Apple Vision visual witness
    → remote multimodal teacher when uncertain

Apple Vision is a **visual witness and candidate scorer, not the system's main reasoner**. It
matters more when: DOM/AX are incomplete; the interface is canvas-based or highly visual; multiple
similar candidates exist; the page changed visually but not structurally; the DOM and visual
witnesses disagree. It is in the observation schema from day one, in shadow mode — capturing
embeddings, visually-changed regions, similarity to known states, candidate crops linked to DOM/AX
nodes, visual novelty/confidence, and before/after visual agreement — so the corpus trains future
state classifiers, target rankers and verifiers rather than being a folder of unlabeled images.

## 3. The transition corpus — the core training row

Each step stores: before state · evidence · selected action · expected postconditions · after
state · actual changes · verification result · teacher correction (when needed). Not the screenshot
alone, and not a giant reasoning transcript.

## 4. Verification policy

When the rung and the evidence disagree: do **not** complete the rung; reobserve; retry or select a
recovery action; ask the remote teacher when ambiguous. **Hard-stop only before irreversible
actions.**

## 5. Training order (narrow components first)

1. Current-state classifier
2. Action-result verifier
3. Target ranker
4. Recovery selector
5. Next-action policy

## Build order

StepRunner first, wired into the path the operator actually drives (the apply ladder), with the
Apple Vision fields present-but-shadow so DOM/AX verification handles the obvious cases while the
visual corpus accumulates. The existing pieces are reused, not rebuilt: `perception/live.sense` is
the belief, `/capture` is the artifact writer, `state_transition.py` is the trainer the corpus
feeds, and `next_rung`'s latest-verdict-wins rule is the retry loop.
