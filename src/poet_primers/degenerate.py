"""Degenerate (wobble) primers: IUPAC expansion, pool statistics and inosine.

A degenerate primer such as ``AGAGTTTGATYMTGGCTCAG`` is synthesised as an
equimolar pool of every sequence its IUPAC codes allow (here Y = C/T and
M = A/C, so four oligos). This module expands such primers, summarises the
Tm and GC spread of the pool, gives per-position base probabilities for a
sequence logo, and replaces highly degenerate positions with inosine.

Inosine (``I``) pairs with every base, so it counts as a single oligo in the
pool. There are no nearest-neighbour parameters for it in the Tm tables, so
the Tm of inosine-containing primers is not calculated.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .sequence import gc_percent
from .thermo import DEFAULT_CONDITIONS, Conditions, melting_temperature

#: Bases represented by each IUPAC nucleotide code (U is read as T).
IUPAC: Mapping[str, str] = MappingProxyType(
    {
        "A": "A",
        "C": "C",
        "G": "G",
        "T": "T",
        "U": "T",
        "W": "AT",
        "S": "CG",
        "M": "AC",
        "K": "GT",
        "R": "AG",
        "Y": "CT",
        "B": "CGT",
        "D": "AGT",
        "H": "ACT",
        "V": "ACG",
        "N": "ACGT",
    }
)

INOSINE = "I"

#: Largest pool whose oligos are enumerated for Tm statistics.
MAX_VARIANTS = 4096

#: Longest primer accepted.
MAX_LENGTH = 200


@dataclass(frozen=True)
class Spread:
    """Minimum, mean and maximum of a value over the oligo pool."""

    min: float
    avg: float
    max: float


@dataclass(frozen=True)
class Variant:
    """One oligo of a degenerate pool."""

    seq: str
    tm: float
    gc: float


@dataclass(frozen=True)
class DegenerateAnalysis:
    """Statistics of the oligo pool a degenerate primer stands for.

    ``tm`` and ``variants`` are ``None`` when the pool is larger than the
    enumeration limit; ``gc`` is exact for any pool size.
    """

    sequence: str
    length: int
    complexity: int
    gc: Spread
    tm: Spread | None
    variants: tuple[Variant, ...] | None


def normalise(seq: str, *, inosine: bool = False) -> str:
    """Upper-case a primer, drop whitespace and check every code is valid.

    Raises:
        ValueError: if the primer is empty, too long or has a non-IUPAC code.
    """
    seq = "".join(seq.split()).upper()
    if not seq:
        raise ValueError("Enter a primer sequence")
    if len(seq) > MAX_LENGTH:
        raise ValueError(f"The primer is too long ({len(seq)} bases; the limit is {MAX_LENGTH})")
    allowed = IUPAC.keys() | ({INOSINE} if inosine else set())
    bad = sorted({c for c in seq if c not in allowed})
    if bad == [INOSINE]:
        raise ValueError(
            "Inosine (I) is only allowed in the replaced primer; its Tm cannot be calculated"
        )
    if bad:
        raise ValueError(f"Not an IUPAC nucleotide code: {', '.join(bad)}")
    return seq


def degeneracy(code: str) -> int:
    """Number of bases a code stands for (inosine counts as one)."""
    return 1 if code == INOSINE else len(IUPAC[code])


def complexity(seq: str) -> int:
    """Number of distinct oligos in the pool (inosine counts as one base)."""
    return math.prod(degeneracy(c) for c in normalise(seq, inosine=True))


def expand(seq: str) -> Iterator[str]:
    """Yield every oligo of the pool, varying the 3' end fastest."""
    for bases in itertools.product(*(IUPAC[c] for c in normalise(seq))):
        yield "".join(bases)


def base_probabilities(seq: str) -> list[dict[str, float]]:
    """Fraction of the pool carrying each base, position by position."""
    result = []
    for code in normalise(seq):
        bases = IUPAC[code]
        result.append({b: 1 / len(bases) for b in bases})
    return result


def gc_spread(seq: str) -> Spread:
    """Exact minimum, mean and maximum %GC over the pool."""
    sets = [IUPAC[c] for c in normalise(seq)]
    gc = [sum(b in "GC" for b in s) for s in sets]
    lowest = sum(n == len(s) for n, s in zip(gc, sets, strict=True))
    highest = sum(n > 0 for n in gc)
    mean = sum(n / len(s) for n, s in zip(gc, sets, strict=True))
    return Spread(*(v / len(sets) * 100 for v in (lowest, mean, highest)))


def replace_with_inosine(seq: str, min_degeneracy: int = 2) -> str:
    """Replace every position standing for ``min_degeneracy`` or more bases with I.

    Raises:
        ValueError: if ``min_degeneracy`` is below 2 or the primer is invalid.
    """
    if min_degeneracy < 2:
        raise ValueError("Replace positions with at least 2 bases")
    return "".join(
        INOSINE if degeneracy(c) >= min_degeneracy else c for c in normalise(seq, inosine=True)
    )


def analyse_degenerate(
    seq: str,
    conditions: Conditions = DEFAULT_CONDITIONS,
    max_variants: int = MAX_VARIANTS,
) -> DegenerateAnalysis:
    """Complexity, %GC and (for pools up to ``max_variants``) Tm of every oligo.

    Raises:
        ValueError: if the primer is empty, too long or has a non-IUPAC code.
    """
    seq = normalise(seq)
    size = complexity(seq)
    variants: tuple[Variant, ...] | None = None
    tm: Spread | None = None
    if size <= max_variants:
        variants = tuple(
            Variant(seq=v, tm=melting_temperature(v, conditions), gc=gc_percent(v))
            for v in expand(seq)
        )
        tms = [v.tm for v in variants]
        tm = Spread(min(tms), statistics.fmean(tms), max(tms))
    return DegenerateAnalysis(
        sequence=seq,
        length=len(seq),
        complexity=size,
        gc=gc_spread(seq),
        tm=tm,
        variants=variants,
    )
