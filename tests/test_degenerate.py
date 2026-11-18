"""Tests for degenerate (wobble) primer expansion and inosine replacement."""

from __future__ import annotations

import pytest

from pyprimer.degenerate import (
    IUPAC,
    Spread,
    analyse_degenerate,
    base_probabilities,
    complexity,
    expand,
    gc_spread,
    replace_with_inosine,
)
from pyprimer.sequence import gc_percent
from pyprimer.thermo import Conditions, melting_temperature

PRIMER = "AGAGTTTGATYMTGGCTCAG"  # Y = C/T, M = A/C


def test_expand_example() -> None:
    assert list(expand(PRIMER)) == [
        "AGAGTTTGATCATGGCTCAG",
        "AGAGTTTGATCCTGGCTCAG",
        "AGAGTTTGATTATGGCTCAG",
        "AGAGTTTGATTCTGGCTCAG",
    ]
    assert complexity(PRIMER) == 4


def test_expand_matches_iupac_sizes() -> None:
    for code, bases in IUPAC.items():
        assert sorted(expand(code)) == sorted(bases)
    assert complexity("NBDHV") == 4 * 3**4
    assert list(expand("acgu")) == ["ACGT"]  # case-insensitive, U read as T


def test_analyse_example() -> None:
    conditions = Conditions(oligo_nm=250)
    result = analyse_degenerate(PRIMER, conditions)
    assert result.complexity == 4 and result.length == 20
    assert result.variants is not None
    tms = [melting_temperature(s, conditions) for s in expand(PRIMER)]
    assert [v.tm for v in result.variants] == tms
    assert [v.gc for v in result.variants] == [45, 50, 40, 45]
    assert result.tm == Spread(min(tms), sum(tms) / 4, max(tms))
    assert result.gc == Spread(40, 45, 50)


def test_gc_spread_is_exact() -> None:
    for seq in ("NS", "RYKMWB", "ACGTNNVH"):
        gcs = [gc_percent(s) for s in expand(seq)]
        spread = gc_spread(seq)
        assert spread.min == pytest.approx(min(gcs))
        assert spread.max == pytest.approx(max(gcs))
        assert spread.avg == pytest.approx(sum(gcs) / len(gcs))


def test_large_pool_skips_tm() -> None:
    result = analyse_degenerate("N" * 20)
    assert result.complexity == 4**20
    assert result.tm is None and result.variants is None
    assert result.gc == Spread(0, 50, 100)
    assert analyse_degenerate("ACGTN", max_variants=3).tm is None


def test_base_probabilities() -> None:
    probs = base_probabilities("AYN")
    assert probs[0] == {"A": 1}
    assert probs[1] == {"C": 0.5, "T": 0.5}
    assert probs[2] == dict.fromkeys("ACGT", 0.25)


def test_inosine_replacement() -> None:
    assert replace_with_inosine(PRIMER) == "AGAGTTTGATIITGGCTCAG"
    assert replace_with_inosine(PRIMER, 3) == PRIMER
    assert replace_with_inosine("ARBN", 3) == "ARII"
    assert replace_with_inosine("ARBN", 4) == "ARBI"
    assert complexity("AGAGTTTGATIITGGCTCAG") == 1
    assert complexity(replace_with_inosine("ARBN", 4)) == 6
    with pytest.raises(ValueError, match="at least 2"):
        replace_with_inosine(PRIMER, 1)


@pytest.mark.parametrize(
    ("seq", "message"),
    [
        ("", "Enter a primer"),
        ("ACGTX", "Not an IUPAC nucleotide code: X"),
        ("ACGI", "Inosine"),
        ("A" * 201, "too long"),
    ],
)
def test_invalid(seq: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        analyse_degenerate(seq)
