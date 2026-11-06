# -*- coding: utf-8 -*-
"""
calibrate_validate 1.10.0 の測り直しの採否（案B、2026-11-05）：見落とした回を採らない規則のテスト。
数値は PL03（2026-11-05）の calib JSON の実例（両眼平均の視線の accuracy、有効率）を写したもの。
"""
import pytest

from calib_compare import current_criteria
from calibrate_validate import (
    CRITERIA_VERSION, CRITERIA_VERSION_V2, lapse_choice, merge_retry, points_selected_by_data_quality,
    points_selected_by_lapse_rule, summarize_verdict,
)


def _att(label, kind, binocular, lrate=1.0, rrate=1.0, prec=0.2):
    """1回分の測定（point_metrics の出力の形）。"""
    return {"label": label, "kind": kind, "accuracy_deg": binocular, "precision_rms_s2s_deg": prec,
            "n_samples_window": 30, "left": {"valid_rate": lrate}, "right": {"valid_rate": rrate},
            "binocular_avg": {"accuracy_deg": binocular}}


def _ok(label, kind="grid", binocular=0.5):
    return _att(label, kind, binocular)


def test_lapse_choice_only_excludes_the_lapsed_attempt():
    assert lapse_choice(18.14, 1.88, 5.0) == "retry"     # 2回目の minimap：もう片方が 1.5° を超えていても採る
    assert lapse_choice(1.99, 11.43, 5.0) == "first"     # 測り直しの方を見落とした
    assert lapse_choice(5.07, 9.57, 5.0) is None         # 両方 5° 超（credits）→ データ品質で決める
    assert lapse_choice(1.53, 0.37, 5.0) is None         # 両方 5° 以下 → データ品質（accuracy で選ばない）
    assert lapse_choice(5.0, 0.5, 5.0) is None           # ちょうど閾値は「超え」ではない
    assert lapse_choice(None, 0.5, 5.0) is None
    assert lapse_choice(18.0, 0.5, None) is None         # --no-lapse-rule


def test_merge_retry_records_both_rules():
    # 試合後の検証の grid_0.5_0.9：1回目 10.56°（有効率 0.93/1.0）、測り直し 0.68°（0.9/0.8）
    first = _att("grid_0.5_0.9", "grid", 10.56, 0.93, 1.0)
    retry = _att("grid_0.5_0.9", "grid", 0.68, 0.9, 0.8)
    m = merge_retry(first, retry)
    assert m["selected_attempt"] == "retry" and m["selection_rule"] == "lapse"
    assert m["selected_attempt_data_quality"] == "first"          # 1.9.0 なら 1回目（有効率で）
    assert m["binocular_avg"]["accuracy_deg"] == 0.68
    assert m["first_attempt"]["binocular_avg_accuracy_deg"] == 10.56
    # 規則を使わなければ 1.9.0 と同じ
    m0 = merge_retry(first, retry, None)
    assert m0["selected_attempt"] == "first" and m0["selection_rule"] == "data_quality"
    # 両方 5° 以下なら、accuracy が良くてもデータ品質が悪い方は採らない（従来どおり）
    m1 = merge_retry(_att("aoi_enemy_team_status", "aoi", 1.53, prec=0.15),
                     _att("aoi_enemy_team_status", "aoi", 0.37, prec=0.17))
    assert m1["selected_attempt"] == "first" and m1["selection_rule"] == "data_quality"


def _pl03_second_run():
    """PL03 の2回目（160842）を簡略にしたもの：見落とし2点（grid_0.5_0.5、minimap）が品質で採られた回。"""
    pts = [_ok(f"grid_{i}") for i in range(5)]
    pts.append(merge_retry(_att("grid_0.5_0.5", "grid", 7.22, 1.0, 1.0), _att("grid_0.5_0.5", "grid", 0.95, 0.87, 1.0)))
    pts.append(merge_retry(_att("aoi_minimap", "aoi", 18.14, 1.0, 1.0), _att("aoi_minimap", "aoi", 1.88, 1.0, 0.83)))
    pts.append(_ok("aoi_round_timer", "aoi", 0.6))
    return pts


def test_summary_current_and_v2():
    pts = _pl03_second_run()
    s = summarize_verdict(pts, 1.0, 1.5, calibration_ok=True)
    assert s["criteria_version"] == CRITERIA_VERSION and s["criteria_version_v2"] == CRITERIA_VERSION_V2
    assert s["points_selected_by_lapse_rule"] == ["grid_0.5_0.5", "aoi_minimap"]
    # 1.10.0：minimap は測り直しの 1.88° で HUD 不合格（見落としを除いても合格にはならない）
    assert s["hud_points_failed"] == ["aoi_minimap"] and not s["passed"]
    assert s["mean_accuracy_binocular_deg"] == pytest.approx((0.5 * 5 + 0.95 + 1.88 + 0.6) / 8)
    # 1.9.0 の採り方：両方とも 1回目（有効率で）
    assert s["mean_accuracy_binocular_deg_v2"] == pytest.approx((0.5 * 5 + 7.22 + 18.14 + 0.6) / 8)
    assert s["hud_points_failed_v2"] == ["aoi_minimap"] and not s["passed_v2"]
    assert s["hud_points_failed_legacy"] == ["aoi_minimap"]
    assert s["lapse_threshold_deg"] == 5.0


def test_lapse_rule_can_turn_a_fail_into_a_pass():
    """試合後の検証の例：見落とし1点だけで不合格だった回が、規則で合格になる。"""
    pts = [_ok(f"grid_{i}") for i in range(4)]
    pts.append(merge_retry(_att("grid_0.5_0.9", "grid", 10.56, 0.93, 1.0), _att("grid_0.5_0.9", "grid", 0.68, 0.9, 0.8)))
    s = summarize_verdict(pts, 1.0, 1.5, calibration_ok=None)
    assert s["passed"] and not s["passed_v2"]


def test_views_for_old_and_new_json():
    pts = _pl03_second_run()
    dq = points_selected_by_data_quality(pts)
    assert [p["binocular_avg"]["accuracy_deg"] for p in dq if p.get("retried")] == [7.22, 18.14]
    # 1.9.0 の JSON（データ品質で採った値が記録されている）に規則を当てると、1.10.0 と同じ値になる
    lapse, changed = points_selected_by_lapse_rule(dq)
    assert changed == ["grid_0.5_0.5", "aoi_minimap"]
    assert [p["binocular_avg"]["accuracy_deg"] for p in lapse if p.get("retried")] == [0.95, 1.88]
    # 1.10.0 の JSON はもう当ててあるので変わらない
    assert points_selected_by_lapse_rule(pts)[1] == []


def test_calib_compare_recomputes_with_lapse_rule():
    dq = points_selected_by_data_quality(_pl03_second_run())
    r = current_criteria(dq, {"status": "calibration_status_success", "succeeded": True})
    assert r["n_points_changed_by_lapse_rule"] == 2
    assert r["mean_accuracy_binocular_deg"] == pytest.approx((0.5 * 5 + 0.95 + 1.88 + 0.6) / 8)
    assert r["passed_current_criteria"] is False
