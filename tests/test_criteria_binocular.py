# -*- coding: utf-8 -*-
"""calibrate_validate 1.9.0 の合否の基準（両眼平均の視線、credits を除く）と、それを読む側のテスト。"""
import math

import pytest

from apply_calibration import load_candidate
from calib_compare import current_criteria
from calibrate_validate import CRITERIA_VERSION, judge, judge_binocular, points_to_retry, summarize_verdict


def _p(label, kind, per_eye, binocular):
    return {"label": label, "kind": kind, "accuracy_deg": per_eye, "precision_rms_s2s_deg": 0.2,
            "binocular_avg": {"accuracy_deg": binocular}}


def test_binocular_basis_and_credits_excluded():
    pts = [_p(f"grid_{i}", "grid", 0.5, 0.3) for i in range(3)] + [
        _p("aoi_minimap", "aoi", 1.8, 1.2),     # 左右眼の平均では不合格、両眼平均の視線では合格
        _p("aoi_credits", "aoi", 2.0, 1.9)]     # credits は判定から外す
    v = judge_binocular(pts, 1.0, 1.5, calibration_ok=True)
    assert v["passed"] and v["hud_points_failed"] == [] and v["criteria_version"] == CRITERIA_VERSION
    assert v["mean_accuracy_binocular_deg"] == pytest.approx((0.9 + 1.2 + 1.9) / 5)  # 全点平均には credits も入る
    assert not judge(pts, 1.0, 1.5)["passed"]  # 旧基準では不合格


def test_binocular_mean_threshold_and_missing_data():
    pts = [_p("grid_0.5_0.5", "grid", 0.9, 1.2), _p("aoi_round_timer", "aoi", 0.5, 0.9)]
    assert not judge_binocular(pts, 1.0, 1.5)["passed"]  # 全点平均 1.05 は 1.0 超
    pts = [_p("grid_0.5_0.5", "grid", 0.3, 0.3), _p("aoi_round_timer", "aoi", None, None)]
    v = judge_binocular(pts, 1.0, 1.5)
    assert not v["passed"] and v["hud_points_failed"] == ["aoi_round_timer"]
    assert v["points_without_binocular_data"] == ["aoi_round_timer"]
    assert not judge_binocular([_p("aoi_minimap", "aoi", 0.3, 0.3)], 1.0, 1.5, calibration_ok=False)["passed"]


PTS_CREDITS_ONLY_BAD = [_p(f"grid_{i}", "grid", 0.5, 0.3) for i in range(3)] + [_p("aoi_credits", "aoi", 2.0, 1.9)]


def test_summary_keeps_legacy_fields():
    s = summarize_verdict(PTS_CREDITS_ONLY_BAD, 1.0, 1.5, calibration_ok=True)
    assert s["passed"] is True and s["passed_legacy"] is False
    assert s["hud_points_failed"] == [] and s["hud_points_failed_legacy"] == ["aoi_credits"]
    assert s["mean_accuracy_deg"] == pytest.approx(0.875)  # 従来どおり左右眼の誤差の平均
    assert s["mean_accuracy_binocular_deg"] == pytest.approx(0.7)


def test_retry_uses_binocular_when_requested():
    pts = [_p("a", "grid", 1.8, 1.2), _p("b", "grid", 0.5, 1.7), _p("c", "grid", 0.5, None)]
    assert points_to_retry(pts, 1.5, basis="binocular") == [1, 2]
    assert points_to_retry(pts, 1.5) == [0]  # 既定は従来どおり


def test_calib_compare_recomputes_current_criteria():
    r = current_criteria(PTS_CREDITS_ONLY_BAD, {"status": "calibration_status_success", "succeeded": True})
    assert r["passed_current_criteria"] is True and r["mean_accuracy_binocular_deg"] == pytest.approx(0.7)
    old = [{"label": "grid_0.5_0.5", "kind": "grid", "accuracy_deg": 0.5}]  # 1.4.0 より前
    assert current_criteria(old, {}) == {"passed_current_criteria": None, "mean_accuracy_binocular_deg": None}


def test_apply_calibration_uses_binocular_mean(tmp_path):
    import json
    (tmp_path / "c.calibdata").write_bytes(b"x")
    calib = {"subject_id": "P01", "calibration": {"succeeded": True},
             "summary": {"passed": False, "hud_points_failed": [], "mean_accuracy_deg": 1.3,
                         "mean_accuracy_binocular_deg": 1.1},
             "active_calibration": {"file": "c.calibdata", "sha256": None}}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(calib), encoding="utf-8")
    assert load_candidate(path)["mean_accuracy_deg"] == pytest.approx(1.1)
