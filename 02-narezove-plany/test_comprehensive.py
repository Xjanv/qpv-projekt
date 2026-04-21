"""Comprehensive tests for MIP pattern selection and layout quality."""
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
)
import random


def _fmt(name, w, h, price=100.0):
    return SheetFormat(name=name, width_cm=w, height_cm=h, price_kc=price, available=0)


def _analyze(res, parts):
    """Analyze optimization result."""
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
    """Check that no two placements overlap."""
    for i, a in enumerate(placements):
        ax1, ay1 = margin + a.x_cm, margin + a.y_cm
        ax2, ay2 = ax1 + a.width_cm, ay1 + a.height_cm
        for j, b in enumerate(placements):
            if i >= j:
                continue
            bx1, by1 = margin + b.x_cm, margin + b.y_cm
            bx2, by2 = bx1 + b.width_cm, by1 + b.height_cm
            # Check overlap (with small epsilon for float)
            eps = 0.01
            if ax1 < bx2 - eps and ax2 > bx1 + eps and ay1 < by2 - eps and ay2 > by1 + eps:
                return False, f"Overlap: placement {i} ({a.part_name}) and {j} ({b.part_name})"
    return True, "OK"


def _check_within_bounds(placements, fmt, margin=0):
    """Check all placements are within sheet bounds."""
    uw = fmt.width_cm - 2 * margin
    uh = fmt.height_cm - 2 * margin
    for i, p in enumerate(placements):
        if p.x_cm < -0.01 or p.y_cm < -0.01:
            return False, f"Placement {i} ({p.part_name}) outside bounds: ({p.x_cm}, {p.y_cm})"
        if p.x_cm + p.width_cm > uw + 0.01 or p.y_cm + p.height_cm > uh + 0.01:
            return False, f"Placement {i} ({p.part_name}) exceeds sheet: x={p.x_cm}+{p.width_cm}={p.x_cm+p.width_cm} > {uw}"
    return True, "OK"


# ============================================================
# TEST CASES
# ============================================================

def test_1_max2_five_types():
    """max_patterns=2, 5 types x 20 pcs: must use <=2 patterns, cover all demand."""
    print("\n--- Test 1: max_patterns=2, 5 types x 20 ---")
    parts = [
        PartSpec("pod_p", 80, 60, 20, True),
        PartSpec("sedak", 60, 50, 20, True),
        PartSpec("zada", 70, 40, 20, True),
        PartSpec("operk", 30, 25, 20, True),
        PartSpec("boky", 40, 35, 20, True),
    ]
    formats = [_fmt("A", 200, 140), _fmt("B", 160, 100)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25, w_cuts=0.0, w_cost=0.25, w_formats=0.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=30,
                   force_no_rotate=False, obj=obj, max_patterns=2)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    n_patterns = len(res.patterns_used)

    print(f"  Patterns: {n_patterns}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print(f"  Produced: {produced}")
    print(f"  Undercov: {undercov}")
    print(f"  Overcov:  {overcov}")

    assert n_patterns <= 2, f"FAIL: {n_patterns} patterns > 2"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"
    print("  PASS")


def test_2_auto_five_types():
    """max_patterns=0 (auto), 5 types x 20 pcs: good utilization, full coverage."""
    print("\n--- Test 2: max_patterns=0 (auto), 5 types x 20 ---")
    parts = [
        PartSpec("pod_p", 80, 60, 20, True),
        PartSpec("sedak", 60, 50, 20, True),
        PartSpec("zada", 70, 40, 20, True),
        PartSpec("operk", 30, 25, 20, True),
        PartSpec("boky", 40, 35, 20, True),
    ]
    formats = [_fmt("A", 200, 140), _fmt("B", 160, 100)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25, w_cuts=0.0, w_cost=0.25, w_formats=0.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=30,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    n_patterns = len(res.patterns_used)

    print(f"  Patterns: {n_patterns}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print(f"  Undercov: {undercov}")

    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"
    assert res.utilization_ratio > 0.60, f"FAIL: utilization {res.utilization_ratio:.1%} < 60%"
    print("  PASS")


def test_3_single_type():
    """1 type x 100 pcs: should use few patterns, cover demand exactly."""
    print("\n--- Test 3: 1 type x 100, 1 format ---")
    parts = [PartSpec("dilec", 50, 30, 100, True)]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25, w_cuts=0.0, w_cost=0.25, w_formats=0.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=15,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)

    print(f"  Patterns: {len(res.patterns_used)}, Sheets: {total_sheets}")
    print(f"  Produced: {produced}, Overcov: {overcov}")

    assert produced["dilec"] >= 100, f"FAIL: produced {produced['dilec']} < 100"
    assert overcov["dilec"] <= 10, f"FAIL: overproduction {overcov['dilec']} > 10"
    print("  PASS")


def test_4_no_overlap_geometry():
    """Verify no placements overlap in generated patterns."""
    print("\n--- Test 4: Geometry - no overlaps in patterns ---")
    parts = [
        PartSpec("pod_p", 80, 60, 20, True),
        PartSpec("sedak", 60, 50, 20, True),
        PartSpec("zada", 70, 40, 20, True),
        PartSpec("operk", 30, 25, 20, True),
    ]
    formats = [_fmt("A", 200, 140), _fmt("B", 160, 100)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=20,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    errors = []
    for pat, cnt in res.patterns_used:
        ok, msg = _check_no_overlap(pat.placements)
        if not ok:
            errors.append(f"Pattern {pat.fmt.name}: {msg}")
        ok2, msg2 = _check_within_bounds(pat.placements, pat.fmt)
        if not ok2:
            errors.append(f"Pattern {pat.fmt.name}: {msg2}")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        assert False, "Geometry errors found"
    else:
        print(f"  Checked {len(res.patterns_used)} patterns - all clean")
        print("  PASS")


def test_5_with_margin_and_gap():
    """Test with margin=1cm and gap=0.3cm."""
    print("\n--- Test 5: margin=1, gap=0.3, 3 types ---")
    parts = [
        PartSpec("A", 50, 40, 30, True),
        PartSpec("B", 30, 25, 50, True),
        PartSpec("C", 20, 15, 40, True),
    ]
    formats = [_fmt("Sheet", 150, 100)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=1.0, gap=0.3, budget_s=20,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    n_patterns = len(res.patterns_used)

    print(f"  Patterns: {n_patterns}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print(f"  Undercov: {undercov}")

    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"
    print("  PASS")


def test_6_large_pieces():
    """Large pieces that barely fit on sheet."""
    print("\n--- Test 6: Large pieces (barely fit) ---")
    parts = [
        PartSpec("big_a", 190, 130, 5, False),
        PartSpec("small", 30, 20, 10, True),
    ]
    formats = [_fmt("A", 200, 140)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=15,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)

    print(f"  Patterns: {len(res.patterns_used)}, Sheets: {total_sheets}")
    print(f"  Produced: {produced}")
    print(f"  Undercov: {undercov}")

    assert produced["big_a"] >= 5, f"FAIL: big_a produced {produced['big_a']} < 5"
    assert produced["small"] >= 10, f"FAIL: small produced {produced['small']} < 10"
    print("  PASS")


def test_7_max3_diverse():
    """max_patterns=3 with diverse part sizes."""
    print("\n--- Test 7: max_patterns=3, diverse sizes ---")
    parts = [
        PartSpec("velky", 90, 70, 15, True),
        PartSpec("stredni", 50, 40, 25, True),
        PartSpec("maly", 20, 15, 50, True),
    ]
    formats = [_fmt("A", 200, 140), _fmt("B", 100, 80)]
    obj = Objectives(w_waste=0.50, w_sheets=0.25)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=20,
                   force_no_rotate=False, obj=obj, max_patterns=3)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    n_patterns = len(res.patterns_used)

    print(f"  Patterns: {n_patterns}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print(f"  Undercov: {undercov}")
    print(f"  Overcov:  {overcov}")

    assert n_patterns <= 3, f"FAIL: {n_patterns} patterns > 3"
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"
    print("  PASS")


def test_8_visualization_ticks():
    """Test that draw_sheet_figure doesn't crash and produces valid figure."""
    print("\n--- Test 8: Visualization (draw_sheet_figure) ---")
    parts = [PartSpec("A", 50, 30, 10, True)]
    fmt = _fmt("Sheet", 200, 140)

    pat = _gen_pattern(parts, fmt, 0, 0, False, "BAF", random.Random(42))
    assert pat is not None, "FAIL: _gen_pattern returned None"

    fig = draw_sheet_figure(pat.placements, fmt, 3, "Test", 0)
    ax = fig.axes[0]

    # Check custom ticks exist
    xticks = ax.get_xticks()
    yticks = ax.get_yticks()
    assert len(xticks) > 2, f"FAIL: only {len(xticks)} x-ticks (expected piece boundaries)"
    assert len(yticks) > 2, f"FAIL: only {len(yticks)} y-ticks (expected piece boundaries)"
    # Check 0 and sheet dimensions are in ticks
    assert 0 in xticks or 0.0 in xticks, "FAIL: 0 not in x-ticks"

    import matplotlib.pyplot as plt
    plt.close(fig)
    print(f"  X-ticks: {list(xticks)}")
    print(f"  Y-ticks: {list(yticks)}")
    print("  PASS")


def test_9_real_world_scenario():
    """Real-world: 3 types of upholstery, 2 roll formats, as in user's test case."""
    print("\n--- Test 9: Real-world upholstery scenario ---")
    parts = [
        PartSpec("Potah", 36.4, 56.0, 306, True),
        PartSpec("A", 31.6, 56.0, 153, True),
        PartSpec("B", 31.6, 50.0, 153, True),
    ]
    formats = [
        SheetFormat("role140", 107.0, 168.0, 100.0, 0),
        SheetFormat("role160", 107.0, 150.0, 100.0, 0),
    ]
    obj = Objectives(w_waste=0.50, w_sheets=0.25, w_cuts=0.0, w_cost=0.0, w_formats=0.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=30,
                   force_no_rotate=True, obj=obj, max_patterns=0)

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    n_patterns = len(res.patterns_used)

    print(f"  Patterns: {n_patterns}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    print(f"  Produced: {produced}")
    print(f"  Undercov: {undercov}")
    print(f"  Overcov:  {overcov}")

    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"

    # Verify geometry for each pattern
    for pat, cnt in res.patterns_used:
        ok, msg = _check_no_overlap(pat.placements)
        assert ok, f"FAIL overlap in pattern: {msg}"
        ok2, msg2 = _check_within_bounds(pat.placements, pat.fmt)
        assert ok2, f"FAIL bounds in pattern: {msg2}"

    print("  PASS")


def _is_guillotine_feasible(placements, x0, y0, w, h, eps=0.1):
    """Recursively verify placements form a guillotine-feasible layout."""
    inside = [p for p in placements
              if p.x_cm >= x0 - eps and p.y_cm >= y0 - eps
              and p.x_cm + p.width_cm <= x0 + w + eps
              and p.y_cm + p.height_cm <= y0 + h + eps]
    if len(inside) <= 1:
        return True

    # Collect all possible cut lines at piece boundaries
    cuts_x = sorted({p.x_cm for p in inside} | {p.x_cm + p.width_cm for p in inside})
    cuts_y = sorted({p.y_cm for p in inside} | {p.y_cm + p.height_cm for p in inside})

    for cx in cuts_x:
        if x0 + eps < cx < x0 + w - eps:
            left = [p for p in inside if p.x_cm + p.width_cm <= cx + eps]
            right = [p for p in inside if p.x_cm >= cx - eps]
            if left and right and len(left) + len(right) == len(inside):
                if (_is_guillotine_feasible(left, x0, y0, cx - x0, h, eps)
                        and _is_guillotine_feasible(right, cx, y0, x0 + w - cx, h, eps)):
                    return True

    for cy in cuts_y:
        if y0 + eps < cy < y0 + h - eps:
            top = [p for p in inside if p.y_cm + p.height_cm <= cy + eps]
            bot = [p for p in inside if p.y_cm >= cy - eps]
            if top and bot and len(top) + len(bot) == len(inside):
                if (_is_guillotine_feasible(top, x0, y0, w, cy - y0, eps)
                        and _is_guillotine_feasible(bot, x0, cy, w, y0 + h - cy, eps)):
                    return True

    return False


def test_10_guillotine_feasibility():
    """Verify all generated patterns are guillotine-feasible."""
    print("\n--- Test 10: Guillotine feasibility ---")
    parts = [
        PartSpec("sedak", 52.0, 48.0, 20, True),
        PartSpec("operka", 52.0, 60.0, 20, True),
        PartSpec("pod_l", 18.0, 48.0, 20, False),
        PartSpec("pod_p", 18.0, 48.0, 20, False),
        PartSpec("zada", 40.0, 55.0, 20, True),
    ]
    formats = [
        _fmt("role1", 140.0, 200.0),
        _fmt("role2", 160.0, 200.0),
    ]
    obj = Objectives(w_waste=0.50, w_sheets=0.25, w_cuts=0.0, w_cost=0.0, w_formats=0.0)

    res = optimize(parts, formats, margin=0, gap=0, budget_s=10,
                   force_no_rotate=False, obj=obj, max_patterns=0)

    checked = 0
    for pat, cnt in res.patterns_used:
        uw = pat.fmt.width_cm
        uh = pat.fmt.height_cm
        ok = _is_guillotine_feasible(pat.placements, 0, 0, uw, uh)
        assert ok, (
            f"FAIL: Pattern on {pat.fmt.name} is NOT guillotine-feasible! "
            f"Placements: {[(p.part_name, p.x_cm, p.y_cm, p.width_cm, p.height_cm) for p in pat.placements]}"
        )
        checked += 1

    print(f"  Checked {checked} patterns - all guillotine-feasible")

    produced, undercov, overcov, total_sheets = _analyze(res, parts)
    print(f"  Patterns: {len(res.patterns_used)}, Sheets: {total_sheets}, Util: {res.utilization_ratio:.1%}")
    assert all(v == 0 for v in undercov.values()), f"FAIL: undercoverage {undercov}"
    print("  PASS")


if __name__ == "__main__":
    tests = [
        test_1_max2_five_types,
        test_2_auto_five_types,
        test_3_single_type,
        test_4_no_overlap_geometry,
        test_5_with_margin_and_gap,
        test_6_large_pieces,
        test_7_max3_diverse,
        test_8_visualization_ticks,
        test_9_real_world_scenario,
        test_10_guillotine_feasibility,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
