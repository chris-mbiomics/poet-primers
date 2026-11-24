"""Tests for the DegePrime port (universal degenerate primers from an alignment)."""

from __future__ import annotations

import math
import os
import random
import subprocess
from pathlib import Path

import pytest

from poet_primers import universal
from poet_primers.degenerate import IUPAC
from poet_primers.universal import (
    UNASSIGNED,
    AlignedSeq,
    DegePrimeSettings,
    TrimSettings,
    _compact,
    degeprime,
    is_valid_degeneracy,
    parse_alignment,
    taxon_at_level,
    taxonomy_from_headers,
    taxonomy_from_rdp_genbank,
    taxonomy_from_table,
    to_fasta,
    to_tsv,
    trim_alignment,
    valid_max_degeneracy,
)


def aln(*rows: str) -> tuple[AlignedSeq, ...]:
    return tuple(AlignedSeq(f"s{i + 1}", "", r) for i, r in enumerate(rows))


def search(seqs: tuple[AlignedSeq, ...], **kw: int) -> list[tuple[int, str, int, int]]:
    settings = DegePrimeSettings(**{"skip": 0, "length": 5, **kw})
    return [(w.pos, w.primer, w.matching, w.spanning) for w in degeprime(seqs, settings).windows]


# ------------------------------------------------------------------ parsing


def test_parse_alignment() -> None:
    seqs = parse_alignment(">a  Bacteria;Firmicutes\nAC-G\nT.\n\n>b\nACGGTA\n")
    assert seqs == (
        AlignedSeq("a", "Bacteria;Firmicutes", "AC-GT."),
        AlignedSeq("b", "", "ACGGTA"),
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "FASTA"),
        ("ACGT", "start with a '>'"),
        (">a\nACGT\n>a\nACGT", "more than once"),
        (">a\nACGT\n>b\nACG", "same length"),
        (">a\n>b\nACGT", "empty"),
    ],
)
def test_parse_alignment_errors(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_alignment(text)


# ----------------------------------------------------------------- trimming


def test_trim_default_keeps_everything_upper_cases_and_converts_u() -> None:
    trimmed = trim_alignment(aln("acgu-a", "ACG..A"))
    assert [s.seq for s in trimmed.seqs] == ["ACGT-A", "ACG..A"]
    assert trimmed.columns == (0, 1, 2, 3, 4, 5)


def test_trim_occupancy_marks_bases_before_removed_columns() -> None:
    # Column 2 has a base in one sequence of four: removed at 0.5 occupancy.
    trimmed = trim_alignment(aln("AC-GT", "ACTGT", "AC-GT", "AC-GT"), TrimSettings(0.5))
    assert trimmed.columns == (0, 1, 3, 4)
    assert [s.seq for s in trimmed.seqs] == ["ACGT", "AcGT", "ACGT", "ACGT"]


def test_trim_trailing_dots() -> None:
    rows = ("..ACG", "..ACG", "TTACG", "T-ACG")
    # Dots are not counted as missing by default, so column 0 is fully occupied.
    assert trim_alignment(aln(*rows), TrimSettings(1.0)).columns == (0, 2, 3, 4)
    assert trim_alignment(aln(*rows), TrimSettings(0.5, max_trailing=0.25)).columns == (2, 3, 4)
    gap = TrimSettings(0.75, trailing_as_gap=True)
    assert trim_alignment(aln(*rows), gap).columns == (2, 3, 4)


def test_trim_reference() -> None:
    trimmed = trim_alignment(aln("A-CG-T", "AACGGT"), TrimSettings(reference="s1"))
    assert trimmed.columns == (0, 2, 3, 5)
    assert [s.seq for s in trimmed.seqs] == ["ACGT", "aCgT"]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (TrimSettings(1.5), "between 0 and 1"),
        (TrimSettings(max_trailing=-1), "between 0 and 1"),
        (TrimSettings(0.5, reference="s1"), "not both"),
        (TrimSettings(reference="nope"), "not in the alignment"),
        (TrimSettings(1.0), "No alignment column"),
    ],
)
def test_trim_errors(settings: TrimSettings, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        trim_alignment(aln("A-", "-C"), settings)


# --------------------------------------------------------------- degeneracy


def test_valid_degeneracy() -> None:
    valid = [d for d in range(1, 40) if is_valid_degeneracy(d)]
    assert valid == [1, 2, 3, 4, 6, 8, 9, 12, 16, 18, 24, 27, 32, 36]
    assert [valid_max_degeneracy(d) for d in (12, 10, 5, 7, 1)] == [12, 9, 4, 6, 1]
    with pytest.raises(ValueError):
        valid_max_degeneracy(0)


# ------------------------------------------------------------------ windows


def test_window_counts_spanning_entropy_and_primer() -> None:
    seqs = aln("ACGTACGTAC", "ACGTACGTAC", "ACGAACGTAC", "--GTACGTAC")
    result = degeprime(seqs, DegePrimeSettings(length=4, max_degeneracy=2, skip=0))
    first = result.windows[0]
    assert (first.pos, first.column, first.spanning, first.unique) == (0, 1, 3, 2)
    assert first.entropy == pytest.approx(-(2 / 3) * math.log2(2 / 3) - (1 / 3) * math.log2(1 / 3))
    assert (first.primer, first.degeneracy, first.matching, first.fraction) == ("ACGW", 2, 3, 1)
    # From position 2 the sequence with leading gaps spans the window too.
    assert [w.spanning for w in result.windows] == [3, 3, 4, 4, 4, 4, 4]
    # Degeneracy 1: the most common oligo wins.
    one = degeprime(seqs, DegePrimeSettings(length=4, max_degeneracy=1, skip=0)).windows[0]
    assert (one.primer, one.matching) == ("ACGT", 2)


def test_skip_and_min_depth() -> None:
    seqs = aln("ACGTACGTAC", "ACGTACGTAC")
    assert [w[0] for w in search(seqs, length=4, skip=2)] == [2, 3, 4]
    assert search(seqs, length=4, min_depth=3) == []


def test_lower_case_inside_window_is_not_a_match() -> None:
    # "c" at position 1 marks an insertion removed by trimming: windows that
    # contain it but do not end on it exclude that sequence's oligo.
    seqs = aln("AcGTAC", "ACGTAC")
    assert search(seqs, length=3) == [
        (0, "ACG", 1, 2),
        (1, "CGT", 1, 2),
        (2, "GTA", 2, 2),
        (3, "TAC", 2, 2),
    ]
    # The last base of a window may be lower case.
    assert search(aln("ACGTAc", "ACGTAC"), length=6) == [(0, "ACGTAC", 2, 2)]


def test_gaps_and_ambiguity_codes_are_not_matches() -> None:
    assert search(aln("ACNTA", "ACGTA", "AC-TA"), length=5) == [(0, "ACGTA", 1, 3)]


def test_merging_finds_two_variant_optimum() -> None:
    rows = ["AAAACAAAA"] * 6 + ["AAAATAAAA"] * 4
    assert search(aln(*rows), length=9, max_degeneracy=2) == [(0, "AAAAYAAAA", 10, 10)]
    assert search(aln(*rows), length=9, max_degeneracy=1) == [(0, "AAAACAAAA", 6, 10)]


def test_compact_is_literal() -> None:
    # k-mer counts 5, 3, 2; drawing the first leaves the last one's stale end.
    assert _compact([5, 8, 10], [0, 1, 2], {0}, [5, 3, 2]) == ([3, 8], [1, 2])
    assert _compact([5, 8, 10], [0, 1, 2], {2}, [5, 3, 2]) == ([5, 8], [0, 1])


def _diverse(seed: int, n: int = 40, width: int = 60) -> tuple[AlignedSeq, ...]:
    rng = random.Random(seed)
    root = [rng.choice("ACGT") for _ in range(width)]
    clades = [[c if rng.random() > 0.2 else rng.choice("ACGT") for c in root] for _ in range(5)]
    rows = []
    for _ in range(n):
        row = [c if rng.random() > 0.05 else rng.choice("ACGT-") for c in rng.choice(clades)]
        rows.append("".join(row))
    return aln(*rows)


@pytest.mark.parametrize("max_deg", [1, 4, 12, 48])
def test_primers_respect_degeneracy_and_matches_are_exact(max_deg: int) -> None:
    seqs = _diverse(max_deg)
    settings = DegePrimeSettings(length=12, max_degeneracy=max_deg, skip=0, iterations=20)
    for w in degeprime(seqs, settings).windows:
        sets = [IUPAC[c] for c in w.primer]
        assert w.degeneracy == math.prod(map(len, sets)) <= max_deg
        mers = [s.seq[w.pos : w.pos + 12] for s in seqs]
        hits = sum(all(b in bases for b, bases in zip(m, sets, strict=True)) for m in mers)
        assert w.matching == hits
        assert w.fraction == pytest.approx(hits / w.spanning)


def test_results_are_reproducible_and_independent_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    seqs = _diverse(3)
    settings = DegePrimeSettings(length=10, max_degeneracy=6, skip=0, iterations=10)
    full = degeprime(seqs, settings)
    assert degeprime(seqs, settings) == full
    part = degeprime(seqs, DegePrimeSettings(**{**settings.__dict__, "start": 20, "end": 30}))
    assert part.windows == tuple(w for w in full.windows if 20 <= w.pos <= 30)
    monkeypatch.setattr(universal, "PARALLEL_WORK", 0)
    parallel = degeprime(seqs, settings, workers=3)
    assert parallel == full


def test_columns_map_to_untrimmed_alignment() -> None:
    trimmed = trim_alignment(aln("A-CGTA", "A-CGTA", "AGCGTA"), TrimSettings(0.5))
    windows = degeprime(trimmed.seqs, DegePrimeSettings(length=3, skip=0), columns=trimmed.columns)
    assert [(w.pos, w.column) for w in windows.windows] == [(0, 1), (1, 3), (2, 4)]


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (DegePrimeSettings(length=2), "length"),
        (DegePrimeSettings(length=30), "longer than the alignment"),
        (DegePrimeSettings(length=5, max_degeneracy=0), "degeneracy"),
        (DegePrimeSettings(length=5, min_depth=0), "depth"),
        (DegePrimeSettings(length=5, iterations=0), "Iterations"),
        (DegePrimeSettings(length=5, start=5, end=2), "before the first"),
        (DegePrimeSettings(length=5, start=99), "past the last"),
        (DegePrimeSettings(length=5, iterations=1000), "too much work"),
    ],
)
def test_settings_errors(settings: DegePrimeSettings, message: str) -> None:
    seqs = aln("A" * 1000)
    if message != "too much work":
        seqs = aln("A" * 20)
    with pytest.raises(ValueError, match=message):
        degeprime(seqs, settings)


# ----------------------------------------------------------------- taxonomy


def test_taxon_at_level() -> None:
    assert taxon_at_level("Bacteria;Firmicutes;Bacilli", 2) == "Bacteria;Firmicutes"
    assert taxon_at_level("Bacteria;Firmicutes", 3) == "Bacteria;Firmicutes"


def test_taxonomy_sources() -> None:
    seqs = parse_alignment(">a Bacteria;Firmicutes\nA\n>b\nA\n")
    assert taxonomy_from_headers(seqs) == {"a": "Bacteria;Firmicutes"}
    assert taxonomy_from_table("a\tBacteria;X\n\nb\tArchaea;Y\n") == {
        "a": "Bacteria;X",
        "b": "Archaea;Y",
    }
    with pytest.raises(ValueError, match="line 2"):
        taxonomy_from_table("a\tB\nb B\n")


RDP = """\
LOCUS       S000000001   1500 bp    rRNA     linear   BCT 01-JAN-2000
DEFINITION  Escherichia coli
SOURCE      Escherichia coli
  ORGANISM  Escherichia coli
            Root; Bacteria; Proteobacteria; Gammaproteobacteria;
            Enterobacteriales; Enterobacteriaceae; Escherichia.
REFERENCE   1  (bases 1 to 1500)
//
LOCUS       S000000002   1500 bp    rRNA     linear   BCT 01-JAN-2000
  ORGANISM  unknown
            no lineage here
COMMENT     none
//
"""


def test_rdp_genbank() -> None:
    assert taxonomy_from_rdp_genbank(RDP) == {
        "S000000001": "Root;Bacteria;Proteobacteria;Gammaproteobacteria;"
        "Enterobacteriales;Enterobacteriaceae;Escherichia."
    }


def test_taxon_coverage_and_tsv() -> None:
    seqs = aln("ACGTA", "ACGTA", "ACCTA", "ACCTA", "ACGTA")
    taxonomy = {"s1": "B;X", "s2": "B;X", "s3": "B;Y;1", "s4": "B;Y;2"}
    result = degeprime(
        seqs, DegePrimeSettings(length=5, max_degeneracy=1, skip=0), taxonomy=taxonomy, level=2
    )
    assert result.taxa == ("B;X", "B;Y", UNASSIGNED)
    (w,) = result.windows
    assert (w.primer, w.matching) == ("ACGTA", 3)
    assert [(c.spanning, c.matching, c.fraction) for c in w.taxa] == [
        (2, 2, 1),
        (2, 0, 0),
        (1, 1, 1),
    ]
    assert to_tsv(result).splitlines() == [
        "Pos\tNumberSpanning\tUniqueMers\tEntropy\tPrimerDeg\tPrimerSeq\tNumberMatching\t"
        "FractionMatching\tSpanning B;X\tMatching B;X\tFraction B;X\tSpanning B;Y\tMatching B;Y\t"
        f"Fraction B;Y\tSpanning {UNASSIGNED}\tMatching {UNASSIGNED}\tFraction {UNASSIGNED}",
        "0\t5\t2\t0.970950594454669\t1\tACGTA\t3\t0.6\t2\t2\t1\t2\t0\tNA\t1\t1\t1",
    ]
    with pytest.raises(ValueError, match="level"):
        degeprime(seqs, DegePrimeSettings(length=5), taxonomy=taxonomy, level=0)


# ------------------------------------------- cross-check against the Perl scripts

DEGEPRIME_DIR = os.environ.get("DEGEPRIME_DIR")
perl = pytest.mark.skipif(
    not DEGEPRIME_DIR, reason="set DEGEPRIME_DIR to a DEGEPRIME checkout to compare with Perl"
)


def _random_alignment(rng: random.Random) -> str:
    width = rng.randrange(30, 80)
    base = [rng.choice("ACGT") for _ in range(width)]
    out = []
    for i in range(rng.randrange(3, 30)):
        row = base[:]
        for j in range(width):
            r = rng.random()
            if r < 0.08:
                row[j] = rng.choice("ACGTU")
            elif r < 0.14:
                row[j] = "-"
            elif r < 0.16:
                row[j] = row[j].lower()
        head, tail = rng.randrange(15), rng.randrange(15)
        row[:head] = "." * head
        row[width - tail :] = "." * tail
        out.append(f">s{i} Root;T{i % 3};G{i % 5}\n{''.join(row)}\n")
    return "".join(out)


def _run(script: str, *args: str) -> None:
    assert DEGEPRIME_DIR
    subprocess.run(
        ["perl", str(Path(DEGEPRIME_DIR) / script), *args], check=True, capture_output=True
    )


TRIM_CASES = [
    (TrimSettings(), []),
    (TrimSettings(0.9), ["-min", "0.9"]),
    (TrimSettings(0.5, trailing_as_gap=True), ["-min", "0.5", "-trailgap"]),
    (TrimSettings(0.5, max_trailing=0.2), ["-min", "0.5", "-max_trailing", "0.2"]),
    (TrimSettings(reference="s1"), ["-ref", "s1"]),
]


@perl
@pytest.mark.parametrize("seed", range(15))
def test_trim_matches_perl(seed: int, tmp_path: Path) -> None:
    text = _random_alignment(random.Random(seed))
    (tmp_path / "in.fa").write_text(text)
    seqs = parse_alignment(text)
    for settings, args in TRIM_CASES:
        _run(
            "TrimAlignment.pl", "-i", str(tmp_path / "in.fa"), "-o", str(tmp_path / "out.fa"), *args
        )
        assert to_fasta(trim_alignment(seqs, settings).seqs) == (tmp_path / "out.fa").read_text()


@perl
@pytest.mark.parametrize("seed", range(15))
def test_degeprime_matches_perl_when_deterministic(seed: int, tmp_path: Path) -> None:
    """With a degeneracy every window fits in, the randomised search has one answer."""
    rng = random.Random(seed)
    seqs = parse_alignment(_random_alignment(rng))
    trimmed = trim_alignment(seqs, TrimSettings(0.5))
    (tmp_path / "t.fa").write_text(to_fasta(trimmed.seqs))
    (tmp_path / "tax.txt").write_text("".join(f"{s.id}\t{s.description}\n" for s in seqs))
    length, skip = rng.randrange(5, 10), rng.randrange(5)
    args = ["-l", str(length), "-d", "4096", "-skip", str(skip), "-iter", "5"]
    tax = ["-taxfile", str(tmp_path / "tax.txt"), "-taxlevel", "2"]
    _run("DegePrime.pl", "-i", str(tmp_path / "t.fa"), "-o", str(tmp_path / "dp.tsv"), *args, *tax)
    settings = DegePrimeSettings(length=length, max_degeneracy=4096, skip=skip, iterations=5)
    ours = to_tsv(degeprime(trimmed.seqs, settings, taxonomy=taxonomy_from_headers(seqs), level=2))
    theirs = (tmp_path / "dp.tsv").read_text()
    for a, b in zip(ours.splitlines(), theirs.splitlines(), strict=True):
        a_cells, b_cells = a.split("\t"), b.split("\t")
        # Entropy is summed in hash order in Perl, so the 15th digit varies.
        if a_cells[0] != "Pos":
            assert float(a_cells[3]) == pytest.approx(float(b_cells[3]), rel=1e-12)
            a_cells[3] = b_cells[3]
        assert a_cells == b_cells
