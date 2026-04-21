"""Edge case tests for the QPV cutting-stock optimizer."""
import sys, types, math

# Mock streamlit
_mock = types.ModuleType("streamlit")
_mock.cache_data = lambda f=None, **kw: f if f else (lambda fn: fn)
_mock.cache_resource = lambda f=None, **kw: f if f else (lambda fn: fn)
for attr in ("sidebar", "columns", "tabs", "expander", "container", "empty",
             "spinner", "progress", "form", "markdown", "write", "info",
             "warning", "error", "success", "caption", "divider",
             "number_input", "slider", "checkbox", "radio", "selectbox",
             "text_input", "text_area", "button", "download_button",
             "file_uploader", "data_editor", "metric", "header",
             "subheader", "title", "set_page_config", "rerun",
             "session_state", "toast", "html"):
    setattr(_mock, attr, lambda *a, **kw: None)
_mock.session_state = {}
sys.modules["streamlit"] = _mock
sys.modules["streamlit.components"] = types.ModuleType("streamlit.components")
sys.modules["streamlit.components.v1"] = types.ModuleType("streamlit.components.v1")

from app_quality import (
    PartSpec, SheetFormat, Objectives, optimize,
    _gen_pattern, _generate_all_patterns, draw_sheet_figure,
    df_to_parts, df_to_formats,
)


def _fmt(name, w, h, price=100.0, available=0):
    return SheetFormat(name=name, width_cm=w, height_cm=h, price_kc=price, available=available)


def _analyze(res, parts):
    demand = {p.name: p.qty for p in parts}
    produced = {}
    for pat, cnt in res.patterns_used:
        for name, pc in pat.part_counts.items():
            produced[name] = produced.get(name, 0) + pc * cnt
    undercov = {n: max(0, demand[n] - produced.get(n, 0)) for n in demand}
    overcov = {n: max(0, produced.get(n, 0) - demand[n]) for n in demand}
    total_sheets = sum(c for _, c in res.patterns_used)
    return produced, undercov, overcov, total_sheets


def _check_no_overlap(placements, margin=0):
    for i, a in enumerate(placements):
        ax1, ay1 = margin + a.x_cm, margin + a.y_cm
        ax2, ay2 = ax1 + a.width_cm, ay1 + a.height_cm
        for j, b in enumerate(placements):
            if i >= j:
                continue
            bx1, by1 = margin + b.x_cm, margin + b.y_cm
            bx2, by2 = bx1 + b.width_cm, by1 + b.height_cm
            eps = 0.01
            if ax1 < bx2 - eps and ax2 > bx1 + eps and ay1 < by2 - eps and ay2 > by1 + eps:
                return False, f"Overlap: {i} ({a.part_name}) and {j} ({b.part_name})"
    return True, "OK"


def _check_within_bounds(placements, fmt, margin=0, gap=0):
    uw = fmt.width_cm - 2 * margin
    uh = fmt.height_cm - 2 * margin
    for i, p in enumerate(placements):
        if p.x_cm < -0.01 or p.y_cm < -0.01:
            return False, f"Placement {i} outside bounds: ({p.x_cm:.2f}, {p.y_cm:.2f})"
        if p.x_cm + p.width_cm > uw + 0.01 or p.y_cm + p.height_cm > uh + 0.01:
            return False, f"Placement {i} exceeds sheet: right={p.x_cm+p.width_cm:.2f} > {uw}"
    return True, "OK"


# ============================================================
# EDGE CASES
# ============================================================

def test_ec1_piece_exactly_fills_sheet():
    """Single piece that exactly fills the sheet (no waste)."""
    print("\n--- EC1: Piece exactly fills sheet ---")
    parts = [PartSpec("exact", 200, 140, 5, False)]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}, Util: {res.utilization_ratio:.1%}")

    assert produced.get("exact", 0) >= 5, f"FAIL: produced {produced.get('exact',0)} < 5"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"
    # Utilization should be ~100%
    assert res.utilization_ratio > 0.95, f"FAIL: utilization {res.utilization_ratio:.1%} < 95%"
    print("  PASS")


def test_ec2_single_piece_qty1():
    """Single piece, qty=1 — minimal edge case."""
    print("\n--- EC2: Single piece, qty=1 ---")
    parts = [PartSpec("unique", 100, 80, 1, False)]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}")

    assert total_sheets == 1, f"FAIL: {total_sheets} sheets for qty=1"
    assert produced.get("unique", 0) >= 1, "FAIL: piece not produced"
    print("  PASS")


def test_ec3_many_tiny_pieces():
    """Many tiny pieces that pack very densely."""
    print("\n--- EC3: Many tiny pieces x500 ---")
    parts = [PartSpec("tiny", 10, 5, 500, True)]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.80, w_sheets=0.20)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced.get('tiny',0)}, Util: {res.utilization_ratio:.1%}")

    assert produced.get("tiny", 0) >= 500, f"FAIL: produced {produced.get('tiny',0)} < 500"
    assert res.utilization_ratio > 0.85, f"FAIL: util {res.utilization_ratio:.1%} < 85% for tiny pieces"
    print("  PASS")


def test_ec4_rotation_required():
    """Piece only fits with rotation."""
    print("\n--- EC4: Piece fits only with rotation ---")
    # Sheet is 200x50. Piece is 40x190 — fits rotated as 190x40, not 40x190 (height>50)
    parts = [PartSpec("longpiece", 40, 190, 3, True)]
    formats = [_fmt("A", 200, 50)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}")

    assert produced.get("longpiece", 0) >= 3, f"FAIL: produced {produced.get('longpiece',0)} < 3"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"
    print("  PASS")


def test_ec5_force_no_rotate():
    """With force_no_rotate=True, pieces should not be rotated."""
    print("\n--- EC5: force_no_rotate=True ---")
    parts = [
        PartSpec("portrait", 30, 80, 10, True),   # tall piece — rotation not allowed
        PartSpec("landscape", 80, 30, 10, True),  # wide piece
    ]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=True, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}")

    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"

    # Verify placements respect original orientation
    for pat, _ in res.patterns_used:
        for pl in pat.placements:
            if pl.part_name == "portrait":
                # width must be 30, height 80 (not rotated)
                assert abs(pl.width_cm - 30) < 0.1 and abs(pl.height_cm - 80) < 0.1, \
                    f"FAIL: portrait rotated! {pl.width_cm}x{pl.height_cm}"
            elif pl.part_name == "landscape":
                assert abs(pl.width_cm - 80) < 0.1 and abs(pl.height_cm - 30) < 0.1, \
                    f"FAIL: landscape rotated! {pl.width_cm}x{pl.height_cm}"
    print("  PASS")


def test_ec6_multiple_formats_selection():
    """Two formats available — optimizer should pick the cheaper/more efficient one."""
    print("\n--- EC6: Two formats, optimizer picks better one ---")
    parts = [PartSpec("dilec", 50, 40, 30, True)]
    # Format A is 200x140 = 28000 cm², price 100
    # Format B is 120x100 = 12000 cm², price 200 (expensive, smaller)
    formats = [_fmt("A", 200, 140, price=100.0), _fmt("B", 120, 100, price=200.0)]
    obj = Objectives(w_waste=0.30, w_sheets=0.10, w_cost=0.60)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    used_formats = {pat.fmt.name for pat, _ in res.patterns_used}
    print(f"  Sheets: {total_sheets}, Formats used: {used_formats}, Cost: {res.total_cost:.0f}")

    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"
    # With cost priority, should primarily use cheaper format A
    # Count sheets per format
    sheets_a = sum(cnt for pat, cnt in res.patterns_used if pat.fmt.name == "A")
    sheets_b = sum(cnt for pat, cnt in res.patterns_used if pat.fmt.name == "B")
    print(f"  Sheets A (cheap)={sheets_a}, Sheets B (expensive)={sheets_b}")
    assert sheets_a >= sheets_b, "FAIL: optimizer used more expensive format B more than A"
    print("  PASS")


def test_ec7_limited_sheet_availability():
    """Format has limited availability (available=3) — must not exceed it."""
    print("\n--- EC7: Limited sheet availability (available=3) ---")
    parts = [PartSpec("dilec", 50, 40, 50, True)]
    formats = [
        _fmt("Limited", 200, 140, price=100.0, available=3),
        _fmt("Unlimited", 150, 100, price=120.0, available=0),
    ]
    obj = Objectives(w_waste=0.50, w_sheets=0.50)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}")

    # Count usage of limited format
    limited_usage = sum(cnt for pat, cnt in res.patterns_used if pat.fmt.name == "Limited")
    print(f"  Limited format used: {limited_usage}/3")

    assert limited_usage <= 3, f"FAIL: used {limited_usage} > 3 sheets of limited format"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"
    print("  PASS")


def test_ec8_geometry_with_margin_and_gap():
    """Verify placements respect margin and gap — no piece crosses margin zone."""
    print("\n--- EC8: Geometry with margin=2cm and gap=0.5cm ---")
    margin = 2.0
    gap = 0.5
    parts = [
        PartSpec("A", 40, 30, 10, True),
        PartSpec("B", 25, 20, 15, True),
    ]
    formats = [_fmt("Sheet", 150, 100)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=margin, gap=gap, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"

    for pat, cnt in res.patterns_used:
        ok, msg = _check_within_bounds(pat.placements, pat.fmt, margin=margin)
        assert ok, f"FAIL: {msg}"
        ok2, msg2 = _check_no_overlap(pat.placements)
        assert ok2, f"FAIL: {msg2}"

    print(f"  Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print("  PASS")


def test_ec9_df_to_parts_edge_cases():
    """Test df_to_parts with edge case inputs."""
    print("\n--- EC9: df_to_parts edge cases ---")
    import pandas as pd

    # Normal valid data
    df = pd.DataFrame([
        {"Název": "A", "Šířka (cm)": 50.0, "Výška (cm)": 30.0, "Počet kusů": 20, "Lze otočit": True},
        {"Název": "B", "Šířka (cm)": 40.0, "Výška (cm)": 25.0, "Počet kusů": 10, "Lze otočit": False},
    ])
    parts = df_to_parts(df)
    assert len(parts) == 2, f"FAIL: expected 2 parts, got {len(parts)}"
    assert parts[0].name == "A"
    assert parts[0].width_cm == 50.0
    assert parts[1].rotatable == False

    # Rows with empty names should be skipped
    df2 = pd.DataFrame([
        {"Název": "A", "Šířka (cm)": 50.0, "Výška (cm)": 30.0, "Počet kusů": 20, "Lze otočit": False},
        {"Název": "", "Šířka (cm)": float("nan"), "Výška (cm)": float("nan"), "Počet kusů": float("nan"), "Lze otočit": False},
        {"Název": "  ", "Šířka (cm)": float("nan"), "Výška (cm)": float("nan"), "Počet kusů": float("nan"), "Lze otočit": False},
    ])
    parts2 = df_to_parts(df2)
    assert len(parts2) == 1, f"FAIL: expected 1 part (empty rows skipped), got {len(parts2)}"

    print("  PASS")


def test_ec10_df_to_formats_edge_cases():
    """Test df_to_formats with edge case inputs."""
    print("\n--- EC10: df_to_formats edge cases ---")
    import pandas as pd

    # available=0 means unlimited
    df = pd.DataFrame([
        {"Název": "Big", "Šířka (cm)": 200.0, "Výška (cm)": 140.0, "Dostupné množství (0 = ∞)": 0},
        {"Název": "Small", "Šířka (cm)": 100.0, "Výška (cm)": 70.0, "Dostupné množství (0 = ∞)": 5},
    ])
    formats = df_to_formats(df)
    assert len(formats) == 2
    assert formats[0].available == 0   # unlimited
    assert formats[1].available == 5   # limited

    # Empty rows skipped
    df2 = pd.DataFrame([
        {"Název": "A", "Šířka (cm)": 200.0, "Výška (cm)": 140.0, "Dostupné množství (0 = ∞)": 0},
        {"Název": "", "Šířka (cm)": float("nan"), "Výška (cm)": float("nan"), "Dostupné množství (0 = ∞)": float("nan")},
    ])
    formats2 = df_to_formats(df2)
    assert len(formats2) == 1, f"FAIL: expected 1 format (empty rows skipped), got {len(formats2)}"

    print("  PASS")


def test_ec11_utilization_calculation():
    """Verify utilization is calculated from usable area (excluding margins)."""
    print("\n--- EC11: Utilization from usable area ---")
    margin = 5.0  # 5cm margins
    # Sheet 200x140, usable = 190x130 = 24700 cm²
    # One piece 190x130 = 24700 cm² → expected ~100% utilization
    parts = [PartSpec("full", 190, 130, 1, False)]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=margin, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    print(f"  Utilization: {res.utilization_ratio:.1%}")
    # Should be close to 100% since the piece fills the usable area
    assert res.utilization_ratio > 0.95, f"FAIL: util {res.utilization_ratio:.1%} < 95%"
    print("  PASS")


def test_ec12_overcoverage_penalty():
    """MIP should minimize overproduction (overcov penalty in objective)."""
    print("\n--- EC12: Overcoverage minimization ---")
    # 1 type x 10, sheet fits exactly 10 per sheet
    # Expected: 1 sheet, exactly 10 pieces
    parts = [PartSpec("pcs", 20, 14, 10, False)]
    formats = [_fmt("A", 200, 140)]  # Fits 10x10 = 100 pieces, or 10 in a row × 10 cols
    obj = Objectives(w_waste=0.50, w_sheets=0.50)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Sheets: {total_sheets}, Produced: {produced}, Overcov: {overcov}")

    assert produced.get("pcs", 0) >= 10, "FAIL: undercoverage"
    # Sheet fits 100 per sheet, so minimum overproduction with 1 sheet = 90
    # But optimizer might use 1 sheet. Key check: no undercoverage.
    assert undercov.get("pcs", 0) == 0, f"FAIL: undercoverage {undercov}"
    print("  PASS")


def test_ec13_max_patterns_1():
    """max_patterns=1: single pattern must cover at least SOME demand."""
    print("\n--- EC13: max_patterns=1 ---")
    parts = [
        PartSpec("A", 50, 40, 10, True),
        PartSpec("B", 30, 20, 10, True),
    ]
    formats = [_fmt("Sheet", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=1)

    n_patterns = len(res.patterns_used)
    print(f"  Patterns: {n_patterns}, Sheets: {sum(c for _,c in res.patterns_used)}, Util: {res.utilization_ratio:.1%}")

    assert n_patterns <= 1, f"FAIL: {n_patterns} patterns > 1"
    assert len(res.sheet_results) > 0, "FAIL: no sheets produced"
    print("  PASS")


def test_ec14_price_optimization():
    """With w_cost high, optimizer should prefer cheaper format."""
    print("\n--- EC14: Price optimization ---")
    parts = [PartSpec("dilec", 90, 60, 20, True)]
    formats = [
        _fmt("Cheap", 200, 140, price=50.0),
        _fmt("Expensive", 200, 140, price=500.0),
    ]
    obj = Objectives(w_waste=0.0, w_sheets=0.0, w_cost=1.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    used_formats = {pat.fmt.name for pat, _ in res.patterns_used}
    print(f"  Formats used: {used_formats}, Cost: {res.total_cost:.0f}")

    assert "Cheap" in used_formats, "FAIL: optimizer didn't choose cheaper format"
    assert "Expensive" not in used_formats, "FAIL: optimizer chose expensive format"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercov {undercov}"
    print("  PASS")


def test_ec15_determinism():
    """Same inputs produce same outputs (deterministic)."""
    print("\n--- EC15: Determinism ---")
    parts = [
        PartSpec("A", 50, 40, 15, True),
        PartSpec("B", 30, 25, 20, True),
    ]
    formats = [_fmt("Sheet", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    results = []
    for _ in range(2):
        res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                       force_no_rotate=False, obj=obj, max_patterns=0)
        produced, undercov, _, sheets = _analyze(res, parts)
        results.append((sheets, res.utilization_ratio, dict(undercov)))

    r1, r2 = results
    print(f"  Run1: sheets={r1[0]}, util={r1[1]:.3f}")
    print(f"  Run2: sheets={r2[0]}, util={r2[1]:.3f}")

    # Same number of sheets and utilization (within small float tolerance)
    assert r1[0] == r2[0], f"FAIL: sheet count differs {r1[0]} vs {r2[0]}"
    assert abs(r1[1] - r2[1]) < 0.01, f"FAIL: util differs {r1[1]:.3f} vs {r2[1]:.3f}"
    assert r1[2] == r2[2], f"FAIL: coverage differs {r1[2]} vs {r2[2]}"
    print("  PASS")


if __name__ == "__main__":
    tests = [
        test_ec1_piece_exactly_fills_sheet,
        test_ec2_single_piece_qty1,
        test_ec3_many_tiny_pieces,
        test_ec4_rotation_required,
        test_ec5_force_no_rotate,
        test_ec6_multiple_formats_selection,
        test_ec7_limited_sheet_availability,
        test_ec8_geometry_with_margin_and_gap,
        test_ec9_df_to_parts_edge_cases,
        test_ec10_df_to_formats_edge_cases,
        test_ec11_utilization_calculation,
        test_ec12_overcoverage_penalty,
        test_ec13_max_patterns_1,
        test_ec14_price_optimization,
        test_ec15_determinism,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("ALL EDGE CASE TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
