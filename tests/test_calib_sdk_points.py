# -*- coding: utf-8 -*-
"""
calibrate_validate 1.6.0（F2）：SDK の較正結果の点ごと・眼ごとの記録のテスト。
SDK のクラスは tobii-research 2.1.0 と同じ属性名の偽物で代用する。
"""
import math
from types import SimpleNamespace

import pytest

from calib_compare import calib_point_usage
from calibrate_validate import run_calibration, summarize_calibration_point, summarize_calibration_result

USED, NOT_USED, INVALID = "validity_valid_and_used", "validity_valid_but_not_used", "validity_invalid_and_not_used"


def _eye(pos, validity):
    return SimpleNamespace(position_on_display_area=pos, validity=validity)


def _point(xy, samples):
    """samples: [((左の位置, 左の validity), (右の位置, 右の validity)), ...]"""
    return SimpleNamespace(position_on_display_area=xy,
                           calibration_samples=tuple(SimpleNamespace(left_eye=_eye(*l), right_eye=_eye(*r))
                                                     for l, r in samples))


def test_counts_rates_and_offsets_per_eye():
    # 左眼：4サンプル中3つ使用（点の少し右下）、1つは遠すぎて不使用（右へ大きく）。右眼：2つ無効
    p = _point((0.1, 0.5), [
        (((0.11, 0.51), USED), ((0.10, 0.50), USED)),
        (((0.11, 0.51), USED), ((0.10, 0.50), USED)),
        (((0.11, 0.51), USED), ((math.nan, math.nan), INVALID)),
        (((0.40, 0.50), NOT_USED), ((math.nan, math.nan), INVALID)),
    ])
    s = summarize_calibration_point(p)
    assert (s["x"], s["y"], s["n_samples"]) == (0.1, 0.5, 4)
    assert (s["left_n_used"], s["left_n_valid_not_used"], s["left_n_invalid"]) == (3, 1, 0)
    assert (s["right_n_used"], s["right_n_valid_not_used"], s["right_n_invalid"]) == (2, 0, 2)
    assert s["left_used_rate"] == pytest.approx(0.75) and s["right_used_rate"] == pytest.approx(0.5)
    assert s["left_used_offset_x_norm"] == pytest.approx(0.01) and s["left_used_offset_y_norm"] == pytest.approx(0.01)
    assert s["left_not_used_offset_x_norm"] == pytest.approx(0.30)  # 使わなかったサンプルの向き（先読みの手がかり）
    assert s["right_not_used_offset_x_norm"] is None
    assert s["left_n_unknown_validity"] == 0


def test_unknown_validity_is_counted_not_dropped():
    s = summarize_calibration_point(_point((0.5, 0.5), [(((0.5, 0.5), 1), ((0.5, 0.5), USED))]))
    assert s["left_n_unknown_validity"] == 1 and s["left_n_used"] == 0 and s["right_n_used"] == 1


def test_point_without_samples():
    s = summarize_calibration_point(_point((0.5, 0.5), []))
    assert s["n_samples"] == 0 and s["left_used_rate"] is None and s["left_n_used"] == 0


def test_result_failure_returns_empty_list():
    # 両眼の成功以外では SDK が点の一覧を空で返す
    pts, err = summarize_calibration_result(SimpleNamespace(status="calibration_status_failure", calibration_points=()))
    assert pts == [] and err is None


def test_unreadable_sdk_attributes_do_not_stop_calibration():
    broken = SimpleNamespace(position_on_display_area=(0.9, 0.1))  # calibration_samples が無い
    pts, err = summarize_calibration_result(SimpleNamespace(calibration_points=(broken,)))
    assert pts == [{"x": 0.9, "y": 0.1}]
    assert err is not None and "AttributeError" in err


class _FakeCalibration:
    def __init__(self, eyetracker):
        self.collected = []

    def enter_calibration_mode(self):
        pass

    def leave_calibration_mode(self):
        pass

    def collect_data(self, x, y):
        self.collected.append((x, y))
        return "calibration_status_success"

    def compute_and_apply(self):
        pts = tuple(_point(xy, [(((xy[0], xy[1]), USED), ((xy[0], xy[1]), USED))] * 3) for xy in self.collected)
        return SimpleNamespace(status="calibration_status_success", calibration_points=pts)


class _FakeScreen:
    def target(self, x, y, shrink_sec):
        return True


def test_run_calibration_records_details():
    tr = SimpleNamespace(ScreenBasedCalibration=_FakeCalibration, CALIBRATION_STATUS_SUCCESS="calibration_status_success")
    out = run_calibration(tr, object(), _FakeScreen(), 0.0, [(0.5, 0.5), (0.1, 0.1)])
    assert out["succeeded"] and out["points_detail_error"] is None
    assert [(p["x"], p["y"]) for p in out["points_used_by_sdk"]] == [(0.5, 0.5), (0.1, 0.1)]  # 既存の x, y は残る
    assert out["points_used_by_sdk"][0]["left_n_used"] == 3
    assert out["points"][0]["first_collect_status"] == "calibration_status_success"


def test_calib_compare_min_used_rate():
    calib = {"points_used_by_sdk": [
        {"x": 0.5, "y": 0.5, "left_used_rate": 1.0, "right_used_rate": 0.9},
        {"x": 0.1, "y": 0.5, "left_used_rate": 0.2, "right_used_rate": 0.95},
    ]}
    assert calib_point_usage(calib) == {"calib_min_used_rate": 0.2, "calib_min_used_point": "calib_0.10_0.50_left"}
    old = calib_point_usage({"points_used_by_sdk": [{"x": 0.5, "y": 0.5}]})  # 1.5.0 以前
    assert math.isnan(old["calib_min_used_rate"]) and old["calib_min_used_point"] is None
    assert calib_point_usage({"status": "not_performed (--validate-only)"})["calib_min_used_point"] is None
