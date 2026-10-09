# -*- coding: utf-8 -*-
"""calibrate_validate の計算部分のテスト（SDK・画面は使わない）。"""
import math

import numpy as np
import pytest

from calibrate_validate import (
    display_point_to_ucs,
    eye_metrics,
    judge,
    point_metrics,
    validation_targets,
)

# 幅 600 mm × 高さ 340 mm、トラッカー原点から画面下端が少し上・奥にある想定の表示領域
DA = {"top_left": (-300.0, 360.0, 50.0), "top_right": (300.0, 360.0, 50.0), "bottom_left": (-300.0, 20.0, 50.0)}
EYE = np.array([0.0, 190.0, 700.0])  # 画面中央の正面 650 mm


def test_display_point_to_ucs_corners_and_center():
    assert display_point_to_ucs(0, 0, DA).tolist() == [-300, 360, 50]
    assert display_point_to_ucs(1, 1, DA).tolist() == [300, 20, 50]
    assert display_point_to_ucs(0.5, 0.5, DA).tolist() == [0, 190, 50]


def test_accuracy_of_known_offset():
    target = display_point_to_ucs(0.5, 0.5, DA)
    # 目標から水平に 650*tan(1°) ずらした点を見ている → 1°
    off = 650 * math.tan(math.radians(1.0))
    gaze = target + np.array([off, 0, 0])
    m = eye_metrics(np.tile(EYE, (10, 1)), np.tile(gaze, (10, 1)), target)
    assert m["accuracy_deg"] == pytest.approx(1.0, abs=1e-6)
    assert m["precision_rms_s2s_deg"] == pytest.approx(0.0, abs=1e-6)


def test_precision_alternating_samples():
    target = display_point_to_ucs(0.5, 0.5, DA)
    off = 650 * math.tan(math.radians(0.25))
    gazes = np.array([target + np.array([off if i % 2 else -off, 0, 0]) for i in range(20)])
    m = eye_metrics(np.tile(EYE, (20, 1)), gazes, target)
    assert m["precision_rms_s2s_deg"] == pytest.approx(0.5, abs=1e-3)
    assert m["accuracy_deg"] == pytest.approx(0.25, abs=1e-3)


def test_point_metrics_uses_only_valid_eyes():
    target_xy = (0.5, 0.5)
    t = display_point_to_ucs(*target_xy, DA)
    good = {
        "left_gaze_point_validity": 1, "left_gaze_origin_validity": 1,
        "left_gaze_origin_in_user_coordinate_system": tuple(EYE),
        "left_gaze_point_in_user_coordinate_system": tuple(t),
        "left_gaze_point_on_display_area": (0.5, 0.52),
        "right_gaze_point_validity": 0, "right_gaze_origin_validity": 0,
        "right_gaze_origin_in_user_coordinate_system": (math.nan,) * 3,
        "right_gaze_point_in_user_coordinate_system": (math.nan,) * 3,
        "right_gaze_point_on_display_area": (math.nan, math.nan),
    }
    m = point_metrics([good] * 5, target_xy, DA)
    assert m["left"]["n_samples"] == 5 and m["right"]["n_samples"] == 0
    assert m["accuracy_deg"] == pytest.approx(0.0, abs=1e-6)
    assert m["left"]["bias_y_norm"] == pytest.approx(0.02)


def test_validation_targets_dedup_and_aoi_centers():
    aois = [
        {"name": "crosshair", "type": "circle", "center_x": 0.5, "center_y": 0.5, "radius_x": 0.03, "radius_y": 0.06},
        {"name": "credits", "type": "rectangle", "x_min": 0.935, "x_max": 0.985, "y_min": 0.955, "y_max": 0.985},
    ]
    t = validation_targets(aois)
    labels = [x["label"] for x in t]
    assert "aoi_crosshair" not in labels  # 中央のグリッド点と重複
    cr = [x for x in t if x["label"] == "aoi_credits"][0]
    assert cr["x"] == pytest.approx(0.96) and cr["y"] == pytest.approx(0.97)


def test_judge():
    pts = [
        {"label": "g", "kind": "grid", "accuracy_deg": 0.6, "precision_rms_s2s_deg": 0.2},
        {"label": "aoi_credits", "kind": "aoi", "accuracy_deg": 1.8, "precision_rms_s2s_deg": 0.3},
    ]
    v = judge(pts, 1.0, 1.5)
    assert not v["passed"] and v["hud_points_failed"] == ["aoi_credits"]
    pts[1]["accuracy_deg"] = 1.2
    assert judge(pts, 1.0, 1.5)["passed"]
