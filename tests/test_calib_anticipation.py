# -*- coding: utf-8 -*-
"""calib_anticipation（先読みの診断）と calib_compare の眼の移動の列のテスト。"""
import math

import pytest

from calib_anticipation import analyze, binom_tail, next_targets
from calib_compare import eye_drift

W, H, Z = 596.7, 335.6, 650.0


def _pt(label, x, y, bias_x_deg=0.0, bias_y_deg=0.0, prec=0.15, **extra):
    """両眼とも同じずれ（度）の検証点。"""
    bx = math.tan(math.radians(bias_x_deg)) * Z / W
    by = math.tan(math.radians(bias_y_deg)) * Z / H
    eye = {"bias_x_norm": bx, "bias_y_norm": by}
    return {"label": label, "target_x": x, "target_y": y, "left": dict(eye), "right": dict(eye),
            "precision_rms_s2s_deg": prec, **extra}


def _calib(points):
    return {"subject_id": "T", "display_area_ucs_mm": {"width_mm": W, "height_mm": H},
            "head_position": {"recorded": {"z_mid_mm": Z}}, "validation_points": points}


def test_inferred_order_for_old_json():
    # 1.3.0 以前：1周目は一覧の順、測り直しは index 順。測り直しを採用した点の次は、次の測り直しの点
    pts = [_pt("a", 0.1, 0.1), _pt("b", 0.5, 0.1, retried=True, selected_attempt="retry"),
           _pt("c", 0.9, 0.1), _pt("d", 0.5, 0.5, retried=True, selected_attempt="first")]
    nxt, source = next_targets(pts)
    assert source == "inferred"
    assert nxt == {0: 1, 1: 3, 2: 3, 3: 1}  # d は1周目（次は b の測り直し）、b の測り直しの次は d の測り直し


def test_recorded_order_is_used():
    # 記録された順序が実装からの推定と違っても、記録を使う
    pts = [_pt("a", 0.1, 0.1, presentation_index=0),
           _pt("b", 0.5, 0.1, presentation_index=5, retried=True, selected_attempt="retry",
               first_attempt={"presentation_index": 1}, retry_attempt={"presentation_index": 5}),
           _pt("c", 0.9, 0.1, presentation_index=2),
           _pt("d", 0.5, 0.5, presentation_index=3, retried=True, selected_attempt="first",
               first_attempt={"presentation_index": 3}, retry_attempt={"presentation_index": 4})]
    nxt, source = next_targets(pts)
    assert source == "recorded"
    assert nxt == {0: 1, 2: 3, 3: 3, 1: None}  # d の1周目の次は d の測り直し（4）、b の測り直し（5）は最後


def test_anticipation_detected_toward_next_point():
    # ally で次の round_timer（右へ約 9°）の方向へ 8° ずれ、区間内で目が動いた
    pts = [_pt("aoi_ally_team_status", 0.327, 0.048, bias_x_deg=8.0, prec=1.2),
           _pt("aoi_round_timer", 0.501, 0.048),
           _pt("aoi_enemy_team_status", 0.675, 0.048, bias_x_deg=-2.0)]
    res = analyze(_calib(pts))
    rows = {r["label"]: r for r in res["rows"]}
    assert set(rows) == {"aoi_ally_team_status"}  # 最後の点は次が無いので対象外、ずれの小さい点も対象外
    r = rows["aoi_ally_team_status"]
    assert r["next_label"] == "aoi_round_timer" and r["moving"]
    assert r["cos"] == pytest.approx(1.0, abs=1e-6)
    assert r["error_deg"] == pytest.approx(8.0, abs=1e-6)


def test_opposite_direction_is_not_anticipation():
    pts = [_pt("p", 0.1, 0.5, bias_x_deg=-3.0), _pt("q", 0.5, 0.5)]
    r = analyze(_calib(pts))["rows"][0]
    assert r["cos"] == pytest.approx(-1.0, abs=1e-6) and not r["moving"]


def test_binom_tail_matches_1029_result():
    # 10/29：目が動いた 9 点中 6 点（一致の確率 約 0.253）→ 約 0.011
    chance = 2 * math.degrees(math.acos(0.7)) / 360
    assert binom_tail(9, 6, chance) == pytest.approx(0.011, abs=0.001)
    assert binom_tail(3, 0, chance) == pytest.approx(1.0)


def test_eye_drift_columns():
    pts = [{"eye_xyz_mm": [0.0, -50.0, 640.0]}, {"eye_xyz_mm": [0.0, -40.0, 620.0]},
           {"eye_xyz_mm": [0.0, -45.0, 600.0]}, {"eye_xyz_mm": None}]
    d = eye_drift(pts, {"y_mid_mm": -44.8, "z_mid_mm": 660.0})
    assert d["eye_z_shift_mm"] == pytest.approx(-40.0)  # 中央値 620 − 660：前に出た
    assert d["eye_y_shift_mm"] == pytest.approx(-0.2)
    assert d["eye_z_range_mm"] == pytest.approx(40.0) and d["eye_y_range_mm"] == pytest.approx(10.0)


def test_eye_drift_old_json_is_nan():
    d = eye_drift([{"label": "x"}], {"y_mid_mm": -44.8, "z_mid_mm": 660.0})
    assert all(math.isnan(v) for v in d.values())
