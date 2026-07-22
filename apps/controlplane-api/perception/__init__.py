"""perception — how the agent knows WHERE IT IS (PLAN_perception_v1).

Two witnesses with complementary failure modes, deliberately kept separate:

  witness A (DOM/AX)  route + roles + accessible names + page text    -> sharp at PHASE
  witness B (visual)  a frozen image encoder + a prototype bank       -> sharp at PLATFORM,
                                                                         and the only thing here
                                                                         that can say "I have
                                                                         never seen anything like
                                                                         this"

They are combined by LATE FUSION and never by concatenation: averaging two uncalibrated
confidences is how a system ends up confidently wrong exactly where it should have raised its
hand. Disagreement is a first-class signal, not noise to smooth.

Measured 2026-07-22 before any of this was designed (73 labeled captures, Apple Vision
FeaturePrint): exact state 55%, platform 93%, same-vs-different AUROC 0.836 — and the confusions
were `workday_my_information <-> workday_questions <-> my_experience`, i.e. exactly the phases the
DOM reads off field labels. That split is the design.
"""
