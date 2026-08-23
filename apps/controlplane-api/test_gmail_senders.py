"""The sender table, read both directions — the tandem seam two sessions share.

`senders_for` (the verify leg's hints) and `classify_sender` (the outcome matcher's attribution)
are one table read forwards and backwards, and these pin the properties that make that safe: the
row shape both readers use, that the ATS mail brand is derived from the REGISTRY rather than from
a domain_id stem, and that a hint too short to be a hint never ships.
"""

from __future__ import annotations

import errands
import gmail_senders


def test_the_ats_mail_brand_is_not_the_callers_domain_stem():
    """THE REASON THIS MODULE EXISTS. `errands._sender_hints` derives `indeed_jobs` → `indeed`,
    which is right when the caller IS the brand that sends the mail. The account rung's caller is
    an ATS: "Boston Children's Hospital on BrassRing" gets its code from `@trm.brassring.com`, and
    no suffix-stripping of any domain_id produces that."""
    hints = gmail_senders.senders_for("brassring", company="Boston Children's Hospital")
    assert any("brassring.com" in h for h in hints)
    # And the company travels too — some tenants mail from their own address with the brand only
    # in the subject line.
    assert "boston children's hospital" in hints


def test_a_row_carries_mail_and_site_domains_side_by_side():
    """The shape seam ruling 4 fixed: ONE row per ATS, both columns, both readers. The outcome
    matcher folds its interim mail-domain table into `ATS_MAIL_DOMAINS` against this shape."""
    row = gmail_senders.domains_for("workday")
    assert set(row) == {"mail", "site"}
    assert "myworkdayjobs.com" in row["site"]
    assert isinstance(row["mail"], tuple)          # the column the matcher extends


def test_a_hint_that_would_match_half_the_inbox_is_not_shipped():
    """A two-letter company is a substring of "message" and "manage". The errand's freshness window
    and its ambiguity refusal soften a MISS; nothing softens a wrong code typed into a live
    signup."""
    hints = gmail_senders.senders_for("workday", company="GE")
    assert "ge" not in hints


def test_an_unknown_ats_falls_back_to_the_stem_rather_than_to_nothing():
    """The old suffix-stripping path is demoted to last resort, not deleted: an ATS nobody has
    catalogued still gets the hint it would have had before this module existed."""
    assert gmail_senders.senders_for("someatsnobodymapped_jobs") == ["someatsnobodymapped"]
    assert errands.domain_stem("someatsnobodymapped_jobs") == "someatsnobodymapped"


def test_the_table_reads_backwards_to_the_same_ats():
    """`classify_sender` is the same knowledge inverted — the property that keeps the matcher and
    the verify leg from growing two tables that disagree about who sent what."""
    assert gmail_senders.classify_sender("no-reply@myworkdayjobs.com") == "workday"
    assert gmail_senders.classify_sender("Talentsuite@trm.brassring.com") == "brassring"
    # A subdomain resolves to its vendor; an address we have no row for is None, never a guess.
    assert gmail_senders.classify_sender("careers@notanatsweknow.example") is None
    assert gmail_senders.classify_sender("") is None
    assert gmail_senders.classify_sender("not-an-address") is None


def test_a_measured_sender_outranks_the_catalogue_constant():
    """The 08-21 consult-side rule, now with a consumer: a sender we have actually SEEN this ATS
    mail from is cited before a registry constant, so the evidence names a measurement."""
    class _Row:
        value = "mail.tenant.example"

    class _Q:
        def filter_by(self, **_kw):
            return self

        def all(self):
            return [_Row()]

    class _DB:
        def query(self, _model):
            return _Q()

    hints = gmail_senders.senders_for("workday", company="MFS", db=_DB())
    assert hints[0] == "mail.tenant.example"
    assert "myworkdayjobs.com" in hints            # the catalogue still rides behind it


def test_a_broken_characteristics_read_does_not_cost_the_hints():
    """Hints are best-effort by construction. A DB that raises must degrade to the table, not take
    the errand down with it — the drive is waiting on this."""
    class _DB:
        def query(self, _model):
            raise RuntimeError("no db")

    assert "myworkdayjobs.com" in gmail_senders.senders_for("workday", db=_DB())
