"""The controller — the teachable `decide()` in observe() -> decide() -> act().

This package is the running implementation of `docs/PLAN_controller_v1.md`: a cost-ordered
cascade (recipe/program -> model -> teacher -> human) that reads a frozen `Bundle` and emits
a frozen `Decision`, shaped exactly like `resolve_answer`'s cascade one altitude up. It is
Career Search only until a scenario family graduates.

Modules, in build order:
  - bundle    (M1) compose the observation surfaces into a Bundle — PURE, no IO
  - programs  (M2) compile verified decision sequences into replayable intent programs
  - decide    (M2/M3) the cascade: rung 0 (program) -> rung 1 (model) -> escalate
  - loop      (M2) the thin harness: observe -> decide -> act (Interaction API) -> verify
  - unexpected      the "we are not where we assumed" policy — re-observe once, then escalate;
                    shared with the login drive so both decide staleness identically
  - teach     (M4) propose-approve — corrections on the controller's own states (DAgger)
  - shadow    (M5) decide-without-acting beside a teacher, for the agreement metric
  - metrics   (M5) per-scenario shadow agreement — the promotion number
  - replay    (M5) re-run journaled bundles through decide() — the regression suite

The frozen I/O contract (Bundle/Decision/DecisionRecord) lives in the shared `interaction`
package, not here — both the actor and the trainer must agree on it.
"""
