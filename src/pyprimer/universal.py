"""Universal (broad-coverage) degenerate primers from an alignment: a DegePrime port.

DegePrime (Hugerth et al. 2014, Appl Environ Microbiol 80:5116-5123;
https://github.com/EnvGen/DEGEPRIME, GPL-2.0-or-later) slides a window of
the primer length along a multiple sequence alignment and, at every
position, looks for the degenerate oligo of at most a given degeneracy that
matches as many of the aligned sequences as possible. It uses "Weighted
Randomised Merging": the distinct k-mers in the window are drawn at random,
weighted by how many sequences carry them, and merged into an IUPAC primer
as long as its degeneracy stays within the limit; the best of many such
rounds wins.

This module ports the three steps of the original scripts:

* :func:`trim_alignment` (``TrimAlignment.pl``) removes sparsely occupied
  columns and marks bases that preceded a removed column in lower case, so
  that windows bridging an insertion are not treated as contiguous.
* :func:`degeprime` (``DegePrime.pl``) scores every window, optionally with
  coverage per taxon.
* :func:`taxonomy_from_headers`, :func:`taxonomy_from_rdp_genbank` and
  :func:`taxonomy_from_table` (``MakeSilvaTaxonomy.pl``,
  ``MakeRdpTaxonomy.pl`` and the taxonomy file DegePrime reads).

The randomised search is reproducible here: every window gets its own
random generator seeded from ``seed`` and the window position, and k-mers
are kept in first-seen order (Perl used ``rand`` and hash order).
"""

from __future__ import annotations

import itertools
import math
import multiprocessing
import random
import re
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from .degenerate import IUPAC

#: Bit for each base in a per-position base set.
BITS: Mapping[str, int] = {"A": 1, "C": 2, "G": 4, "T": 8}

#: IUPAC code for each base set (as a 4-bit mask).
CODE_FOR_MASK: Mapping[int, str] = {
    sum(BITS[b] for b in bases): code for code, bases in IUPAC.items() if code != "U"
}

_POPCOUNT = tuple(bin(i).count("1") for i in range(16))

#: Distinct k-mers drawn per round of Weighted Randomised Merging.
MAX_DRAWS = 100

#: Consecutive repeated draws that make the Perl code drop drawn k-mers.
MAX_FAILS = 4

#: Upper limit on windows x iterations for one search.
MAX_WORK = 400_000

#: Sequences x windows above which a search is split over worker processes.
PARALLEL_WORK = 20_000

MIN_LENGTH = 3
MAX_LENGTH = 50
MAX_DEGENERACY = 10_000
MAX_ITERATIONS = 1000

UNASSIGNED = "(unassigned)"

_LEADING_GAP = re.compile(r"^\W*", re.ASCII)
_TRAILING_GAP = re.compile(r"\W*$", re.ASCII)
_WORD = re.compile(r"\w", re.ASCII)
_PURE = re.compile(r"[ACGT]+")


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignedSeq:
    """One row of a FASTA alignment."""

    id: str
    description: str
    seq: str


@dataclass(frozen=True)
class TrimSettings:
    """Column filters of ``TrimAlignment.pl``.

    ``min_occupancy``: minimum fraction of sequences with a base in a column
    (``-min``); ``max_trailing``: maximum fraction with a trailing ``.``
    (``-max_trailing``); ``trailing_as_gap``: count ``.`` as ``-``
    (``-trailgap``); ``reference``: keep only the columns where this sequence
    has a base (``-ref``, excludes the other filters).
    """

    min_occupancy: float = 0.0
    max_trailing: float = 1.0
    trailing_as_gap: bool = False
    reference: str = ""


@dataclass(frozen=True)
class Trimmed:
    """A trimmed alignment; ``columns[i]`` is the 0-based original column of column ``i``."""

    seqs: tuple[AlignedSeq, ...]
    columns: tuple[int, ...]


@dataclass(frozen=True)
class DegePrimeSettings:
    """Search settings of ``DegePrime.pl``.

    ``start``/``end`` limit the search to windows starting in that range of
    (trimmed, 0-based) positions; ``end`` = 0 means up to the last window.
    """

    length: int = 18
    max_degeneracy: int = 12
    min_depth: int = 1
    skip: int = 20
    iterations: int = 100
    seed: int = 1
    start: int = 0
    end: int = 0


DEFAULT_TRIM = TrimSettings()
DEFAULT_SETTINGS = DegePrimeSettings()


@dataclass(frozen=True)
class TaxonCoverage:
    """Sequences of one taxon spanning a window, and how many the primer matches."""

    spanning: int
    matching: int

    @property
    def fraction(self) -> float | None:
        return self.matching / self.spanning if self.spanning else None


@dataclass(frozen=True)
class WindowResult:
    """Best degenerate primer found at one window position.

    ``pos`` is the 0-based position in the trimmed alignment (DegePrime's
    ``Pos``); ``column`` is the 1-based column of the window's first base
    in the alignment as given.
    """

    pos: int
    column: int
    spanning: int
    unique: int
    entropy: float
    degeneracy: int
    primer: str
    matching: int
    fraction: float
    taxa: tuple[TaxonCoverage, ...] = ()


@dataclass(frozen=True)
class DegePrimeResult:
    """Every window with enough gap-free sequences, plus the taxa columns."""

    length: int
    max_degeneracy: int
    windows: tuple[WindowResult, ...]
    taxa: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# FASTA alignments and trimming
# --------------------------------------------------------------------------


def parse_alignment(text: str) -> tuple[AlignedSeq, ...]:
    """Read a FASTA alignment; every sequence must have the same length.

    Raises:
        ValueError: if there are no sequences, names are missing or repeated,
            or the rows differ in length.
    """
    records: list[AlignedSeq] = []
    header: tuple[str, str] | None = None
    parts: list[str] = []

    def flush() -> None:
        if header is not None:
            records.append(AlignedSeq(header[0], header[1], "".join(parts)))

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            flush()
            fields = line[1:].split(None, 1)
            if not fields:
                raise ValueError("A FASTA header has no sequence name")
            header = (fields[0], fields[1].strip() if len(fields) > 1 else "")
            parts = []
        elif line:
            if header is None:
                raise ValueError("The alignment must be in FASTA format (start with a '>' line)")
            parts.append("".join(line.split()))
    flush()

    if not records:
        raise ValueError("Paste or open an alignment in FASTA format")
    seen: set[str] = set()
    for r in records:
        if r.id in seen:
            raise ValueError(f"Sequence name {r.id} appears more than once")
        seen.add(r.id)
        if not r.seq:
            raise ValueError(f"Sequence {r.id} is empty")
    lengths = {len(r.seq) for r in records}
    if len(lengths) > 1:
        raise ValueError(
            "Not all aligned sequences have the same length "
            f"({min(lengths):,} to {max(lengths):,} columns); is this an alignment?"
        )
    return tuple(records)


def to_fasta(seqs: Iterable[AlignedSeq]) -> str:
    """FASTA text with names only, as ``TrimAlignment.pl`` writes it."""
    return "".join(f">{s.id}\n{s.seq}\n" for s in seqs)


def _occupied_columns(seqs: Sequence[AlignedSeq], settings: TrimSettings) -> list[int]:
    width = len(seqs[0].seq)
    counts = [0] * width
    trailing = [0] * width
    for s in seqs:
        seq = s.seq.replace(".", "-") if settings.trailing_as_gap else s.seq
        for i, c in enumerate(seq):
            if c != "-":
                counts[i] += 1
            if c == ".":
                trailing[i] += 1
    total = len(seqs)
    keep = []
    for i in range(width):
        if trailing[i] / total > settings.max_trailing:
            continue
        if (
            total == trailing[i]
            or (counts[i] - trailing[i]) / (total - trailing[i]) >= settings.min_occupancy
        ):
            keep.append(i)
    return keep


def trim_alignment(seqs: Sequence[AlignedSeq], settings: TrimSettings = DEFAULT_TRIM) -> Trimmed:
    """Drop sparsely occupied columns (``TrimAlignment.pl``).

    Bases are upper-cased and U becomes T; a kept base is then written in
    lower case when a removed column after it (before the next kept column)
    held a base, which tells :func:`degeprime` that the sequence has an
    insertion there.

    Raises:
        ValueError: on settings out of range, an unknown reference, or when
            no column is kept.
    """
    if not 0 <= settings.min_occupancy <= 1:
        raise ValueError("Minimum occupancy must be between 0 and 1")
    if not 0 <= settings.max_trailing <= 1:
        raise ValueError("Maximum trailing fraction must be between 0 and 1")
    reference = settings.reference.strip()
    if reference and settings.min_occupancy:
        raise ValueError("Give either a minimum occupancy or a reference sequence, not both")
    if not seqs:
        raise ValueError("The alignment has no sequences")

    if reference:
        ref = next((s for s in seqs if s.id == reference), None)
        if ref is None:
            raise ValueError(
                f"Reference sequence {reference} is not in the alignment "
                "(only the name up to the first space counts)"
            )
        columns = [i for i, c in enumerate(ref.seq) if c.isascii() and c.isalpha()]
    else:
        columns = _occupied_columns(seqs, settings)
    if not columns:
        raise ValueError("No alignment column passes the trimming settings")

    trimmed = []
    for s in seqs:
        seq = s.seq.upper().replace("U", "T")
        out = []
        for here, after in itertools.pairwise(columns):
            nt = seq[here]
            out.append(nt.lower() if _WORD.search(seq[here + 1 : after]) else nt)
        out.append(seq[columns[-1]])
        trimmed.append(AlignedSeq(s.id, s.description, "".join(out)))
    return Trimmed(tuple(trimmed), tuple(columns))


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------


def taxonomy_from_headers(seqs: Iterable[AlignedSeq]) -> dict[str, str]:
    """Taxonomy written after the name in each header (SILVA FASTA, ``MakeSilvaTaxonomy.pl``)."""
    return {s.id: s.description for s in seqs if s.description}


def taxonomy_from_table(text: str) -> dict[str, str]:
    """Read ``id<TAB>taxonomy`` lines, levels separated by ``;``.

    Raises:
        ValueError: if a non-blank line has no tab.
    """
    result: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            raise ValueError(f"Taxonomy line {n}: expected a name, a tab and the taxonomy")
        result[fields[0].strip()] = fields[1].strip()
    return result


_RDP_LINEAGE = re.compile(r"(Root.*[^.]\.)")


def taxonomy_from_rdp_genbank(text: str) -> dict[str, str]:
    """Lineages from an RDP GenBank file (``MakeRdpTaxonomy.pl``).

    The name is the LOCUS name; the lineage is the ORGANISM block with
    spaces removed, from ``Root`` to its final period. Records without such
    a lineage are skipped (Perl reused the previous record's).
    """
    result: dict[str, str] = {}
    locus = ""
    lineage = ""
    inside = False
    for row in text.splitlines():
        fields = re.split(r"\s+", row)
        first = fields[0]
        if first == "LOCUS" and len(fields) > 1:
            locus = fields[1]
        if first in ("REFERENCE", "COMMENT"):
            inside = False
        if inside:
            lineage += row.replace(" ", "")
        if len(fields) > 1 and fields[1] == "ORGANISM":
            inside = True
            lineage = ""
        if first == "//":
            match = _RDP_LINEAGE.search(lineage)
            if match and locus:
                result[locus] = match.group(1)
            inside = False
    return result


def taxon_at_level(taxonomy: str, level: int) -> str:
    """The first ``level`` ``;``-separated ranks (the whole string if it has fewer)."""
    ranks = taxonomy.split(";")
    return taxonomy if len(ranks) < level else ";".join(ranks[:level])


# --------------------------------------------------------------------------
# DegePrime
# --------------------------------------------------------------------------


def is_valid_degeneracy(d: int) -> bool:
    """Whether ``d`` can be the degeneracy of a DNA oligo (2**i * 3**j)."""
    if d < 1:
        return False
    for p in (2, 3):
        while d % p == 0:
            d //= p
    return d == 1


def valid_max_degeneracy(d: int) -> int:
    """Round down to a possible degeneracy, as DegePrime does with ``-d``."""
    if d < 1:
        raise ValueError("Maximum degeneracy must be at least 1")
    while not is_valid_degeneracy(d):
        d -= 1
    return d


def _bounds(seq: str) -> tuple[int, int]:
    """First and last non-gap index (Perl ``\\W``, so ``_`` is not a gap)."""
    lead = _LEADING_GAP.match(seq)
    trail = _TRAILING_GAP.search(seq)
    assert lead is not None and trail is not None
    return len(lead.group()), len(seq) - len(trail.group()) - 1


def _entropy(counts: Iterable[int], total: int) -> float:
    h = 0.0
    for n in counts:
        p = n / total
        h -= p * math.log(p) / math.log(2)
    return h + 0.0


def _mer_masks(mer: str) -> tuple[int, ...]:
    return tuple(BITS[c] for c in mer)


def _degeneracy(masks: Sequence[int]) -> int:
    return math.prod(_POPCOUNT[m] for m in masks)


def _compact(
    ends: list[int], mers: list[int], chosen: set[int], counts: Sequence[int]
) -> tuple[list[int], list[int]]:
    """Drop drawn k-mers from the pool, literally as DegePrime does.

    The Perl loop leaves the cumulative count of the new last k-mer
    unadjusted, so it keeps a larger share of the draws.
    """
    ends = ends[:]
    order = sorted(chosen)
    adjust_counts = adjust_index = 0
    for i in range(order[0], len(ends) - len(order) - 1):
        while i + adjust_index in chosen:
            adjust_counts += counts[mers[i + adjust_index]]
            adjust_index += 1
        ends[i] = ends[i + adjust_index] - adjust_counts
    del ends[len(ends) - len(order) :]
    keep = [m for k, m in enumerate(mers) if k not in chosen]
    return ends, keep


def _matching(
    pool: Sequence[int], mers: Sequence[str], index: Mapping[str, int], counts: Sequence[int]
) -> list[int]:
    """Indices of the window's k-mers that the degenerate primer covers."""
    if _degeneracy(pool) <= len(mers):
        bases = ["".join(b for b in "ACGT" if m & BITS[b]) for m in pool]
        found = (index.get("".join(p)) for p in itertools.product(*bases))
        return [i for i in found if i is not None]
    return [
        i for i, mer in enumerate(mers) if all(m & BITS[c] for m, c in zip(pool, mer, strict=True))
    ]


def _merge(
    mers: Sequence[str],
    counts: Sequence[int],
    max_deg: int,
    iterations: int,
    rng: random.Random,
) -> tuple[tuple[int, ...], int, list[int]]:
    """Weighted Randomised Merging; returns (base masks, degeneracy, matched k-mers)."""
    length = len(mers[0])
    masks = [_mer_masks(m) for m in mers]
    index = {m: i for i, m in enumerate(mers)}
    total_count = sum(counts)

    union = tuple(_or(m[p] for m in masks) for p in range(length))
    if len(mers) <= MAX_DRAWS and _degeneracy(union) <= max_deg:
        # Every round merges every k-mer: the result is the union.
        return union, _degeneracy(union), list(range(len(mers)))

    # Each base set packed 4 bits per position into one integer, so a draw
    # only has to look at the positions where it adds a base.
    packed = [sum(m << 4 * p for p, m in enumerate(ms)) for ms in masks]
    ends = list(itertools.accumulate(counts))
    best: tuple[tuple[int, ...], int, list[int]] | None = None
    best_match = 0
    cache: dict[int, list[int]] = {}
    draw = rng.random
    for _ in range(iterations):
        pool_ends = ends
        pool_mers = list(range(len(mers)))
        chosen: set[int] = set()
        pool = 0
        sizes = [0] * length
        deg = 0
        trial = fails = 0
        while deg < max_deg:
            trial += 1
            if trial > MAX_DRAWS:
                break
            # int(rand(N)) in Perl.
            k = bisect_left(pool_ends, int(draw() * pool_ends[-1]))
            if k in chosen:
                trial -= 1
                fails += 1
            else:
                chosen.add(k)
                fails = 0
                mer = packed[pool_mers[k]]
                new_bits = mer & ~pool
                if not new_bits:
                    continue  # already covered: merging changes nothing
                if not pool:
                    new_deg = 1
                    added = list(range(length))
                else:
                    new_deg = deg
                    added = []
                    while new_bits and new_deg <= max_deg:
                        low = new_bits & -new_bits
                        new_bits ^= low
                        p = (low.bit_length() - 1) >> 2
                        new_deg = new_deg // sizes[p] * (sizes[p] + 1)
                        added.append(p)
                if new_deg <= max_deg:
                    deg = new_deg
                    pool |= mer
                    for p in added:
                        sizes[p] += 1
                    # No further k-mer can add a base without exceeding the limit.
                    if deg * 4 > max_deg * 3:
                        break
            if fails > MAX_FAILS:
                pool_ends, pool_mers = _compact(pool_ends, pool_mers, chosen, counts)
                chosen = set()
            if not pool_ends:
                break
        matched = cache.get(pool)
        if matched is None:
            matched = cache[pool] = _matching(_unpack(pool, length), mers, index, counts)
        match = sum(counts[i] for i in matched)
        if match > best_match:
            best_match = match
            best = (_unpack(pool, length), deg, matched)
            if match == total_count:
                break
    assert best is not None
    return best


def _unpack(pool: int, length: int) -> tuple[int, ...]:
    return tuple((pool >> 4 * p) & 15 for p in range(length))


def _or(values: Iterable[int]) -> int:
    out = 0
    for v in values:
        out |= v
    return out


def window_count(width: int, settings: DegePrimeSettings) -> int:
    """Number of window positions a search will examine."""
    last = width - settings.length
    end = min(settings.end or last, last)
    return max(0, end - settings.start + 1)


def _check_settings(settings: DegePrimeSettings, width: int) -> None:
    s = settings
    if not MIN_LENGTH <= s.length <= MAX_LENGTH:
        raise ValueError(f"Primer length must be {MIN_LENGTH} to {MAX_LENGTH}")
    if s.length > width:
        raise ValueError(f"The primer is longer than the alignment ({width} columns)")
    if not 1 <= s.max_degeneracy <= MAX_DEGENERACY:
        raise ValueError(f"Maximum degeneracy must be 1 to {MAX_DEGENERACY:,}")
    if s.min_depth < 1:
        raise ValueError("Minimum depth must be at least 1")
    if s.skip < 0:
        raise ValueError("Skipped end length cannot be negative")
    if not 1 <= s.iterations <= MAX_ITERATIONS:
        raise ValueError(f"Iterations must be 1 to {MAX_ITERATIONS}")
    if s.start < 0 or s.end < 0:
        raise ValueError("First and last position cannot be negative")
    if s.end and s.end < s.start:
        raise ValueError("The last position is before the first")
    if s.start > width - s.length:
        raise ValueError(f"The first position is past the last possible one ({width - s.length})")
    windows = window_count(width, s)
    if windows * s.iterations > MAX_WORK:
        raise ValueError(
            f"{windows:,} windows x {s.iterations} iterations is too much work for one search "
            f"(limit {MAX_WORK:,}); narrow the first and last position or use fewer iterations"
        )


def degeprime(
    seqs: Sequence[AlignedSeq],
    settings: DegePrimeSettings = DEFAULT_SETTINGS,
    *,
    columns: Sequence[int] | None = None,
    taxonomy: Mapping[str, str] | None = None,
    level: int = 0,
    workers: int = 1,
) -> DegePrimeResult:
    """Best degenerate primer at every window of a (trimmed) alignment.

    ``columns`` maps trimmed to original columns (from :func:`trim_alignment`).
    With ``taxonomy`` (name → ``;``-separated lineage) and ``level`` ≥ 1,
    coverage is also counted per taxon at that rank. Large searches are
    split over up to ``workers`` processes; every window has its own
    random generator, so the result does not depend on the split.

    Raises:
        ValueError: on invalid settings or too large a search.
    """
    if not seqs:
        raise ValueError("The alignment has no sequences")
    width = len(seqs[0].seq)
    _check_settings(settings, width)
    if taxonomy is not None and level < 1:
        raise ValueError("Taxonomy level must be at least 1")
    s = settings
    max_deg = valid_max_degeneracy(s.max_degeneracy)

    taxa: tuple[str, ...] = ()
    taxon_of: list[int] = []
    if taxonomy is not None:
        names = [
            taxon_at_level(taxonomy[x.id], level) if x.id in taxonomy else UNASSIGNED for x in seqs
        ]
        taxa = tuple(sorted(set(names) - {UNASSIGNED})) + (
            (UNASSIGNED,) if UNASSIGNED in names else ()
        )
        position = {t: i for i, t in enumerate(taxa)}
        taxon_of = [position[n] for n in names]

    first = s.start
    last = first + window_count(width, s) - 1
    job = _Scan(
        rows=tuple(x.seq for x in seqs),
        bounds=tuple(_bounds(x.seq) for x in seqs),
        columns=tuple(columns) if columns is not None else tuple(range(width)),
        taxon_of=tuple(taxon_of),
        taxa=len(taxa),
        settings=s,
        max_deg=max_deg,
    )
    chunks = _chunks(first, last, workers if len(seqs) * (last - first + 1) > PARALLEL_WORK else 1)
    if len(chunks) == 1:
        windows = _scan(job, first, last)
    else:
        context = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(len(chunks), mp_context=context) as executor:
            los, his = zip(*chunks, strict=True)
            parts = executor.map(_scan, itertools.repeat(job), los, his)
            windows = [w for part in parts for w in part]
    return DegePrimeResult(
        length=s.length, max_degeneracy=max_deg, windows=tuple(windows), taxa=taxa
    )


def _chunks(first: int, last: int, n: int) -> list[tuple[int, int]]:
    """Split window positions ``first``..``last`` into ``n`` contiguous runs."""
    total = last - first + 1
    n = max(1, min(n, total))
    bounds = [first + total * i // n for i in range(n + 1)]
    return [(lo, hi - 1) for lo, hi in itertools.pairwise(bounds) if hi > lo]


@dataclass(frozen=True)
class _Scan:
    """Everything a worker needs to scan a range of windows."""

    rows: tuple[str, ...]
    bounds: tuple[tuple[int, int], ...]
    columns: tuple[int, ...]
    taxon_of: tuple[int, ...]
    taxa: int
    settings: DegePrimeSettings
    max_deg: int


def _scan(job: _Scan, first: int, last: int) -> list[WindowResult]:
    """Best primer at each window position ``first``..``last`` (``get_mer_ranking``)."""
    s = job.settings
    length = s.length
    windows: list[WindowResult] = []
    for pos in range(first, last + 1):
        counts: dict[str, int] = {}
        carriers: dict[str, list[int]] = {}
        all_mers: dict[str, int] = {}
        spanning: list[int] = []
        for n, (row, (start, end)) in enumerate(zip(job.rows, job.bounds, strict=True)):
            if start + s.skip > pos or end - s.skip < pos + length - 1:
                continue
            mer = row[pos : pos + length - 1] + row[pos + length - 1].upper()
            if _PURE.fullmatch(mer):
                counts[mer] = counts.get(mer, 0) + 1
                carriers.setdefault(mer, []).append(n)
            spanning.append(n)
            all_mers[mer] = all_mers.get(mer, 0) + 1
        if sum(counts.values()) < s.min_depth:
            continue
        mers = list(counts)
        weights = [counts[m] for m in mers]
        rng = random.Random(f"{s.seed}:{pos}")
        pool, deg, matched = _merge(mers, weights, job.max_deg, s.iterations, rng)
        matching = sum(weights[i] for i in matched)
        coverage: tuple[TaxonCoverage, ...] = ()
        if job.taxa:
            span = [0] * job.taxa
            hit = [0] * job.taxa
            for n in spanning:
                span[job.taxon_of[n]] += 1
            for i in matched:
                for n in carriers[mers[i]]:
                    hit[job.taxon_of[n]] += 1
            coverage = tuple(TaxonCoverage(a, b) for a, b in zip(span, hit, strict=True))
        windows.append(
            WindowResult(
                pos=pos,
                column=job.columns[pos] + 1,
                spanning=len(spanning),
                unique=len(mers),
                entropy=_entropy(all_mers.values(), len(spanning)),
                degeneracy=deg,
                primer="".join(CODE_FOR_MASK[m] for m in pool),
                matching=matching,
                fraction=matching / len(spanning),
                taxa=coverage,
            )
        )
    return windows


def _num(x: float) -> str:
    """Format a number the way Perl prints it."""
    return f"{x:.15g}"


def to_tsv(result: DegePrimeResult) -> str:
    """The tab-separated output file of ``DegePrime.pl``."""
    header = [
        "Pos",
        "NumberSpanning",
        "UniqueMers",
        "Entropy",
        "PrimerDeg",
        "PrimerSeq",
        "NumberMatching",
        "FractionMatching",
    ]
    for t in result.taxa:
        header += [f"Spanning {t}", f"Matching {t}", f"Fraction {t}"]
    lines = ["\t".join(header)]
    for w in result.windows:
        cells = [
            str(w.pos),
            str(w.spanning),
            str(w.unique),
            _num(w.entropy),
            str(w.degeneracy),
            w.primer,
            str(w.matching),
            _num(w.fraction),
        ]
        for c in w.taxa:
            fraction = c.fraction
            cells += [
                str(c.spanning),
                str(c.matching),
                "NA" if not c.matching or fraction is None else _num(fraction),
            ]
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"
