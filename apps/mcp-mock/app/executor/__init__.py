"""The ACT stage — executes a selected intent+target in the live browser.

The selector (controlplane-api/select_stage) decides WHAT to do (action + target
backend_node_id + bbox); this package decides HOW the pointer gets there. The
selector never generates pointer paths — drivers do. Pluggable via TrajectoryDriver:
  - DirectDriver        center-click, no synthesized path (default)
  - MinimumJerkDriver   smooth 5th-order path before the click (Phase 6, flag off)
  - RecordOnlyDriver    logs the intended action for human supervision / corpus
Swapping drivers never changes the selector contract.
"""
