# -*- coding: utf-8 -*-
"""
2026-10-13 のコードレビュー（2回目）への対応（計測側）のテスト。SDK・画面は使わない。
- calibrate_validate：較正の失敗を合否に反映、片眼0件の valid_rate、測り直し閾値 0、ディスプレイ番号の範囲
- v5：meta の time_reference_notes に解析での時刻列の使い分けを追加（primary_analysis_column は残す）
"""
import ast
import math
from pathlib import Path

import pytest

from calibrate_validate import (
    display_point_to_ucs,
    judge,
    parse_display_index,
    point_metrics,
    resolve_retry_threshold,
)

ROOT = Path(__file__).resolve().parent.parent
DA = {"top_left": (-300.0, 360.0, 50.0), "top_right": (300.0, 360.0, 50.0), "bottom_left": (-300.0, 20.0, 50.0)}
GOOD_POINTS = [
    {"label": "g", "kind": "grid", "accuracy_deg": 0.5, "precision_rms_s2s_deg": 0.2},
    {"label": "aoi_credits", "kind": "aoi", "accuracy_deg": 0.8, "precision_rms_s2s_deg": 0.3},
]


class TestCalibrateValidate:
    def test_failed_calibration_is_not_passed(self):
        # 検証はトラッカーに残った前回の較正で行われるので、精度が良くても合格にしない
        v = judge(GOOD_POINTS, 1.0, 1.5, calibration_ok=False)
        assert not v["passed"] and v["calibration_ok"] is False
        assert judge(GOOD_POINTS, 1.0, 1.5, calibration_ok=True)["passed"]
        v = judge(GOOD_POINTS, 1.0, 1.5)  # --validate-only（較正なし）
        assert v["passed"] and v["calibration_ok"] is None

    def test_eye_with_no_valid_samples_has_valid_rate_zero(self):
        t = display_point_to_ucs(0.5, 0.9, DA)
        s = {
            "left_gaze_point_validity": 1, "left_gaze_origin_validity": 1,
            "left_gaze_origin_in_user_coordinate_system": (0.0, 190.0, 700.0),
            "left_gaze_point_in_user_coordinate_system": tuple(t),
            "left_gaze_point_on_display_area": (0.5, 0.9),
            "right_gaze_point_validity": 0, "right_gaze_origin_validity": 0,
            "right_gaze_origin_in_user_coordinate_system": (math.nan,) * 3,
            "right_gaze_point_in_user_coordinate_system": (math.nan,) * 3,
            "right_gaze_point_on_display_area": (math.nan, math.nan),
        }
        m = point_metrics([s] * 10, (0.5, 0.9), DA)
        assert m["right"]["valid_rate"] == 0.0  # 以前はキー自体が無く、calib_compare の最小値から漏れていた
        assert m["right"]["bias_y_norm"] is None
        assert m["left"]["valid_rate"] == 1.0
        assert point_metrics([], (0.5, 0.9), DA)["left"]["valid_rate"] is None

    def test_retry_threshold_zero_is_kept(self):
        assert resolve_retry_threshold(False, 0.0, 1.5) == 0.0  # 以前は 0 が偽として 1.5 に置き換わっていた
        assert resolve_retry_threshold(False, None, 1.5) == 1.5
        assert resolve_retry_threshold(True, 0.0, 1.5) is None

    def test_display_index_range(self):
        assert parse_display_index(" 2 ", 2) == 1
        for bad in ("0", "3", "-1", "a", ""):
            with pytest.raises(ValueError):
                parse_display_index(bad, 2)  # 0 が monitors[-1]（最後の画面）として通っていた


def test_v5_meta_records_analysis_time_columns():
    tree = ast.parse((ROOT / "src" / "gaze_estimation" / "tobii_capture_with_sync_flash_v5.py").read_text(encoding="utf-8"))
    notes = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value == "analysis_time_columns" for k in n.keys)]
    assert len(notes) == 1
    d = {k.value: v for k, v in zip(notes[0].keys, notes[0].values)}
    assert ast.literal_eval(d["primary_analysis_column"]) == "pc_time_sec"  # 既存キーは残す
    assert ast.literal_eval(d["analysis_time_columns"]) == {
        "sync_and_segments": "pc_time_sec", "durations_and_gaps": "system_time_stamp_us"}
