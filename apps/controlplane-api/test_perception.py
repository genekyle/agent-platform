"""Perception v1 — facets, the prototype bank, the DOM witness, and the bench's own math.

Offline and free: no DB, no model download, no network. The pixel encoder is exercised on an
image written in the test, so the encoder seam is covered without depending on macOS or on a
600 MB checkpoint being present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perception.bench import auroc
from perception.dom_witness import (NaiveBayesWitness, TfidfCentroidWitness, extract_tokens)
from perception.facets import (PHASES, PLATFORMS, condition_for, facets_for, phase_for,
                               platform_for, tenant_for)
from perception.prototypes import PrototypeBank, cosine


# --- facets ---------------------------------------------------------------------------
def test_same_phase_across_two_vendors_is_the_whole_point():
    """`workday_my_information` and `indeed_apply_contact_info` are one question in two chromes.
    If this ever stops holding, the generalization lever is broken."""
    wd = facets_for("workday_my_information")
    indeed = facets_for("indeed_apply_contact_info")
    assert wd.phase == indeed.phase == "personal_information"
    assert wd.platform != indeed.platform
    assert wd.binding == "workday/personal_information"


def test_url_outranks_the_state_id_prefix_for_platform():
    """A state id is a label we chose; a host is a fact. The fact wins."""
    assert platform_for("login_wall", url="https://acme.wd5.myworkdayjobs.com/en-US/x") == "workday"
    assert platform_for("login_wall", domain_id="facebook_marketplace") == "facebook"
    assert platform_for("workday_sign_in") == "workday"


def test_tenant_comes_from_the_host_never_from_the_id():
    assert tenant_for("https://point32health.wd5.myworkdayjobs.com/en-US/THP/job/x") == "point32health"
    assert tenant_for("https://boards.greenhouse.io/kkr/jobs/123") == "kkr"
    assert tenant_for("") == ""
    assert facets_for("workday_questions").variant == "generic"
    assert facets_for("workday_questions",
                      url="https://acme.wd1.myworkdayjobs.com/x").variant == "tenant:acme"


def test_every_derived_facet_is_in_its_closed_vocabulary():
    """Facets are derived, so a typo in a rule table would silently mint a new class."""
    ids = ["workday_my_information", "indeed_apply_questions", "greenhouse_apply_form",
           "fb_listing_condition_picker", "security_checkpoint_captcha", "out_of_domain",
           "gmail_inbox", "indeed_search_results", "task_complete", "workday_error_retry"]
    for state_id in ids:
        f = facets_for(state_id)
        assert f.platform in PLATFORMS, (state_id, f.platform)
        assert f.phase in PHASES, (state_id, f.phase)


def test_unknown_state_lands_on_unknown_not_on_a_guess():
    assert phase_for("some_state_we_have_never_met") == "unknown"


def test_condition_reads_the_stop_states():
    assert condition_for("security_checkpoint_captcha") == "challenge"
    assert condition_for("workday_error_retry") == "error"
    assert condition_for("indeed_apply_submitted") == "complete"
    assert condition_for("fb_listing_condition_picker") == "picker_open"
    assert condition_for("workday_my_information") == "form_ready"


# --- prototype bank -------------------------------------------------------------------
def test_bank_predicts_the_nearer_centroid_and_reports_a_margin():
    bank = PrototypeBank("test").fit([
        ("a", [1.0, 0.0]), ("a", [0.9, 0.1]),
        ("b", [0.0, 1.0]), ("b", [0.1, 0.9]),
    ])
    pred = bank.predict([0.95, 0.05])
    assert pred.label == "a"
    assert pred.margin > 0


def test_novelty_is_calibrated_not_a_raw_cosine():
    """The measured same/different cosine band is 0.897 vs 0.811 — far too narrow for a hand-set
    threshold. Novelty must be a percentile against what familiarity actually looked like."""
    bank = PrototypeBank("test").fit([
        ("a", [1.0, 0.0, 0.0]), ("a", [0.98, 0.02, 0.0]),
        ("b", [0.0, 1.0, 0.0]), ("b", [0.02, 0.98, 0.0]),
    ])
    familiar = bank.predict([0.99, 0.01, 0.0])
    alien = bank.predict([0.0, 0.0, 1.0])
    assert familiar.novelty < 0.5
    assert alien.novelty > familiar.novelty
    assert alien.novelty == pytest.approx(1.0)


def test_an_empty_bank_is_maximally_novel_rather_than_confidently_wrong():
    pred = PrototypeBank("test").predict([1.0, 0.0])
    assert pred.label is None
    assert pred.novelty == 1.0


def test_update_folds_in_a_correction_without_keeping_the_originals():
    bank = PrototypeBank("test").fit([("a", [1.0, 0.0])])
    bank.update("a", [0.0, 1.0])
    assert bank.counts["a"] == 2
    assert bank.prototypes["a"] == pytest.approx([0.5, 0.5])
    bank.update("c", [0.0, 0.0, 1.0])   # a brand-new class needs no refit
    assert bank.counts["c"] == 1


def test_bank_round_trips_through_json(tmp_path: Path):
    bank = PrototypeBank("enc").fit([("a", [1.0, 0.0]), ("b", [0.0, 1.0])])
    reloaded = PrototypeBank.load(bank.save(tmp_path / "bank.json"))
    assert reloaded.encoder_name == "enc"
    assert reloaded.predict([1.0, 0.0]).label == "a"


def test_cosine_is_defensive_about_shape():
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


# --- DOM witness ----------------------------------------------------------------------
def _artifact(title: str, labels: list[str], *, dialog: bool = False) -> dict:
    return {
        "acquisition": {
            "page_identity": {"title": title, "url": "https://acme.wd5.myworkdayjobs.com/en-US/x"},
            "frame_state": {"dialog_present": dialog, "frame_count": 0},
            "actionable_elements": [{"tag": "input", "label": lab, "text": "", "placeholder": ""}
                                    for lab in labels],
        },
        "ranked_candidates": [{"target": {"role": "textbox", "label": lab}} for lab in labels],
    }


def test_the_featurizer_reads_the_surfaces_the_incumbent_ignored():
    """Title, placeholders, element text and the dialog flag are all already in the artifact and
    were never read — that omission is why Workday's form phases looked alike to witness A."""
    toks = extract_tokens(_artifact("My Information", ["First Name"], dialog=True))
    assert "title:information" in toks
    assert "flag:dialog" in toks
    assert "role:textbox" in toks
    assert any(t.startswith("route:") for t in toks)


def test_page_text_reaches_the_same_featurizer_as_the_trainer():
    """The live path has page text but no artifact; the trainer has an artifact and no page text.
    One featurizer or the corpus and the runtime silently disagree about the features."""
    toks = extract_tokens({}, page_text="Voluntary Disclosures")
    assert "txt:voluntary" in toks


@pytest.mark.parametrize("witness_cls", [TfidfCentroidWitness, NaiveBayesWitness])
def test_both_model_families_learn_a_separable_two_class_corpus(witness_cls):
    witness = witness_cls().fit([
        ("sign_in", extract_tokens(_artifact("Sign In", ["Email Address", "Password"]))),
        ("sign_in", extract_tokens(_artifact("Sign In", ["Email", "Password"]))),
        ("questions", extract_tokens(_artifact("Questions", ["Sponsorship", "Salary"]))),
        ("questions", extract_tokens(_artifact("Questions", ["Sponsorship", "Desired Salary"]))),
    ])
    pred = witness.predict(extract_tokens(_artifact("Sign In", ["Email Address", "Password"])))
    assert pred.label == "sign_in"


def test_naive_bayes_reports_zero_novelty_because_it_cannot_know_novelty():
    """Not a bug to paper over — NB's posteriors sum to 1 over the KNOWN classes by construction.
    That blind spot is exactly why witness B exists, so it must be reported honestly."""
    witness = NaiveBayesWitness().fit([
        ("a", ["tok:x", "tok:y"]), ("b", ["tok:p", "tok:q"]),
    ])
    assert witness.predict(["tok:totally", "tok:alien"]).novelty == 0.0


def test_tfidf_flags_an_alien_document_as_novel():
    witness = TfidfCentroidWitness().fit([
        ("a", ["tok:sign", "tok:in", "tok:password"]),
        ("a", ["tok:sign", "tok:in", "tok:email"]),
        ("b", ["tok:salary", "tok:sponsorship"]),
        ("b", ["tok:salary", "tok:visa"]),
    ])
    familiar = witness.predict(["tok:sign", "tok:in", "tok:password"])
    alien = witness.predict(["tok:zebra", "tok:quasar"])
    assert alien.novelty > familiar.novelty


def test_tfidf_can_explain_itself():
    witness = TfidfCentroidWitness().fit([("a", ["tok:password", "tok:password", "tok:email"])])
    assert witness.top_features("a", k=1)[0][0].startswith("tok:")


# --- the bench's own math -------------------------------------------------------------
def test_auroc_endpoints_and_ties():
    assert auroc([1.0, 1.0], [0.0, 0.0]) == 1.0
    assert auroc([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert auroc([1.0], [1.0]) == 0.5
    assert auroc([], [1.0]) is None


# --- encoders -------------------------------------------------------------------------
def test_pixel_encoder_embeds_and_caches(tmp_path: Path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image
    from perception import encoders

    monkeypatch.setenv("PERCEPTION_CACHE_DIR", str(tmp_path / "cache"))
    img = tmp_path / "a.png"
    Image.new("RGB", (64, 64), (10, 200, 30)).save(img)

    enc = encoders.get_encoder("pixel32")
    vec = enc.embed(img)
    assert vec and len(vec) == 32 * 32
    enc.flush()
    assert (tmp_path / "cache" / "pixel32.json").exists()
    # second read comes from the cache, not the image
    assert encoders.get_encoder("pixel32").embed(img) == vec


def test_unknown_encoder_raises_rather_than_silently_falling_back():
    from perception import encoders
    with pytest.raises(ValueError):
        encoders.get_encoder("not_a_real_encoder")


def test_a_missing_image_is_none_not_a_crash(tmp_path: Path, monkeypatch):
    from perception import encoders
    monkeypatch.setenv("PERCEPTION_CACHE_DIR", str(tmp_path))
    assert encoders.get_encoder("pixel32").embed(tmp_path / "nope.png") is None
