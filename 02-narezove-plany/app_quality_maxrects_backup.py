from __future__ import annotations

import base64
import html as html_mod
import io
import json
import itertools
import math
import os
import pathlib
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
import pandas as pd
import streamlit as st
import streamlit.components.v1 as st_components

_APP_DIR = pathlib.Path(__file__).resolve().parent
_DEFAULT_LOGO_PATH = _APP_DIR / "qpv_logo.png"

# ---------------------------------------------------------------------------
# PDF font with Czech diacritics support
# ---------------------------------------------------------------------------
_PDF_FONT = "Helvetica"
_PDF_FONT_BOLD = "Helvetica-Bold"

def _register_pdf_font() -> None:
    """Register a TTF font with full CE character support for PDF export."""
    global _PDF_FONT, _PDF_FONT_BOLD
    try:
        import matplotlib.font_manager as fm
        dejavu = fm.findfont("DejaVu Sans")
        dejavu_bold = fm.findfont("DejaVu Sans:bold")
        if dejavu and pathlib.Path(dejavu).is_file() and "dejavu" in dejavu.lower():
            pdfmetrics.registerFont(TTFont("DejaVuSans", dejavu))
            _PDF_FONT = "DejaVuSans"
        if dejavu_bold and pathlib.Path(dejavu_bold).is_file() and "dejavu" in dejavu_bold.lower():
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", dejavu_bold))
            _PDF_FONT_BOLD = "DejaVuSans-Bold"
    except Exception:
        pass  # fallback to Helvetica

_register_pdf_font()


def _load_default_logo() -> Optional[bytes]:
    if _DEFAULT_LOGO_PATH.is_file():
        return _DEFAULT_LOGO_PATH.read_bytes()
    return None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartSpec:
    name: str
    width_cm: float
    height_cm: float
    qty: int
    rotatable: bool


@dataclass(frozen=True)
class SheetFormat:
    name: str
    width_cm: float
    height_cm: float
    price_kc: float
    available: int  # 0 = neomezene


@dataclass(frozen=True)
class Item:
    uid: str
    part_name: str
    width_cm: float
    height_cm: float
    rotatable: bool


@dataclass
class FreeRect:
    x_cm: float
    y_cm: float
    width_cm: float
    height_cm: float

    @property
    def area(self) -> float:
        return self.width_cm * self.height_cm


@dataclass
class Placement:
    uid: str
    part_name: str
    x_cm: float
    y_cm: float
    width_cm: float
    height_cm: float
    rotated: bool


@dataclass
class Pattern:
    """Reusable layout: a specific arrangement of parts on one sheet format."""
    fmt: SheetFormat
    part_counts: Dict[str, int]
    placements: List[Placement]
    utilization: float
    total_items: int

    def key(self) -> str:
        counts_str = ",".join(f"{k}:{v}" for k, v in sorted(self.part_counts.items()))
        return f"{self.fmt.name}|{counts_str}"


@dataclass
class SheetResult:
    fmt: SheetFormat
    placements: List[Placement]
    pattern_id: int


@dataclass
class Objectives:
    w_cost: float = 0.25
    w_sheets: float = 0.25
    w_waste: float = 0.50
    w_cuts: float = 0.00
    w_formats: float = 0.00

    def normalised(self) -> "Objectives":
        total = self.w_cost + self.w_sheets + self.w_waste + self.w_cuts + self.w_formats
        if total < 1e-9:
            return Objectives(0.2, 0.2, 0.2, 0.2, 0.2)
        f = 1.0 / total
        return Objectives(
            w_cost=self.w_cost * f, w_sheets=self.w_sheets * f,
            w_waste=self.w_waste * f, w_cuts=self.w_cuts * f, w_formats=self.w_formats * f,
        )

    def pct(self) -> Dict[str, float]:
        n = self.normalised()
        return {
            "Cena materialu": round(n.w_cost * 100),
            "Pocet archu": round(n.w_sheets * 100),
            "Odpad (vyteznost)": round(n.w_waste * 100),
            "Pocet rezu": round(n.w_cuts * 100),
            "Pocet formatu": round(n.w_formats * 100),
        }


@dataclass
class OptimizationResult:
    sheet_results: List[SheetResult]
    patterns_used: List[Tuple[Pattern, int]]  # (pattern, count)
    utilization_ratio: float
    total_cost: float
    attempts: int
    elapsed_sec: float
    lower_bound_sheets: int
    objectives_score: float
    formats_used: int
    total_cuts: int


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_parts(csv_text: str) -> List[PartSpec]:
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Seznam dilcu je prazdny. Zadej alespon jeden dilek.")
    out: List[PartSpec] = []
    for idx, line in enumerate(lines, start=1):
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 5:
            raise ValueError(
                f"Radek {idx}: Ocekavam 5 hodnot oddelennych carkou - "
                f"nazev, sirka, vyska, mnozstvi, rotace (1/0). Dostal jsem {len(cols)} hodnot."
            )
        name, w_s, h_s, q_s, r_s = cols
        w, h, q = float(w_s), float(h_s), int(float(q_s))
        if w <= 0 or h <= 0 or q <= 0:
            raise ValueError(f"Radek {idx}: Sirka, vyska i mnozstvi musi byt vetsi nez 0.")
        if r_s not in ("0", "1"):
            raise ValueError(f"Radek {idx}: Rotace musi byt 0 (zakazana) nebo 1 (povolena).")
        out.append(PartSpec(name=name, width_cm=w, height_cm=h, qty=q, rotatable=(r_s == "1")))
    names = [p.name for p in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicitní názvy dílců: {', '.join(dupes)}. Každý dílec musí mít unikátní název.")
    return out


def parse_formats(csv_text: str) -> List[SheetFormat]:
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Zadej alespon jeden format archu (napr. Arch A,70,100,50,0).")
    out: List[SheetFormat] = []
    for idx, line in enumerate(lines, start=1):
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 5:
            raise ValueError(
                f"Radek {idx}: Ocekavam 5 hodnot - "
                f"nazev, sirka, vyska, cena_kc, dostupne_mnozstvi (0=neomezene). Dostal jsem {len(cols)}."
            )
        name, w_s, h_s, p_s, a_s = cols
        w, h, p, avail = float(w_s), float(h_s), float(p_s), int(float(a_s))
        if w <= 0 or h <= 0:
            raise ValueError(f"Radek {idx}: Sirka a vyska musi byt vetsi nez 0.")
        if p < 0:
            raise ValueError(f"Radek {idx}: Cena musi byt 0 nebo vyssi.")
        if avail < 0:
            raise ValueError(f"Radek {idx}: Dostupne mnozstvi musi byt 0 (= neomezene) nebo kladne cislo.")
        out.append(SheetFormat(name=name, width_cm=w, height_cm=h, price_kc=p, available=avail))
    names = [f.name for f in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicitní názvy formátů: {', '.join(dupes)}. Každý formát musí mít unikátní název.")
    return out


# ---------------------------------------------------------------------------
# Default DataFrames for data_editor inputs
# ---------------------------------------------------------------------------

_DEFAULT_PARTS_DF = pd.DataFrame({
    "Název": pd.Series(dtype="str"),
    "Šířka (cm)": pd.Series(dtype="float"),
    "Výška (cm)": pd.Series(dtype="float"),
    "Počet kusů": pd.Series(dtype="int"),
    "Lze otočit": pd.Series(dtype="bool"),
})

_DEFAULT_FORMATS_DF = pd.DataFrame({
    "Název": pd.Series(dtype="str"),
    "Šířka (cm)": pd.Series(dtype="float"),
    "Výška (cm)": pd.Series(dtype="float"),
    "Dostupné množství (0 = ∞)": pd.Series(dtype="int"),
})


def df_to_parts(df: "pd.DataFrame") -> List[PartSpec]:
    rows = df.dropna(subset=["Název", "Šířka (cm)", "Výška (cm)", "Počet kusů"])
    rows = rows[rows["Název"].astype(str).str.strip() != ""]
    if rows.empty:
        raise ValueError("Seznam dílců je prázdný. Zadej alespoň jeden dílec.")
    out: List[PartSpec] = []
    for i, (_, row) in enumerate(rows.iterrows(), start=1):
        name = str(row["Název"]).strip()
        w, h, q = float(row["Šířka (cm)"]), float(row["Výška (cm)"]), int(row["Počet kusů"])
        rotatable = bool(row.get("Lze otočit", False))
        if w <= 0 or h <= 0 or q <= 0:
            raise ValueError(f"Řádek {i} ({name}): šířka, výška i počet kusů musí být větší než 0.")
        out.append(PartSpec(name=name, width_cm=w, height_cm=h, qty=q, rotatable=rotatable))
    names = [p.name for p in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicitní názvy dílců: {', '.join(dupes)}. Každý dílec musí mít unikátní název.")
    return out


def df_to_formats(df: "pd.DataFrame") -> List[SheetFormat]:
    rows = df.dropna(subset=["Název", "Šířka (cm)", "Výška (cm)"])
    rows = rows[rows["Název"].astype(str).str.strip() != ""]
    if rows.empty:
        raise ValueError("Zadej alespoň jeden formát archu.")
    out: List[SheetFormat] = []
    for i, (_, row) in enumerate(rows.iterrows(), start=1):
        name = str(row["Název"]).strip()
        w, h = float(row["Šířka (cm)"]), float(row["Výška (cm)"])
        price = 0.0
        avail = int(row.get("Dostupné množství (0 = ∞)", 0) or 0)
        if w <= 0 or h <= 0:
            raise ValueError(f"Řádek {i} ({name}): šířka a výška musí být větší než 0.")
        if price < 0:
            raise ValueError(f"Řádek {i} ({name}): cena musí být 0 nebo vyšší.")
        if avail < 0:
            raise ValueError(f"Řádek {i} ({name}): dostupné množství musí být 0 (= neomezené) nebo kladné číslo.")
        out.append(SheetFormat(name=name, width_cm=w, height_cm=h, price_kc=price, available=avail))
    names = [f.name for f in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicitní názvy formátů: {', '.join(dupes)}. Každý formát musí mít unikátní název.")
    return out


# ---------------------------------------------------------------------------
# Geometry (MaxRects)
# ---------------------------------------------------------------------------

def _intersect(a: FreeRect, b: FreeRect) -> Optional[FreeRect]:
    x1, y1 = max(a.x_cm, b.x_cm), max(a.y_cm, b.y_cm)
    x2 = min(a.x_cm + a.width_cm, b.x_cm + b.width_cm)
    y2 = min(a.y_cm + a.height_cm, b.y_cm + b.height_cm)
    if x2 <= x1 or y2 <= y1:
        return None
    return FreeRect(x1, y1, x2 - x1, y2 - y1)


def _split(fr: FreeRect, used: FreeRect) -> List[FreeRect]:
    inter = _intersect(fr, used)
    if inter is None:
        return [fr]
    out: List[FreeRect] = []
    if inter.x_cm > fr.x_cm:
        out.append(FreeRect(fr.x_cm, fr.y_cm, inter.x_cm - fr.x_cm, fr.height_cm))
    rs = inter.x_cm + inter.width_cm
    if rs < fr.x_cm + fr.width_cm:
        out.append(FreeRect(rs, fr.y_cm, fr.x_cm + fr.width_cm - rs, fr.height_cm))
    if inter.y_cm > fr.y_cm:
        out.append(FreeRect(fr.x_cm, fr.y_cm, fr.width_cm, inter.y_cm - fr.y_cm))
    bs = inter.y_cm + inter.height_cm
    if bs < fr.y_cm + fr.height_cm:
        out.append(FreeRect(fr.x_cm, bs, fr.width_cm, fr.y_cm + fr.height_cm - bs))
    return [r for r in out if r.width_cm > 1e-9 and r.height_cm > 1e-9]


def _prune(rects: List[FreeRect]) -> List[FreeRect]:
    def inside(a: FreeRect, b: FreeRect) -> bool:
        return (a.x_cm >= b.x_cm and a.y_cm >= b.y_cm
                and a.x_cm + a.width_cm <= b.x_cm + b.width_cm
                and a.y_cm + a.height_cm <= b.y_cm + b.height_cm)
    return [r for i, r in enumerate(rects) if not any(i != j and inside(r, o) for j, o in enumerate(rects))]


def _score_place(heuristic: str, fr: FreeRect, w: float, h: float) -> Tuple[float, float]:
    dw, dh = fr.width_cm - w, fr.height_cm - h
    short, lng = min(dw, dh), max(dw, dh)
    area_fit = fr.area - w * h
    if heuristic == "BAF":
        return (area_fit, short)
    if heuristic == "BSSF":
        return (short, lng)
    return (lng, short)


def _find_best_on_sheet(
    free_rects: List[FreeRect], item: Item, heuristic: str,
    rng: random.Random, force_no_rotate: bool, uw: float, uh: float,
) -> Optional[Tuple[float, float, float, float, bool]]:
    opts = [(item.width_cm, item.height_cm, False)]
    if (not force_no_rotate) and item.rotatable and item.width_cm != item.height_cm:
        opts.append((item.height_cm, item.width_cm, True))
    if len(opts) == 2 and rng.random() < 0.5:
        opts = [opts[1], opts[0]]
    best = None
    for fr in free_rects:
        for w, h, rot in opts:
            if w <= fr.width_cm + 1e-9 and h <= fr.height_cm + 1e-9 and w <= uw and h <= uh:
                sc = _score_place(heuristic, fr, w, h)
                cand = (fr.x_cm, fr.y_cm, w, h, rot, sc[0], sc[1])
                if best is None or (cand[5], cand[6]) < (best[5], best[6]):
                    best = cand
    return None if best is None else (best[0], best[1], best[2], best[3], best[4])


# ---------------------------------------------------------------------------
# Pattern generation
# ---------------------------------------------------------------------------

def _gen_pattern(
    part_types: List[PartSpec],
    fmt: SheetFormat,
    margin: float,
    gap: float,
    force_no_rotate: bool,
    heuristic: str,
    rng: random.Random,
    priority_order: Optional[List[str]] = None,
) -> Optional[Pattern]:
    """Fill one sheet with parts from the given types, creating a reusable pattern."""
    uw = fmt.width_cm - 2 * margin
    uh = fmt.height_cm - 2 * margin
    if uw <= 0 or uh <= 0:
        return None
    sheet_area = uw * uh

    # Build item pool: enough copies of each type to potentially fill the sheet
    pool: List[Item] = []
    for p in part_types:
        fits_normal = p.width_cm <= uw and p.height_cm <= uh
        fits_rotated = p.rotatable and p.height_cm <= uw and p.width_cm <= uh
        if not (fits_normal or fits_rotated):
            continue
        part_area = p.width_cm * p.height_cm
        max_on_sheet = int(sheet_area / part_area) + 2
        count = min(max_on_sheet, p.qty)
        for i in range(count):
            pool.append(Item(
                uid=f"{p.name}-{i}", part_name=p.name,
                width_cm=p.width_cm, height_cm=p.height_cm,
                rotatable=p.rotatable,
            ))

    if not pool:
        return None

    # Order the pool based on strategy
    if priority_order:
        def sort_key(it: Item) -> Tuple:
            try:
                prio = priority_order.index(it.part_name)
            except ValueError:
                prio = 999
            return (prio, -(it.width_cm * it.height_cm))
        pool.sort(key=sort_key)
    else:
        pool.sort(key=lambda it: -(it.width_cm * it.height_cm))

    # Small perturbation within same-type groups
    if rng.random() < 0.3:
        n = len(pool)
        swaps = max(1, n // 20)
        for _ in range(swaps):
            i, j = rng.randrange(n), rng.randrange(n)
            pool[i], pool[j] = pool[j], pool[i]

    # Pack onto a single sheet using MaxRects
    free_rects = [FreeRect(0.0, 0.0, uw, uh)]
    placements: List[Placement] = []
    placed_counts: Dict[str, int] = {}
    parts_area = 0.0

    for item in pool:
        slot = _find_best_on_sheet(free_rects, item, heuristic, rng, force_no_rotate, uw, uh)
        if slot is None:
            continue
        x, y, w, h, rotated = slot
        pl = Placement(uid=item.uid, part_name=item.part_name,
                       x_cm=x, y_cm=y, width_cm=w, height_cm=h, rotated=rotated)
        used = FreeRect(x, y, w + gap, h + gap)
        free_rects = _prune([sf for fr in free_rects for sf in _split(fr, used)])
        placements.append(pl)
        placed_counts[item.part_name] = placed_counts.get(item.part_name, 0) + 1
        parts_area += w * h

    if not placements:
        return None

    return Pattern(
        fmt=fmt,
        part_counts=placed_counts,
        placements=placements,
        utilization=parts_area / sheet_area,
        total_items=len(placements),
    )


def _generate_all_patterns(
    parts: List[PartSpec],
    formats: List[SheetFormat],
    margin: float,
    gap: float,
    force_no_rotate: bool,
    budget_s: float,
) -> List[Pattern]:
    """Generate a diverse set of candidate patterns within time budget.

    When rotation is allowed, we ALSO generate all patterns without rotation
    and keep whichever variant has better utilization. This guarantees that
    enabling rotation never produces a worse result than disabling it.
    """
    heuristics = ["BAF", "BSSF", "BLSF"]
    t_end = time.perf_counter() + budget_s
    seen: Dict[str, Pattern] = {}
    part_names = [p.name for p in parts]

    # When rotation is allowed, try BOTH variants for every pattern attempt
    rotate_modes = [True, False] if not force_no_rotate else [True]

    def _add(pat: Optional[Pattern]) -> None:
        if pat is None:
            return
        k = pat.key()
        if k not in seen or pat.utilization > seen[k].utilization:
            seen[k] = pat

    def _try(part_list: List[PartSpec], fmt: SheetFormat, h: str,
             rng: random.Random, prio: Optional[List[str]] = None) -> None:
        for no_rot in rotate_modes:
            _add(_gen_pattern(part_list, fmt, margin, gap, no_rot, h, rng, priority_order=prio))

    trial = 0
    while time.perf_counter() < t_end:
        trial += 1
        rng = random.Random(trial)
        h = heuristics[trial % len(heuristics)]

        for fmt in formats:
            for p in parts:
                _try([p], fmt, h, rng)

            for prio_name in part_names:
                prio = [prio_name] + [n for n in part_names if n != prio_name]
                _try(parts, fmt, h, rng, prio=prio)

            _try(parts, fmt, h, rng)

            if len(parts) >= 2:
                for combo in itertools.combinations(parts, 2):
                    prio = [combo[0].name, combo[1].name]
                    if rng.random() < 0.5:
                        prio = prio[::-1]
                    _try(list(combo), fmt, h, rng, prio=prio)

            shuffled_names = list(part_names)
            rng.shuffle(shuffled_names)
            _try(parts, fmt, h, rng, prio=shuffled_names)

        if len(seen) > 80 and time.perf_counter() > t_end * 0.8:
            break

    return list(seen.values())


# ---------------------------------------------------------------------------
# Pattern selection – optimal iterative with custom remainder
# ---------------------------------------------------------------------------


def _best_fresh_pattern(
    rem_parts: List[PartSpec],
    formats: List[SheetFormat],
    margin: float,
    gap: float,
    force_no_rotate: bool,
    fmt_limits: Dict[str, int],
    fmt_used: Optional[Dict[str, int]] = None,
) -> Optional[Pattern]:
    """Generate the best single pattern for exactly the given remaining parts.

    Tries all heuristics × formats × seeds and returns the pattern with
    highest utilization, respecting format availability limits.
    """
    best: Optional[Pattern] = None
    _fmt_used = fmt_used or {}
    for fmt in formats:
        limit = fmt_limits.get(fmt.name, 0)
        already = _fmt_used.get(fmt.name, 0)
        if limit > 0 and already >= limit:
            continue
        for h in ("BAF", "BSSF", "BLSF"):
            for seed in (42, 137, 271):
                pat = _gen_pattern(
                    rem_parts, fmt, margin, gap,
                    force_no_rotate, h, random.Random(seed),
                )
                if pat is None:
                    continue
                # Prefer patterns that cover more demanded part types
                covers = sum(1 for name, cnt in pat.part_counts.items() if cnt > 0)
                # Primary: coverage, secondary: utilization
                if best is None:
                    best = pat
                else:
                    best_covers = sum(1 for name, cnt in best.part_counts.items() if cnt > 0)
                    if (covers, pat.utilization) > (best_covers, best.utilization):
                        best = pat
    return best


def _select_patterns_mip(
    candidates: List[Pattern],
    demand: Dict[str, int],
    obj: Objectives,
    fmt_limits: Dict[str, int],
    n_fmts_available: int,
    max_patterns: int,
    margin: float,
    total_demand_area: float,
    ref_sheet_area: float,
) -> List[Tuple[Pattern, int]]:
    """Select patterns using OR-Tools MIP solver for globally optimal solution.

    Given a pool of candidate patterns (from MaxRects), finds the optimal
    combination of patterns and their repetition counts to cover all demand
    while respecting max_patterns limit and format availability.
    """
    from ortools.linear_solver import pywraplp

    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        return []
    solver.SetTimeLimit(8_000)

    n_obj = obj.normalised()
    n = len(candidates)
    if n == 0:
        return []

    part_names = list(demand.keys())
    total_demand = sum(demand.values())
    M = total_demand + 1
    lb_sheets = max(1, math.ceil(total_demand_area / max(ref_sheet_area, 1.0)))

    # --- Decision variables ---
    x = [solver.IntVar(0, M, f"x{i}") for i in range(n)]
    y = [solver.BoolVar(f"y{i}") for i in range(n)]

    # Overproduction per part type (auxiliary)
    overprod = [solver.NumVar(0, M, f"over_{j}") for j in range(len(part_names))]

    # Slack (undercoverage) per part type — soft demand constraint
    slack = [solver.IntVar(0, demand[pname], f"slack_{j}") for j, pname in enumerate(part_names)]

    # Format usage binary vars (for w_formats objective)
    fmt_names_set = sorted({c.fmt.name for c in candidates})
    z = {f: solver.BoolVar(f"z_{f}") for f in fmt_names_set}

    # --- Constraints ---

    # 1. Demand coverage (soft) + overproduction definition
    # slack[j] captures undercoverage; penalized heavily in objective so solver
    # always prefers full coverage but stays feasible when max_patterns is very
    # restrictive (e.g. max_patterns=1 with many part types).
    for j, pname in enumerate(part_names):
        produced = solver.Sum(
            candidates[i].part_counts.get(pname, 0) * x[i] for i in range(n)
        )
        solver.Add(produced + slack[j] >= demand[pname])
        solver.Add(overprod[j] >= produced - demand[pname])

    # 2. Pattern limit
    if max_patterns > 0:
        solver.Add(solver.Sum(y) <= max_patterns)

    # 3. Link x[i] > 0 ⟹ y[i] = 1
    for i in range(n):
        solver.Add(x[i] <= M * y[i])

    # 4. Format availability limits
    for fname in fmt_names_set:
        limit = fmt_limits.get(fname, 0)
        if limit > 0:
            solver.Add(
                solver.Sum(x[i] for i in range(n) if candidates[i].fmt.name == fname)
                <= limit
            )

    # 5. Link y[i] ⟹ z[format] (for format count objective)
    for i in range(n):
        solver.Add(y[i] <= z[candidates[i].fmt.name])

    # --- Objective: weighted multi-criteria minimization ---
    cuts_per = [_count_cuts_pattern(c) for c in candidates]
    cost_norm = max(
        max(c.fmt.price_kc for c in candidates) * lb_sheets, 1.0
    )
    cuts_norm = max(max(cuts_per) * lb_sheets, 1.0) if cuts_per else 1.0

    solver.Minimize(
        n_obj.w_sheets * solver.Sum(x[i] for i in range(n)) / max(lb_sheets, 1)
        + n_obj.w_cost * solver.Sum(
            candidates[i].fmt.price_kc * x[i] for i in range(n)
        ) / cost_norm
        + n_obj.w_waste * solver.Sum(
            (1.0 - candidates[i].utilization) * x[i] for i in range(n)
        )
        + n_obj.w_cuts * solver.Sum(
            cuts_per[i] * x[i] for i in range(n)
        ) / cuts_norm
        + n_obj.w_formats * solver.Sum(
            z[f] for f in fmt_names_set
        ) / max(n_fmts_available, 1)
        + 10.0 * solver.Sum(overprod)
        + 1000.0 * solver.Sum(slack)
    )

    status = solver.Solve()
    if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        result = [
            (candidates[i], int(round(x[i].solution_value())))
            for i in range(n)
            if x[i].solution_value() > 0.5
        ]
        if result:
            return result
    return []


# ---------------------------------------------------------------------------
# Pattern evaluation helpers
# ---------------------------------------------------------------------------

def _count_cuts_pattern(pat: Pattern) -> int:
    xs, ys = set(), set()
    for pl in pat.placements:
        xs.add(round(pl.x_cm, 3)); xs.add(round(pl.x_cm + pl.width_cm, 3))
        ys.add(round(pl.y_cm, 3)); ys.add(round(pl.y_cm + pl.height_cm, 3))
    return len(xs) + len(ys)


def _evaluate_selection(
    selection: List[Tuple[Pattern, int]],
    demand: Dict[str, int],
    obj: Objectives,
    total_demand_area: float,
    n_fmts_available: int,
    ref_sheet_area: float = 0.0,
    margin: float = 0.0,
) -> float:
    """Score a selection of patterns (lower = better). Globally comparable."""
    n = obj.normalised()
    if not selection:
        return float("inf")

    total_sheets = sum(cnt for _, cnt in selection)
    total_cost = sum(pat.fmt.price_kc * cnt for pat, cnt in selection)
    total_parts_area = sum(
        sum(pl.width_cm * pl.height_cm for pl in pat.placements) * cnt
        for pat, cnt in selection
    )
    total_usable_area = sum(
        (pat.fmt.width_cm - 2 * margin) * (pat.fmt.height_cm - 2 * margin) * cnt
        for pat, cnt in selection
    )
    util = total_parts_area / total_usable_area if total_usable_area > 0 else 0.0
    total_cuts = sum(_count_cuts_pattern(pat) * cnt for pat, cnt in selection)
    n_formats = len({pat.fmt.name for pat, _ in selection})

    produced: Dict[str, int] = {}
    for pat, cnt in selection:
        for name, pc in pat.part_counts.items():
            produced[name] = produced.get(name, 0) + pc * cnt
    overprod = sum(max(0, produced.get(name, 0) - qty) for name, qty in demand.items())
    underprod = sum(max(0, qty - produced.get(name, 0)) for name, qty in demand.items())

    # Global reference: lower bound based on largest available format
    lb_sheets = max(1, math.ceil(total_demand_area / max(ref_sheet_area, 1.0)))

    return (
        n.w_sheets * (total_sheets / max(lb_sheets, 1))
        + n.w_cost * (total_cost / max(total_sheets * 10.0, 1.0))
        + n.w_waste * (1.0 - util)
        + n.w_cuts * (total_cuts / max(total_sheets * 15, 1))
        + n.w_formats * (n_formats / max(n_fmts_available, 1))
        + overprod * 0.5
        + underprod * 100.0
    )


# ---------------------------------------------------------------------------
# Build result from selection
# ---------------------------------------------------------------------------

def _build_result(
    selection: List[Tuple[Pattern, int]],
    demand: Dict[str, int],
    formats: List[SheetFormat],
    margin: float,
    attempts: int,
    elapsed: float,
) -> OptimizationResult:
    sheet_results: List[SheetResult] = []
    for pat_idx, (pat, cnt) in enumerate(selection):
        for _ in range(cnt):
            sheet_results.append(SheetResult(
                fmt=pat.fmt,
                placements=list(pat.placements),
                pattern_id=pat_idx,
            ))

    total_parts_area = sum(
        sum(pl.width_cm * pl.height_cm for pl in pat.placements) * cnt
        for pat, cnt in selection
    )
    total_usable = sum(
        (sr.fmt.width_cm - 2 * margin) * (sr.fmt.height_cm - 2 * margin)
        for sr in sheet_results
    )
    util = total_parts_area / total_usable if total_usable > 0 else 0.0

    total_cuts = sum(_count_cuts_pattern(pat) * cnt for pat, cnt in selection)
    formats_used = len({pat.fmt.name for pat, _ in selection})

    best_fmt_area = max(
        (f.width_cm - 2 * margin) * (f.height_cm - 2 * margin)
        for f in formats
    ) if formats else 1.0
    lb = max(1, math.ceil(total_parts_area / best_fmt_area)) if selection else 1

    return OptimizationResult(
        sheet_results=sheet_results,
        patterns_used=selection,
        utilization_ratio=util,
        total_cost=sum(sr.fmt.price_kc for sr in sheet_results),
        attempts=attempts,
        elapsed_sec=elapsed,
        lower_bound_sheets=lb,
        objectives_score=0.0,
        formats_used=formats_used,
        total_cuts=total_cuts,
    )


# ---------------------------------------------------------------------------
# Main optimizer
# ---------------------------------------------------------------------------

def _optimize_single_run(
    parts: List[PartSpec],
    formats: List[SheetFormat],
    margin: float,
    gap: float,
    budget_s: float,
    force_no_rotate: bool,
    obj: Objectives,
    max_patterns: int,
    t0: float,
) -> Optional[OptimizationResult]:
    """Single optimization pass with a fixed rotate setting. Returns None if no solution found."""
    demand = {p.name: p.qty for p in parts}
    fmt_limits = {f.name: f.available for f in formats}

    gen_budget = max(2.0, budget_s * 0.85)
    all_patterns = _generate_all_patterns(parts, formats, margin, gap, force_no_rotate, gen_budget)
    if not all_patterns:
        return None

    n_fmts = len(formats)
    total_demand_area = sum(p.width_cm * p.height_cm * p.qty for p in parts)
    ref_sheet_area = max(
        (f.width_cm - 2 * margin) * (f.height_cm - 2 * margin) for f in formats
    )

    # --- Primary path: MIP solver for globally optimal pattern selection ---
    selection = _select_patterns_mip(
        all_patterns, demand, obj, fmt_limits, n_fmts, max_patterns,
        margin, total_demand_area, ref_sheet_area,
    )

    # --- Fallback: if MIP fails, use best single repeated pattern ---
    if not selection:
        best_pat = max(all_patterns, key=lambda p: p.utilization)
        # Use ceil(demand / count) to ensure full demand coverage (may overproduce slightly)
        max_reps = max(
            (math.ceil(demand[name] / cnt) for name, cnt in best_pat.part_counts.items()
             if cnt > 0 and demand.get(name, 0) > 0),
            default=1,
        )
        if max_reps > 0:
            selection = [(best_pat, max(1, max_reps))]
        else:
            return None

    elapsed = time.perf_counter() - t0
    result = _build_result(selection, demand, formats, margin, 1, elapsed)
    result.objectives_score = _evaluate_selection(
        selection, demand, obj, total_demand_area, n_fmts, ref_sheet_area, margin,
    )
    return result


def optimize(
    parts: List[PartSpec],
    formats: List[SheetFormat],
    margin: float,
    gap: float,
    budget_s: float,
    force_no_rotate: bool,
    obj: Objectives,
    max_patterns: int = 0,
) -> OptimizationResult:
    """Run optimization with full time budget.

    When rotation is allowed (force_no_rotate=False), _generate_all_patterns
    automatically generates patterns both with and without rotation, so the
    selection always considers the best orientation for each piece.
    This guarantees that enabling rotation can only help, never hurt.
    """
    t0 = time.perf_counter()
    result = _optimize_single_run(
        parts, formats, margin, gap, budget_s,
        force_no_rotate=force_no_rotate, obj=obj, max_patterns=max_patterns, t0=t0,
    )
    if result is None:
        raise RuntimeError(
            "Nepodarilo se vygenerovat zadny vzor rozlozeni. "
            "Zkontroluj, ze se dilce vejdou na alespon jeden format archu."
        )
    return result


# ---------------------------------------------------------------------------
# Visualisation
# ---------------------------------------------------------------------------

def _part_color(name: str) -> Tuple[float, float, float]:
    import colorsys
    return colorsys.hsv_to_rgb((abs(hash(name)) % 360) / 360.0, 0.30, 0.95)


def _dim_label(ax, x1: float, y1: float, x2: float, y2: float, text: str,
               color: str = "#444", fontsize: float = 6.5) -> None:
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    ax.annotate(
        text, xy=(mx, my), fontsize=fontsize, color=color,
        ha="center", va="center",
        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85),
    )


def draw_sheet_figure(
    placements: List[Placement], fmt: SheetFormat,
    pattern_count: int, label: str, margin: float,
) -> plt.Figure:
    fw, fh = fmt.width_cm, fmt.height_cm
    fig, ax = plt.subplots(figsize=(13, 8))
    fig.patch.set_facecolor("#fafbfc")
    ax.set_facecolor("#fafbfc")

    title = f"{label}  -  {fmt.name} ({fw} x {fh} cm)"
    if pattern_count > 1:
        title += f"   [{pattern_count}x opakovat]"
    ax.set_title(title, fontsize=12, fontweight="bold", pad=12, color="#334155")
    ax.set_xlim(-14, fw + 5)
    ax.set_ylim(-5, fh + 5)
    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()

    ax.add_patch(Rectangle((0, 0), fw, fh, fill=True, facecolor="#ffffff",
                            linewidth=2.0, edgecolor="#94a3b8"))
    uw, uh = fw - 2 * margin, fh - 2 * margin
    if margin > 0:
        ax.add_patch(Rectangle((margin, margin), uw, uh,
                                fill=False, edgecolor="#cbd5e1", linewidth=0.7, linestyle="--"))

    _dim_label(ax, 0, -3.0, fw, -3.0, f"{fw:.1f} cm", color="#64748b", fontsize=8)
    ax.plot([0, fw], [-2.0, -2.0], color="#94a3b8", linewidth=0.5)
    ax.plot([0, 0], [-3.2, -0.8], color="#94a3b8", linewidth=0.4)
    ax.plot([fw, fw], [-3.2, -0.8], color="#94a3b8", linewidth=0.4)

    _dim_label(ax, -9.0, 0, -9.0, fh, f"{fh:.1f} cm", color="#64748b", fontsize=8)
    ax.plot([-8.0, -8.0], [0, fh], color="#94a3b8", linewidth=0.5)
    ax.plot([-9.2, -6.8], [0, 0], color="#94a3b8", linewidth=0.4)
    ax.plot([-9.2, -6.8], [fh, fh], color="#94a3b8", linewidth=0.4)

    for pl in placements:
        x, y = margin + pl.x_cm, margin + pl.y_cm
        col = _part_color(pl.part_name)
        ax.add_patch(Rectangle((x, y), pl.width_cm, pl.height_cm,
                                facecolor=col, edgecolor="#64748b", linewidth=0.5, alpha=0.88))

        min_dim = min(pl.width_cm, pl.height_cm)
        if pl.width_cm >= 4 and pl.height_cm >= 3:
            lbl = pl.part_name
            if pl.rotated:
                lbl += " (ot.)"
            fs = max(5.5, min(8.0, min_dim * 0.6))
            ax.text(x + pl.width_cm / 2, y + pl.height_cm * 0.38, lbl,
                    fontsize=fs, ha="center", va="center", color="#334155", fontweight="medium")

        if pl.width_cm >= 4 and pl.height_cm >= 2.5:
            dim_text = f"{pl.width_cm:.1f} x {pl.height_cm:.1f}"
            fs2 = max(4.5, min(6.5, min_dim * 0.45))
            ax.text(x + pl.width_cm / 2, y + pl.height_cm * 0.65, dim_text,
                    fontsize=fs2, ha="center", va="center", color="#64748b")

    # Custom ticks at piece boundaries
    x_edges = sorted(
        {0}
        | {margin + pl.x_cm for pl in placements}
        | {margin + pl.x_cm + pl.width_cm for pl in placements}
    )
    y_edges = sorted(
        {0}
        | {margin + pl.y_cm for pl in placements}
        | {margin + pl.y_cm + pl.height_cm for pl in placements}
    )
    ax.set_xticks(x_edges)
    ax.set_xticklabels([f"{v:.0f}" for v in x_edges], rotation=0, fontsize=7.5, ha="center")
    ax.set_yticks(y_edges)
    ax.set_yticklabels([f"{v:.0f}" for v in y_edges], fontsize=7.5, ha="right")
    ax.tick_params(axis="x", labelsize=7.5, colors="#475569", length=5, width=0.8,
                   top=True, bottom=True, labeltop=True, labelbottom=True)
    ax.tick_params(axis="y", labelsize=7.5, colors="#475569", length=5, width=0.8, labelrotation=0)
    # Grid lines at piece boundaries (more visible than default)
    ax.grid(axis="both", alpha=0.35, linewidth=0.6, color="#94a3b8", linestyle="--")
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def build_pdf(res: OptimizationResult, margin: float, logo: Optional[bytes]) -> bytes:
    if logo is None:
        logo = _load_default_logo()
    buf = io.BytesIO()
    pdf = pdf_canvas.Canvas(buf, pagesize=landscape(A4))
    pw, ph = landscape(A4)

    def head(title: str) -> None:
        # QPV-blue header band (matches qpv.cz)
        pdf.setFillColor(colors.HexColor("#0e3572"))
        pdf.rect(0, ph - 65, pw, 65, stroke=0, fill=1)
        if logo:
            pdf.drawImage(ImageReader(io.BytesIO(logo)), 16, ph - 57, width=72, height=38,
                          preserveAspectRatio=True, mask="auto")
        else:
            pdf.setFillColor(colors.white)
            pdf.setFont(_PDF_FONT_BOLD, 22)
            pdf.drawString(24, ph - 47, "QPV")
        pdf.setFillColor(colors.white)
        pdf.setFont(_PDF_FONT_BOLD, 14)
        pdf.drawString(100, ph - 40, title)
        pdf.setFont(_PDF_FONT, 8)
        pdf.setFillColor(colors.HexColor("#a8c0e8"))
        pdf.drawRightString(pw - 16, ph - 40, "qpv.cz")

    # Summary page
    head("Nařezový plán - souhrn")
    pdf.setFillColor(colors.HexColor("#334155"))
    pdf.setFont(_PDF_FONT, 10)
    y0 = ph - 82
    pdf.drawString(20, y0,      f"Celkem archů: {len(res.sheet_results)}")
    pdf.drawString(20, y0 - 16, f"Výtěžnost materiálu: {res.utilization_ratio * 100.0:.2f} %")
    pdf.drawString(280, y0,      f"Použitých formátů: {res.formats_used}")
    pdf.drawString(280, y0 - 16, f"Odhadovaný počet řezů: {res.total_cuts}")
    pdf.drawString(280, y0 - 32, f"Otestováno kombinací: {res.attempts}")
    pdf.drawString(20, y0 - 52, f"Doba výpočtu: {res.elapsed_sec:.1f} s")

    y_off = y0 - 78
    pdf.setFillColor(colors.HexColor("#1e3a5f"))
    pdf.setFont(_PDF_FONT_BOLD, 10)
    pdf.drawString(20, y_off, "Přehled rozložení:")
    y_off -= 18
    pdf.setFillColor(colors.HexColor("#334155"))
    pdf.setFont(_PDF_FONT, 9)
    for pat_idx, (pat, cnt) in enumerate(res.patterns_used):
        parts_desc = ", ".join(f"{v}x {k}" for k, v in sorted(pat.part_counts.items()))
        pdf.drawString(28, y_off,
                       f"Vzor {pat_idx + 1}: {cnt}x arch '{pat.fmt.name}' "
                       f"({pat.fmt.width_cm} x {pat.fmt.height_cm} cm) - {parts_desc}")
        y_off -= 15
        if y_off < 40:
            pdf.showPage()
            head("Nařezový plán - souhrn (pokračování)")
            y_off = ph - 82
    pdf.showPage()

    # One page per pattern
    for pat_idx, (pat, cnt) in enumerate(res.patterns_used):
        label = f"Vzor {pat_idx + 1}"
        head(f"{label} / {len(res.patterns_used)}  ({cnt}x)")
        fig = draw_sheet_figure(pat.placements, pat.fmt, cnt, label, margin)
        img = io.BytesIO()
        fig.savefig(img, format="png", dpi=170, bbox_inches="tight")
        plt.close(fig)
        img.seek(0)
        pdf.drawImage(ImageReader(img), 16, 20, width=pw - 32, height=ph - 110,
                      preserveAspectRatio=True, mask="auto")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def run_benchmark(parts: List[PartSpec], formats: List[SheetFormat], margin: float, gap: float,
                  force_no_rotate: bool, obj: Objectives, max_patterns: int = 0) -> List[Dict]:
    rows = []
    for b in [10, 60, 300]:
        r = optimize(parts, formats, margin, gap, float(b), force_no_rotate, obj, max_patterns)
        rows.append({
            "Cas (s)": b,
            "Archu": len(r.sheet_results),
            "Vzoru": len(r.patterns_used),
            "Vytizeni (%)": round(r.utilization_ratio * 100.0, 2),
            "Formatu": r.formats_used,
            "Rezu": r.total_cuts,
            "Pokusu": r.attempts,
        })
    return rows


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

_CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"], .stApp {
        font-family: 'Poppins', sans-serif !important;
    }

    /* ---- Header band ---- */
    .qpv-header {
        background: #0e3572;
        padding: 18px 28px;
        border-radius: 10px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        gap: 24px;
    }
    .qpv-header h1 {
        color: #ffffff !important;
        margin: 0;
        font-size: 1.75rem;
        font-weight: 600;
        letter-spacing: 0.01em;
    }
    .qpv-header p {
        color: #a8c0e8 !important;
        margin: 4px 0 0 0;
        font-size: 0.92rem;
    }

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"] {
        background: #efefef !important;
        border-right: 3px solid #0e3572;
    }
    [data-testid="stSidebarContent"] {
        padding-top: 0.25rem !important;
    }

    /* ---- Hide Deploy toolbar ---- */
    [data-testid="stToolbar"] {
        display: none !important;
    }

    /* ---- Remove top padding from main content ---- */
    [data-testid="stMainBlockContainer"] {
        padding-top: 0.5rem !important;
    }
    [data-testid="stSidebar"] * { color: #1a1a1a !important; font-size: 0.875rem !important; }
    [data-testid="stSidebar"] .sidebar-section { font-size: 0.85rem !important; }
    [data-testid="stSidebar"] label { font-size: 0.875rem !important; }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4 {
        color: #0e3572 !important;
        font-weight: 600;
    }
    [data-testid="stSidebar"] hr { border-color: #c8c8c8 !important; }

    /* ---- Metrics ---- */
    div[data-testid="metric-container"] {
        background: #f4f7fc;
        border: 1px solid #c5d3e8;
        border-top: 3px solid #0e3572;
        border-radius: 8px;
        padding: 14px 18px;
    }
    div[data-testid="stMetricValue"] { color: #0e3572 !important; font-weight: 700; font-size: 1.6rem; }
    div[data-testid="stMetricLabel"] { color: #444444 !important; font-size: 0.82rem; font-weight: 500; }

    /* ---- Primary button (QPV blue) ---- */
    .stButton > button[kind="primary"] {
        background: #0e3572 !important;
        color: #ffffff !important;
        border: none;
        border-radius: 6px;
        font-weight: 600;
        font-size: 0.97rem;
        letter-spacing: 0.02em;
        padding: 10px 20px;
    }
    .stButton > button[kind="primary"]:hover {
        background: #0b2a5c !important;
        box-shadow: 0 3px 10px rgba(14,53,114,0.35);
    }
    .stButton > button[kind="secondary"] {
        border: 1.5px solid #0e3572 !important;
        color: #0e3572 !important;
        border-radius: 6px;
        font-weight: 500;
    }
    .stButton > button[kind="secondary"]:hover {
        background: #e8eef8 !important;
    }

    /* ---- Info / info box ---- */
    div[data-testid="stInfo"] {
        border-left: 4px solid #0e3572;
        background: #edf2fb;
    }
    div[data-testid="stSuccess"] {
        border-left: 4px solid #0e6e3b;
    }

    /* ---- Expander ---- */
    div[data-testid="stExpander"] {
        border: 1px solid #c5d3e8;
        border-radius: 8px;
        background: #f8fafd;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 500;
        color: #0e3572 !important;
    }

    /* ---- Dividers ---- */
    hr { border-color: #dde4ef !important; }

    /* ---- Input fields ---- */
    .stTextArea textarea, .stNumberInput input, .stTextInput input {
        background: #ffffff !important;
        color: #1a1a1a !important;
        border: 1.5px solid #c5d3e8 !important;
        border-radius: 6px !important;
    }
    .stTextArea textarea:focus, .stNumberInput input:focus {
        border-color: #0e3572 !important;
        box-shadow: 0 0 0 2px rgba(14,53,114,0.12) !important;
    }

    /* ---- Info banner ---- */
    /* ---- Hide slider tick bar ---- */
    [data-testid="stSliderTickBar"] {
        display: none !important;
    }

    /* ---- Align sidebar top with main content ---- */
    [data-testid="stLogoSpacer"] {
        display: none !important;
    }
    [data-testid="stSidebarHeader"] {
        min-height: 0 !important;
        height: auto !important;
        padding: 0 !important;
    }

    /* ---- Sidebar section headers ---- */
    .sidebar-section {
        background: #0e3572;
        color: #ffffff !important;
        font-size: 0.85rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        padding: 6px 12px;
        border-radius: 6px;
        margin-top: 8px;
        margin-bottom: 4px;
    }

    /* ---- Hide heading anchor links ---- */
    [data-testid="stMarkdownContainer"] h5 a,
    [data-testid="stMarkdownContainer"] h4 a,
    [data-testid="stMarkdownContainer"] h3 a { display: none !important; }

    /* circle buttons styled via JS */

    /* ---- Custom tooltip for ? icons next to headings ---- */
    .qpv-tip {
        position: relative;
        cursor: help;
        display: inline-flex;
        vertical-align: middle;
        margin-left: 4px;
        color: #666;
    }
    .qpv-tip::after {
        content: attr(data-tip);
        position: absolute;
        bottom: calc(100% + 8px);
        left: 50%;
        transform: translateX(-50%);
        background: #ffffff;
        color: rgb(26, 26, 26);
        border: 1px solid rgba(0, 0, 0, 0.1);
        box-shadow: rgba(0, 0, 0, 0.16) 0px 1px 4px 0px;
        padding: 8px 12px;
        border-radius: 8px;
        font-size: 0.875rem;
        font-weight: 400;
        line-height: 1.5;
        width: max-content;
        max-width: 280px;
        white-space: normal;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.15s;
        z-index: 9999;
    }
    .qpv-tip:hover::after { opacity: 1; }

    /* ---- Slider label in main area — bigger font ---- */
    [data-testid="stMainBlockContainer"] [data-testid="stSlider"] [data-testid="stWidgetLabel"] p {
        font-size: 1.05rem !important;
        font-weight: 600 !important;
        color: #1a1a1a !important;
    }

    .qpv-info-bar {
        background: #e8eef8;
        border-left: 4px solid #0e3572;
        border-radius: 0 6px 6px 0;
        padding: 10px 16px;
        color: #1a1a1a;
        font-size: 0.92rem;
        margin-bottom: 10px;
    }
</style>
"""


def main() -> None:
    st.set_page_config(
        page_title="QPV - Nařezový plán",
        page_icon="https://qpv.cz/favicon.ico",
        layout="wide",
    )
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

    # JS: style ＋/－ buttons as circles matching the table border, reduce gap below tables
    st_components.html("""
    <script>
    (function() {
        var doc = window.parent.document;
        var BTN_STYLE = [
            'width:32px!important', 'height:32px!important', 'min-height:32px!important',
            'border-radius:50%!important', 'padding:0!important',
            'background:#ffffff!important',
            'border:1px solid rgba(26,26,26,0.25)!important',
            'color:rgba(26,26,26,0.6)!important',
            'font-size:1.1rem!important', 'font-weight:400!important',
            'line-height:1!important', 'box-shadow:none!important'
        ].join(';');

        function styleAddBtns() {
            doc.querySelectorAll('button[data-testid="stBaseButton-secondary"]').forEach(function(btn) {
                var code = btn.textContent.trim().charCodeAt(0);
                if (code === 65291 || code === 65293) { // ＋ or －
                    btn.style.cssText += ';' + BTN_STYLE;
                    btn.onmouseenter = function() {
                        btn.style.background = '#f0f2f6';
                        btn.style.borderColor = 'rgba(26,26,26,0.5)';
                        btn.style.color = 'rgba(26,26,26,0.9)';
                    };
                    btn.onmouseleave = function() {
                        btn.style.background = '#ffffff';
                        btn.style.borderColor = 'rgba(26,26,26,0.25)';
                        btn.style.color = 'rgba(26,26,26,0.6)';
                    };
                }
            });

            // Reduce gap between table and the buttons row below it
            doc.querySelectorAll('[data-testid="stFullScreenFrame"]').forEach(function(frame) {
                var elContainer = frame.closest('[data-testid="stElementContainer"]');
                if (!elContainer) return;
                var next = elContainer.nextElementSibling;
                if (next && next.dataset.testid === 'stElementContainer') {
                    next.style.marginTop = '-10px';
                }
            });

            // Move slider help (?) icon right next to the label text instead of far right
            doc.querySelectorAll('[data-testid="stSlider"]').forEach(function(slider) {
                var label = slider.querySelector('[data-testid="stWidgetLabel"]');
                if (!label) return;
                var p = label.querySelector('p');
                var tooltipIcon = label.querySelector('[data-testid="stTooltipIcon"]');
                if (p && tooltipIcon && !p.contains(tooltipIcon)) {
                    tooltipIcon.style.display = 'inline-flex';
                    tooltipIcon.style.verticalAlign = 'middle';
                    tooltipIcon.style.marginLeft = '4px';
                    p.appendChild(tooltipIcon);
                }
            });
        }

        styleAddBtns();
        var obs = new MutationObserver(styleAddBtns);
        obs.observe(doc.body, { childList: true, subtree: true });
        setTimeout(function() { obs.disconnect(); }, 30000);
    })();
    </script>
    """, height=0)

    default_logo = _load_default_logo()


    # ======================== HEADER ========================
    logo_b64 = ""
    if default_logo:
        logo_b64 = base64.b64encode(default_logo).decode()

    logo_html = (
        f"<div style='background:#ffffff; border-radius:8px; padding:6px 10px; "
        f"display:inline-flex; align-items:center;'>"
        f"<img src='data:image/png;base64,{logo_b64}' style='height:44px;'>"
        f"</div>"
        if logo_b64 else ""
    )
    st.markdown(
        f"""<div class="qpv-header">
            {logo_html}
            <div>
                <h1>Nařezový plán</h1>
                <p style="margin:0; line-height:1.7; opacity:0.92;">
                    <b>1.</b> Zadejte dílce &mdash; co chcete nařezat a v jakém počtu &nbsp;&nbsp;
                    <b>2.</b> Přidejte archy &mdash; z jakého materiálu budete řezat &nbsp;&nbsp;
                    <b>3.</b> Nastavte, jak dlouho má program hledat &nbsp;&nbsp;
                    <b>4.</b> Stiskněte <b>Spustit optimalizaci</b>
                </p>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Session state pro řádky tabulek — výchozí jeden prázdný řádek
    # Test mode: ?test=scenario_name pre-fills data for automated testing
    _test_scenario = st.query_params.get("test", "")
    _TEST_SCENARIOS = {
        "simple": {
            "parts": [
                {"Název": "DilecA", "Šířka (cm)": 50.0, "Výška (cm)": 30.0, "Počet kusů": 20, "Lze otočit": False},
                {"Název": "DilecB", "Šířka (cm)": 40.0, "Výška (cm)": 25.0, "Počet kusů": 15, "Lze otočit": False},
            ],
            "formats": [
                {"Název": "Standard", "Šířka (cm)": 200.0, "Výška (cm)": 150.0, "Dostupné množství (0 = ∞)": 0},
            ],
        },
        "complex5": {
            "parts": [
                {"Název": "zada", "Šířka (cm)": 55.0, "Výška (cm)": 40.0, "Počet kusů": 20, "Lze otočit": False},
                {"Název": "sedak", "Šířka (cm)": 50.0, "Výška (cm)": 50.0, "Počet kusů": 20, "Lze otočit": False},
                {"Název": "operk", "Šířka (cm)": 60.0, "Výška (cm)": 52.0, "Počet kusů": 20, "Lze otočit": False},
                {"Název": "boky", "Šířka (cm)": 70.0, "Výška (cm)": 30.0, "Počet kusů": 20, "Lze otočit": False},
                {"Název": "pod_p", "Šířka (cm)": 26.0, "Výška (cm)": 12.0, "Počet kusů": 20, "Lze otočit": False},
            ],
            "formats": [
                {"Název": "Velky", "Šířka (cm)": 200.0, "Výška (cm)": 140.0, "Dostupné množství (0 = ∞)": 0},
            ],
        },
        "single100": {
            "parts": [
                {"Název": "Panel", "Šířka (cm)": 80.0, "Výška (cm)": 60.0, "Počet kusů": 100, "Lze otočit": False},
            ],
            "formats": [
                {"Název": "Arch", "Šířka (cm)": 250.0, "Výška (cm)": 200.0, "Dostupné množství (0 = ∞)": 0},
            ],
        },
    }
    if _test_scenario in _TEST_SCENARIOS and st.session_state.get("_test_loaded") != _test_scenario:
        sc = _TEST_SCENARIOS[_test_scenario]
        st.session_state["parts_rows"] = pd.DataFrame(sc["parts"])
        st.session_state["formats_rows"] = pd.DataFrame(sc["formats"])
        st.session_state["parts_editor_v"] = st.session_state.get("parts_editor_v", 0) + 1
        st.session_state["formats_editor_v"] = st.session_state.get("formats_editor_v", 0) + 1
        st.session_state["_test_loaded"] = _test_scenario
    if "parts_rows" not in st.session_state:
        st.session_state["parts_rows"] = pd.DataFrame(
            [{"Název": "", "Šířka (cm)": None, "Výška (cm)": None, "Počet kusů": None, "Lze otočit": False}]
        )
    if "formats_rows" not in st.session_state:
        st.session_state["formats_rows"] = pd.DataFrame(
            [{"Název": "", "Šířka (cm)": None, "Výška (cm)": None, "Dostupné množství (0 = ∞)": None}]
        )

    # ======================== SIDEBAR ========================
    with st.sidebar:
        st.markdown("<div class='sidebar-section'>Nastavení řezu</div>", unsafe_allow_html=True)
        st.caption("Nastavte, kolik místa se na archu ztratí u okrajů a jak velká mezera bude mezi jednotlivými dílci. Program tyto hodnoty zohlední při výpočtu — výsledné rozložení pak bude přesně odpovídat tomu, co stroj skutečně zvládne.")

        margin = st.number_input(
            "Okraj archu (cm)", min_value=0.0, value=0.0, step=0.1,
            help=(
                "Část archu kolem dokola, kam stroj nesmí sáhnout. "
                "Tato plocha se odečte ze všech čtyř stran před tím, než program začne dílce rozmísťovat.\n\n"
                "Příklad: arch 100 × 140 cm s okrajem 1 cm → program pracuje jen s plochou 98 × 138 cm."
            ),
        )
        gap = st.number_input(
            "Mezera mezi dílci (cm)", min_value=0.0, value=0.0, step=0.1,
            help=(
                "Prostor mezi sousedními dílci na archu — program ho rezervuje pro každý řez. "
                "Větší mezera znamená více ztráty plochy.\n\n"
                "Příklad: mezera 0,3 cm při 30 řezech = 9 cm plochy navíc spotřebovaných řezy."
            ),
        )

        st.divider()
        st.markdown("<div class='sidebar-section'>Pravidla pro řezání</div>", unsafe_allow_html=True)
        st.caption(
            "Nastavte, co program při sestavování plánu smí a nesmí. "
            "Tato pravidla ovlivňují, jak flexibilně může dílce rozmísťovat "
            "a kolik různých variant nařezání vznikne."
        )
        allow_rotate = st.checkbox(
            "Povolit otáčení dílců na archu",
            value=False,
            help=(
                "Určuje, zda program smí otočit dílec o 90°, aby se lépe vešel na arch. "
                "Při povolení otáčení program najde lepší využití plochy.\n\n"
                "Příklad: dílec 30 × 60 cm může být umístěn i jako 60 × 30 cm — "
                "záleží na tom, co se lépe hodí."
            ),
        )
        force_no_rotate = not allow_rotate
        max_patterns = st.number_input(
            "Maximální počet variant nařezání (0 = bez omezení)",
            min_value=0, max_value=50, value=0, step=1,
            help=(
                "Určuje, kolik různých způsobů rozmístění může výsledný plán obsahovat. "
                "Každá varianta se pak opakuje tolikrát, kolik je potřeba.\n\n"
                "Příklad: hodnota 2 = vzniknou nejvýše 2 různé varianty nařezání. "
                "Hodnota 0 = program si počet určí sám."
            ),
        )
        if max_patterns > 0:
            word = "variantu" if max_patterns == 1 else ("varianty" if max_patterns < 5 else "variant")
            st.info(
                f"Aktivní limit: max. **{max_patterns}** {word} nařezání. "
                f"Může vést k mírně horším výsledkům.",
                icon="ℹ️",
            )

        st.divider()
        st.markdown("<div class='sidebar-section'>Co optimalizovat</div>", unsafe_allow_html=True)
        st.caption(
            "Posuňte posuvníky podle toho, co chcete optimalizovat. "
            "Součet se automaticky přepočítá na 100 %."
        )
        w_waste = st.slider(
            "Co nejmenší odpad", 0.0, 1.0, 0.50, 0.05,
            help=(
                "Určuje, jak moc se program snaží využít každý arch do posledního centimetru. "
                "Čím vyšší hodnota, tím méně materiálu skončí jako odpad. "
                "Příklad: při výtěžnosti 95 % se z každého archu skutečně vyřeže 95 % plochy. "
                "Nevýhoda: při velmi vysoké hodnotě může program upřednostnit složitější rozložení nebo dražší arch. "
                "Pokud zároveň omezíte počet variant nařezání, platí toto: program nejprve dodrží limit variant jako pevnou podmínku "
                "a teprve v jeho rámci hledá rozložení s co nejmenším odpadem."
            ),
        )
        w_sheets = st.slider(
            "Co nejméně archů", 0.0, 1.0, 0.25, 0.05,
            help=(
                "Určuje, jak moc se program snaží použít co nejméně archů celkem. "
                "Méně archů = nižší spotřeba materiálu. "
                "Nevýhoda: program může zvolit složitější rozložení nebo přijmout větší odpad, "
                "aby snížil počet archů. "
                "Pokud zároveň omezíte počet variant nařezání, program nejprve dodrží tento limit "
                "a teprve v jeho rámci minimalizuje počet archů."
            ),
        )
        w_cuts = st.slider(
            "Méně řezů", 0.0, 1.0, 0.00, 0.05,
            help=(
                "Určuje, jak moc se program snaží snížit celkový počet řezů na každém archu. "
                "Méně řezů = jednodušší a rychlejší zpracování na stroji. "
                "Nevýhoda: program může přijmout větší odpad nebo použít více archů, "
                "aby dosáhl jednodušších rozložení. "
                "Pokud omezíte počet variant nařezání, program ho dodrží jako pevnou podmínku "
                "a počet řezů optimalizuje až v jeho rámci."
            ),
        )
        w_cost = 0.0
        w_formats = 0.0
        obj = Objectives(w_cost=w_cost, w_sheets=w_sheets, w_waste=w_waste,
                         w_cuts=w_cuts, w_formats=w_formats)

        pct = obj.pct()
        total_set = w_sheets + w_waste + w_cuts
        if total_set > 0:
            st.caption("**Rozdělení priorit:**")
            ordered = [
                ("Odpad (vyteznost)", "Odpad"),
                ("Pocet archu", "Počet archů"),
                ("Pocet rezu", "Řezy"),
            ]
            for key, nice in ordered:
                p = pct.get(key, 0)
                if p > 0:
                    st.progress(int(p), text=f"{nice}: {int(p)} %")
        else:
            st.warning("Nastavte alespoň jeden cíl (posuňte některý posuvník doprava).")

        logo_bytes = default_logo

    # ======================== MAIN AREA ========================
    col_parts, col_fmt = st.columns(2)

    _PARTS_COLS = {
        "Název": st.column_config.TextColumn("Název", help="Název nebo označení dílce.", width="medium"),
        "Šířka (cm)": st.column_config.NumberColumn("Šířka (cm)", min_value=0.1, step=0.1, help="Šířka dílce v cm.", width=90),
        "Výška (cm)": st.column_config.NumberColumn("Výška (cm)", min_value=0.1, step=0.1, help="Výška dílce v cm.", width=90),
        "Počet kusů": st.column_config.NumberColumn("Ks", min_value=1, step=1, help="Celkový počet kusů.", width=60),
        "Lze otočit": st.column_config.CheckboxColumn("Otočit ⓘ", help="Program smí otočit dílec o 90°, což může zlepšit využití plochy archu.", width=80),
    }
    _FMTS_COLS = {
        "Název": st.column_config.TextColumn("Název", help="Označení formátu archu.", width="small"),
        "Šířka (cm)": st.column_config.NumberColumn("Šířka (cm)", min_value=0.1, step=0.1, help="Šířka archu v cm.", width=90),
        "Výška (cm)": st.column_config.NumberColumn("Výška (cm)", min_value=0.1, step=0.1, help="Výška archu v cm.", width=90),
        "Dostupné množství (0 = ∞)": st.column_config.NumberColumn("Počet (0 = ∞) ⓘ", min_value=0, step=1, help="Kolik archů tohoto formátu máte k dispozici. Hodnota 0 znamená neomezené množství.", width="small"),
    }

    if "parts_editor_v" not in st.session_state:
        st.session_state["parts_editor_v"] = 0
    if "formats_editor_v" not in st.session_state:
        st.session_state["formats_editor_v"] = 0

    with col_parts:
        st.markdown(
            "##### Co chcete nařezat "
            "<span class='qpv-tip' data-tip='Každý typ dílce na jeden řádek — název, rozměry a počet kusů.'>"
            "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' "
            "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
            "<circle cx='12' cy='12' r='10'/>"
            "<path d='M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3'/>"
            "<line x1='12' y1='17' x2='12.01' y2='17'/>"
            "</svg></span>",
            unsafe_allow_html=True,
        )
        parts_df = st.data_editor(
            st.session_state["parts_rows"],
            num_rows="fixed",
            use_container_width=True,
            hide_index=True,
            column_config=_PARTS_COLS,
            key=f"parts_editor_{st.session_state['parts_editor_v']}",
        )
        _, _pc, _pd, _ = st.columns([2, 1, 1, 2])
        with _pc:
            if st.button("＋", key="add_part_row", help="Přidat nový dílec", use_container_width=True):
                new_row = pd.DataFrame([{"Název": "", "Šířka (cm)": float("nan"), "Výška (cm)": float("nan"), "Počet kusů": float("nan"), "Lze otočit": False}])
                combined = pd.concat([parts_df, new_row], ignore_index=True)
                combined["Šířka (cm)"] = pd.to_numeric(combined["Šířka (cm)"], errors="coerce")
                combined["Výška (cm)"] = pd.to_numeric(combined["Výška (cm)"], errors="coerce")
                combined["Počet kusů"] = pd.to_numeric(combined["Počet kusů"], errors="coerce")
                st.session_state["parts_rows"] = combined
                st.session_state["parts_editor_v"] += 1
                st.rerun()
        with _pd:
            if st.button("－", key="del_part_row", help="Odebrat poslední řádek", use_container_width=True):
                if len(parts_df) > 1:
                    trimmed = parts_df.iloc[:-1].reset_index(drop=True)
                    trimmed["Šířka (cm)"] = pd.to_numeric(trimmed["Šířka (cm)"], errors="coerce")
                    trimmed["Výška (cm)"] = pd.to_numeric(trimmed["Výška (cm)"], errors="coerce")
                    trimmed["Počet kusů"] = pd.to_numeric(trimmed["Počet kusů"], errors="coerce")
                    st.session_state["parts_rows"] = trimmed
                    st.session_state["parts_editor_v"] += 1
                    st.rerun()
                else:
                    st.toast("Musí zůstat alespoň jeden řádek.", icon="⚠️")

    with col_fmt:
        st.markdown(
            "##### Z čeho budete řezat "
            "<span class='qpv-tip' data-tip='Formáty materiálu, které máte k dispozici. Lze zadat více — program vybere nejvýhodnější kombinaci.'>"
            "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' "
            "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
            "<circle cx='12' cy='12' r='10'/>"
            "<path d='M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3'/>"
            "<line x1='12' y1='17' x2='12.01' y2='17'/>"
            "</svg></span>",
            unsafe_allow_html=True,
        )
        formats_df = st.data_editor(
            st.session_state["formats_rows"],
            num_rows="fixed",
            use_container_width=True,
            hide_index=True,
            column_config=_FMTS_COLS,
            key=f"formats_editor_{st.session_state['formats_editor_v']}",
        )
        _, _fc, _fd, _ = st.columns([2, 1, 1, 2])
        with _fc:
            if st.button("＋", key="add_fmt_row", help="Přidat nový formát archu", use_container_width=True):
                new_row = pd.DataFrame([{"Název": "", "Šířka (cm)": float("nan"), "Výška (cm)": float("nan"), "Dostupné množství (0 = ∞)": float("nan")}])
                combined = pd.concat([formats_df, new_row], ignore_index=True)
                combined["Šířka (cm)"] = pd.to_numeric(combined["Šířka (cm)"], errors="coerce")
                combined["Výška (cm)"] = pd.to_numeric(combined["Výška (cm)"], errors="coerce")
                combined["Dostupné množství (0 = ∞)"] = pd.to_numeric(combined["Dostupné množství (0 = ∞)"], errors="coerce")
                st.session_state["formats_rows"] = combined
                st.session_state["formats_editor_v"] += 1
                st.rerun()
        with _fd:
            if st.button("－", key="del_fmt_row", help="Odebrat poslední řádek", use_container_width=True):
                if len(formats_df) > 1:
                    trimmed = formats_df.iloc[:-1].reset_index(drop=True)
                    trimmed["Šířka (cm)"] = pd.to_numeric(trimmed["Šířka (cm)"], errors="coerce")
                    trimmed["Výška (cm)"] = pd.to_numeric(trimmed["Výška (cm)"], errors="coerce")
                    trimmed["Dostupné množství (0 = ∞)"] = pd.to_numeric(trimmed["Dostupné množství (0 = ∞)"], errors="coerce")
                    st.session_state["formats_rows"] = trimmed
                    st.session_state["formats_editor_v"] += 1
                    st.rerun()
                else:
                    st.toast("Musí zůstat alespoň jeden řádek.", icon="⚠️")

    # ---- čas hledání ----
    st.markdown("<div style='margin-top:1.5rem'></div>", unsafe_allow_html=True)
    budget_m = st.slider(
        "Jak dlouho má program hledat? (minuty)",
        min_value=1, max_value=10, value=2, step=1,
        help=(
            "Více času = program vyzkouší více variant rozložení dílců na arch. "
            "Pro jednoduché zakázky (1-3 typy) stačí 1-2 min."
        ),
    )
    _test_budget = st.query_params.get("budget", "")
    budget_s = min(int(_test_budget), 600) if _test_budget.isdigit() else budget_m * 60
    if budget_m <= 2:
        hint = "Dostatečné pro jednoduché zakázky (1-3 typy dílců)."
    elif budget_m <= 5:
        hint = "Doporučeno pro zakázky s více typy dílců."
    else:
        hint = "Maximální rozmanitost variant — pro velké nebo velmi složité zakázky."
    st.caption(f"**{budget_m} min** — {hint}")

    # ---- validate ----
    parts_has_data = not parts_df.empty and (parts_df["Název"].astype(str).str.strip() != "").any()
    formats_has_data = not formats_df.empty and (formats_df["Název"].astype(str).str.strip() != "").any()
    ready = parts_has_data and formats_has_data

    # ---- run buttons ----
    do_run = st.button(
        "Spustit optimalizaci", type="primary", use_container_width=True,
        disabled=not ready,
    )

    if not ready:
        return

    try:
        parts = df_to_parts(parts_df)
        formats = df_to_formats(formats_df)
    except Exception as exc:
        st.error(f"{exc}")
        return

    total_pcs = sum(p.qty for p in parts)
    safe_fmt_names = ', '.join(html_mod.escape(f.name) for f in formats)
    st.markdown(
        f"<div class='qpv-info-bar'>"
        f"Načteno <b>{len(parts)} typů dílců</b> = <b>{total_pcs:,} ks celkem</b>"
        f"&nbsp;&nbsp;|&nbsp;&nbsp;"
        f"<b>{len(formats)} formátů archů</b>: {safe_fmt_names}"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ---- check for oversized parts ----
    for p in parts:
        fits_any = False
        for f in formats:
            uw = f.width_cm - 2 * margin
            uh = f.height_cm - 2 * margin
            if (p.width_cm <= uw and p.height_cm <= uh) or (
                p.rotatable and p.height_cm <= uw and p.width_cm <= uh
            ):
                fits_any = True
                break
        if not fits_any:
            st.warning(
                f"Dílec **{html_mod.escape(p.name)}** ({p.width_cm} x {p.height_cm} cm) se nevejde "
                f"na žádný dostupný arch! Optimalizace tento dílec přeskočí.",
                icon="⚠️",
            )

    if do_run:
        status = st.status(f"Hledám nejlepší rozložení (max. {budget_s} s) …", expanded=True)
        with status:
            try:
                st.write(f"Časový limit: **{budget_s} s** — čím déle, tím lepší výsledek.")
                st.session_state.result_q = optimize(
                    parts, formats, margin, gap, float(budget_s), force_no_rotate, obj,
                    max_patterns=int(max_patterns),
                )
                status.update(label="Optimalizace dokončena!", state="complete")
            except Exception as exc:
                status.update(label="Chyba při optimalizaci", state="error")
                st.error(f"{exc}")

    # ======================== RESULTS ========================
    res: Optional[OptimizationResult] = st.session_state.get("result_q")
    if res is None:
        return

    st.markdown("---")
    st.markdown(
        "<h2 style='color:#0e3572; font-weight:700; border-bottom:3px solid #0e3572; "
        "padding-bottom:6px;'>Výsledek optimalizace</h2>",
        unsafe_allow_html=True,
    )

    if max_patterns > 0:
        n_used = len(res.patterns_used)
        st.info(
            f"Použito **{n_used}** z max. **{max_patterns}** variant nařezání.",
            icon="ℹ️",
        )

    if len(formats) > 1:
        used = sorted({pat.fmt.name for pat, _ in res.patterns_used})
        all_names = sorted({f.name for f in formats})
        if set(used) == set(all_names):
            st.info(
                f"Nejlepší výsledek při použití všech formátů ({', '.join(used)}).",
                icon="ℹ️",
            )
        else:
            not_used = sorted(set(all_names) - set(used))
            st.success(
                f"Pro tuto zakázku stačí použít pouze **{', '.join(used)}**. "
                f"Formát {', '.join(not_used)} není potřeba.",
                icon="💡",
            )

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Celkem archů", f"{len(res.sheet_results)}")
    m2.metric("Různých vzorů", f"{len(res.patterns_used)}")
    m3.metric("Výtěžnost", f"{res.utilization_ratio * 100.0:.1f} %")
    m4.metric("Doba výpočtu", f"{res.elapsed_sec:.1f} s")

    st.caption(
        f"Otestováno {res.attempts:,} kombinací  "
        f"|  Dolní mez: {res.lower_bound_sheets} archů"
    )

    # --- Demand coverage ---
    produced: Dict[str, int] = {}
    for pat, cnt in res.patterns_used:
        for name, pc in pat.part_counts.items():
            produced[name] = produced.get(name, 0) + pc * cnt
    demand_rows = []
    for p in parts:
        prod = produced.get(p.name, 0)
        diff = prod - p.qty
        status = "OK" if diff == 0 else (f"+{diff} navíc" if diff > 0 else f"chybí {-diff}")
        demand_rows.append({
            "Dílec": p.name,
            "Potřeba": f"{p.qty} ks",
            "Vyrobeno": f"{prod} ks",
            "Stav": status,
        })
    st.markdown("##### Pokrytí poptávky")
    st.dataframe(demand_rows, use_container_width=True, hide_index=True)

    missing_parts = [r for r in demand_rows if "chybí" in r["Stav"]]
    if missing_parts:
        for r in missing_parts:
            st.error(
                f"POZOR: dílec **{html_mod.escape(r['Dílec'])}** — {html_mod.escape(r['Stav'])}! "
                f"Plán nepokrývá celou poptávku.",
                icon="🚨",
            )

    # --- Pattern summary ---
    st.markdown("##### Přehled rozložení")
    st.caption(
        f"Celkem **{len(res.sheet_results)} archů** rozděleno do "
        f"**{len(res.patterns_used)} různých vzorů**. "
        f"Každý vzor se opakuje tolikrát, kolik je potřeba."
    )

    summary_rows = []
    for pat_idx, (pat, cnt) in enumerate(res.patterns_used):
        parts_desc = ", ".join(f"{v}x {k}" for k, v in sorted(pat.part_counts.items()))
        summary_rows.append({
            "Vzor": f"#{pat_idx + 1}",
            "Arch": pat.fmt.name,
            "Rozměr": f"{pat.fmt.width_cm} x {pat.fmt.height_cm} cm",
            "Opakování": f"{cnt}x",
            "Dílců na arch": pat.total_items,
            "Výtěžnost": f"{pat.utilization * 100:.1f} %",
            "Obsah": parts_desc,
        })
    st.dataframe(summary_rows, use_container_width=True, hide_index=True)

    # --- Pattern visualisations ---
    st.markdown("##### Nákresy vzorů")
    for pat_idx, (pat, cnt) in enumerate(res.patterns_used):
        label = f"Vzor #{pat_idx + 1}"
        parts_short = ", ".join(f"{v}x {k}" for k, v in sorted(pat.part_counts.items()))
        exp_title = (
            f"{label}  —  {pat.fmt.name} ({pat.fmt.width_cm} x {pat.fmt.height_cm} cm)"
            f"  —  {cnt}x opakovat  —  {parts_short}"
        )
        with st.expander(exp_title, expanded=(len(res.patterns_used) <= 5)):
            fig = draw_sheet_figure(pat.placements, pat.fmt, cnt, label, margin)
            st.pyplot(fig, clear_figure=False)
            plt.close(fig)

    # --- PDF export ---
    st.markdown("##### Export do PDF")
    col_dl, col_info = st.columns([2, 3])
    with col_dl:
        st.download_button(
            "Stáhnout PDF report",
            data=build_pdf(res, margin, logo_bytes),
            file_name="qpv-narezovy-plan.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with col_info:
        st.caption(
            "PDF obsahuje souhrnnou stránku a detailní nákres "
            "každého vzoru rozložení včetně rozměrů."
        )



if __name__ == "__main__":
    main()
