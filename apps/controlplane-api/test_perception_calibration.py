"""Calibration — the three bugs that real captures found on 2026-07-22.

All three were invisible in unit fixtures and obvious the moment the fitted observer ran over the
labeled corpus, which is the argument for running a model on real data before believing any of its
numbers. Each is pinned here so it cannot come back.
"""

from __future__ import annotations

import pytest

from perception.dom_witness import TfidfCentroidWitness
from perception.facets import platform_for
from perception.prototypes import PrototypeBank


def _bank_with_a_singleton() -> PrototypeBank:
    """The real corpus's shape in miniature: one well-observed state, one seen exactly once."""
    return PrototypeBank("test").fit([
        ("known", [1.0, 0.0, 0.0]),
        ("known", [0.9, 0.1, 0.0]),
        ("known", [0.8, 0.2, 0.0]),
        ("seen_once", [0.0, 0.0, 1.0]),
    ])


def test_a_singleton_class_does_not_poison_the_calibration():
    """A class with one example has a centroid that IS that example, so it scores a perfect 1.0
    and drags the whole percentile curve up until every well-observed state reads as an outlier.
    Before the fix a genuine `workday_questions` capture scored novelty 0.93."""
    bank = _bank_with_a_singleton()
    assert "seen_once" not in bank._calibration_by_label
    # only the three `known` examples calibrate; the singleton's guaranteed 1.0 is kept out
    assert len(bank._calibration) == 3


def test_familiarity_is_measured_leave_one_out():
    """An example inside its own centroid is not evidence of anything."""
    bank = _bank_with_a_singleton()
    familiar = bank.predict([0.9, 0.1, 0.0])
    assert familiar.novelty < 0.5


def test_novelty_is_calibrated_per_predicted_class_not_one_global_pool():
    """Against a single pool a 20-example state scores MORE novel than a 2-example state, because
    a centroid over twenty varied screenshots sits further from each of them. That measures class
    tightness, not novelty."""
    bank = PrototypeBank("test").fit([
        # a tight class
        ("tight", [1.0, 0.0, 0.0]), ("tight", [0.99, 0.01, 0.0]),
        # a loose one, deliberately spread
        ("loose", [0.0, 1.0, 0.0]), ("loose", [0.0, 0.6, 0.8]), ("loose", [0.0, 0.8, 0.6]),
    ])
    assert set(bank._calibration_by_label) == {"tight", "loose"}
    # A typical member of the LOOSE class must not read as novel just because its class is broad.
    typical_loose = bank.predict([0.0, 0.7, 0.7])
    assert typical_loose.label == "loose"
    assert typical_loose.novelty < 0.9


def test_margin_scale_is_per_witness_because_the_scales_differ_tenfold():
    """Measured on real captures: a correct DOM call sits near a 0.37 margin, a correct visual
    call near 0.04 — every screenshot of a white form is cosine-similar to every other. One shared
    threshold would read the visual witness as permanently unsure."""
    wide = PrototypeBank("wide").fit([
        ("a", [1.0, 0.0]), ("a", [1.0, 0.01]), ("b", [0.0, 1.0]), ("b", [0.01, 1.0])])
    narrow = PrototypeBank("narrow").fit([
        ("a", [1.0, 0.02]), ("a", [1.0, 0.03]), ("b", [1.0, 0.05]), ("b", [1.0, 0.06])])
    assert wide.margin_scale > narrow.margin_scale
    # Each witness's own confident call reads as clear against its own scale.
    assert wide.predict([1.0, 0.0]).clarity == pytest.approx(1.0, abs=0.5)
    assert narrow.predict([1.0, 0.02]).clarity > 0.0


def test_the_dom_witness_calibrates_the_same_way():
    witness = TfidfCentroidWitness().fit([
        ("known", ["tok:a", "tok:b"]), ("known", ["tok:a", "tok:c"]),
        ("seen_once", ["tok:z"]),
    ])
    assert "seen_once" not in witness._calibration_by_label
    assert witness.margin_scale > 0
    assert witness.predict(["tok:a", "tok:b"]).novelty < 0.9


def test_a_facebook_url_is_not_a_company_site():
    """`classify_ats` answers `company_site` for anything it does not recognize, and
    `company_site` is a real platform here — so asking it first made every facebook.com page a
    "company site". Found on a real captcha capture."""
    assert platform_for("captcha_image_challenge",
                        url="https://www.facebook.com/two_step_verification/authentication") == "facebook"
    assert platform_for("gmail_inbox", url="https://mail.google.com/mail/u/0/#inbox") == "google"
    # and a genuinely unrecognized employer host still lands on company_site
    assert platform_for("ats_landing", url="https://careers.example-employer.com/apply") == "company_site"


def test_calibration_survives_a_round_trip():
    bank = _bank_with_a_singleton()
    reloaded = PrototypeBank.from_dict(bank.to_dict())
    assert reloaded._calibration_by_label == bank._calibration_by_label
    assert reloaded.margin_scale == bank.margin_scale
    assert reloaded.predict([0.9, 0.1, 0.0]).novelty == bank.predict([0.9, 0.1, 0.0]).novelty

    witness = TfidfCentroidWitness().fit([("a", ["tok:x"]), ("a", ["tok:x", "tok:y"]),
                                          ("b", ["tok:p"]), ("b", ["tok:p", "tok:q"])])
    again = TfidfCentroidWitness.from_dict(witness.to_dict())
    assert again.predict(["tok:x"]).as_dict() == witness.predict(["tok:x"]).as_dict()
