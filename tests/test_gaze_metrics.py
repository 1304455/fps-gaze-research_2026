# -*- coding: utf-8 -*-
"""aoi_analysis-ver3（gaze_metrics / aoi_geometry）のテスト。IMPROVEMENT_PLAN.md の T7 に対応。"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import gaze_metrics as gm
from aoi_geometry import (
    OFFSCREEN_LABEL,
    OUTSIDE_LABEL,
    aoi_extent_deg,
    aoi_mask,
    assign_aoi,
    deg_radius_to_norm,
    find_overlapping_aois,
    load_aoi_config,
    norm_to_deg,
    offscreen_edge,
)
from synthetic import CROSSHAIR, DISPLAY, DT, EMPTY, MINIMAP, hold, make_df, saccade

ROOT = Path(__file__).resolve().parent.parent
AOI_JSON = ROOT / "src" / "aoi_detector" / "valorant_hud_aoi_circular.json"

RECT = {"name": "r", "type": "rectangle", "x_min": 0.2, "x_max": 0.4, "y_min": 0.2, "y_max": 0.4}
CIRC = {"name": "c", "type": "circle", "center_x": 0.5, "center_y": 0.5, "radius_x": 0.1, "radius_y": 0.2}


@pytest.fixture(scope="module")
def aois():
    a, _ = load_aoi_config(AOI_JSON)
    return a


def run(points, aois, segments=None, **param_overrides):
    params = gm.merge_params(param_overrides)
    return gm.analyze_session(make_df(points), aois, DISPLAY, params, segments)


def seg_table(spans, offset=0.0):
    """[(video_start, video_end), ...] → 区間表（alive）。"""
    raw = pd.DataFrame([
        {"round": i + 1, "segment_type": "alive", "video_start_sec": s, "video_end_sec": e,
         "end_reason": "death", "side": "attack", "notes": ""}
        for i, (s, e) in enumerate(spans)
    ])
    return gm.segments_from_video_time(raw, offset, ["alive"])


# ---------------------------------------------------------------------------
# AOI判定
# ---------------------------------------------------------------------------

class TestAoiAssignment:
    def test_rectangle_boundary_min_inclusive_max_exclusive(self):
        x = np.array([0.2, 0.4, 0.3, 0.3, 0.1999])
        y = np.array([0.3, 0.3, 0.2, 0.4, 0.3])
        assert aoi_mask(x, y, RECT).tolist() == [True, False, True, False, False]

    def test_circle_boundary_inclusive_and_elliptic(self):
        x = np.array([0.6, 0.5, 0.6001, 0.57])
        y = np.array([0.5, 0.7, 0.5, 0.64])
        # (0.07/0.1)^2 + (0.14/0.2)^2 = 0.98 <= 1
        assert aoi_mask(x, y, CIRC).tolist() == [True, True, False, True]

    def test_nan_is_never_inside(self):
        assert not aoi_mask(np.array([np.nan]), np.array([0.3]), RECT)[0]

    def test_overlap_priority_follows_json_order(self):
        a = {"name": "a", "type": "rectangle", "x_min": 0.0, "x_max": 0.5, "y_min": 0.0, "y_max": 0.5}
        b = {"name": "b", "type": "rectangle", "x_min": 0.25, "x_max": 0.75, "y_min": 0.25, "y_max": 0.75}
        x, y = np.array([0.3]), np.array([0.3])
        assert assign_aoi(x, y, [a, b])[0] == "a"
        assert assign_aoi(x, y, [b, a])[0] == "b"
        ov = find_overlapping_aois([a, b], grid=200)
        assert len(ov) == 1 and ov[0]["assigned_to"] == "a"
        assert ov[0]["overlap_area_normalized"] == pytest.approx(0.0625, abs=0.005)

    def test_outside(self):
        assert assign_aoi(np.array([0.9]), np.array([0.9]), [RECT, CIRC])[0] == OUTSIDE_LABEL

    def test_offscreen_edge(self):
        x = np.array([0.5, 0.5, -0.1, 1.2, 0.5, -0.05])
        y = np.array([-0.1, 1.05, 0.5, 0.5, 0.5, 1.2])
        assert offscreen_edge(x, y).tolist() == ["top", "bottom", "left", "right", "", "bottom"]

    def test_real_config_loads_with_display(self, aois):
        names = [a["name"] for a in aois]
        assert names[0] == "minimap" and "credits" in names
        assert all("radius_source" in a for a in aois if a["type"] == "circle")


class TestGeometry:
    def test_norm_to_deg_center_is_zero(self):
        tx, ty = norm_to_deg(0.5, 0.5, DISPLAY, 650)
        assert float(tx) == 0.0 and float(ty) == 0.0

    def test_crosshair_radius_matches_known_values(self):
        # 135 px（0.0351）は 60 cm で約2°、65 cm で約1.85°（CLAUDE.md 既知の問題6）
        r_mm = 0.0351 * DISPLAY.width_mm
        assert math.degrees(math.atan(r_mm / 600)) == pytest.approx(2.0, abs=0.02)
        assert math.degrees(math.atan(r_mm / 650)) == pytest.approx(1.85, abs=0.02)

    def test_radius_deg_roundtrip(self):
        rx, ry = deg_radius_to_norm(2.0, DISPLAY, 650)
        c = {"name": "x", "type": "circle", "center_x": 0.5, "center_y": 0.5, "radius_x": rx, "radius_y": ry}
        ext = aoi_extent_deg(c, DISPLAY, 650)
        assert ext["width_deg"] == pytest.approx(4.0, abs=1e-6)
        assert ext["height_deg"] == pytest.approx(4.0, abs=1e-6)

    def test_radius_deg_in_json(self, tmp_path):
        cfg = {
            "coordinate_system": "normalized", "display": DISPLAY.as_dict(), "viewing_distance_mm": 650,
            "aois": [{"name": "crosshair", "type": "circle", "center_x": 0.5, "center_y": 0.5, "radius_deg": 2.0}],
        }
        p = tmp_path / "a.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        aois, _ = load_aoi_config(p)
        assert aois[0]["radius_x"] == pytest.approx(650 * math.tan(math.radians(2)) / 596.7)

    def test_radius_deg_without_display_is_error(self, tmp_path):
        cfg = {"aois": [{"name": "c", "type": "circle", "center_x": 0.5, "center_y": 0.5, "radius_deg": 2.0}]}
        p = tmp_path / "a.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with pytest.raises(ValueError):
            load_aoi_config(p)

    def test_reserved_name_is_error(self, tmp_path):
        cfg = {"aois": [dict(RECT, name="outside")]}
        p = tmp_path / "a.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        with pytest.raises(ValueError):
            load_aoi_config(p)


# ---------------------------------------------------------------------------
# I-DT
# ---------------------------------------------------------------------------

class TestIdt:
    def test_fixation_saccade_fixation(self, aois):
        pts = hold(CROSSHAIR, 20) + saccade(3) + hold(MINIMAP, 20)
        res = run(pts, aois)
        f = res["fixations"]
        assert len(f) == 2
        assert f["aoi"].tolist() == ["crosshair", "minimap"]
        assert f["n_samples"].tolist() == [20, 20]
        assert f["start_sync_sec"].iloc[0] == pytest.approx(0.0)
        assert f["end_sync_sec"].iloc[0] == pytest.approx(19 * DT)
        assert f["start_sync_sec"].iloc[1] == pytest.approx(23 * DT)
        assert f["duration_sec"].iloc[0] == pytest.approx(20 * DT)

    def test_shorter_than_min_duration_is_not_fixation(self, aois):
        # 5サンプル = 83 ms < 100 ms
        res = run(saccade(3) + hold(CROSSHAIR, 5) + saccade(3), aois)
        assert len(res["fixations"]) == 0

    def test_exactly_min_duration_is_fixation(self, aois):
        # 6サンプル = 5×dt + dt = 100 ms
        res = run(saccade(3) + hold(CROSSHAIR, 6) + saccade(3), aois)
        assert len(res["fixations"]) == 1
        assert res["fixations"]["duration_sec"].iloc[0] == pytest.approx(0.1)

    def test_dispersion_threshold_in_degrees(self):
        # 1° 未満のドリフトは1注視、超えると分割される
        t = np.arange(30) * DT
        ax = np.linspace(0, 0.9, 30)
        ay = np.zeros(30)
        assert gm.detect_fixations_idt(t, ax, ay, min_duration_sec=0.1, max_dispersion_deg=1.0, sample_dt=DT) == [(0, 29)]
        ax2 = np.linspace(0, 2.9, 30)
        fx = gm.detect_fixations_idt(t, ax2, ay, min_duration_sec=0.1, max_dispersion_deg=1.0, sample_dt=DT)
        assert len(fx) >= 2
        for s, e in fx:
            assert ax2[e] - ax2[s] <= 1.0 + 1e-9

    def test_fixation_duration_uses_timestamps_not_sample_count(self):
        # サンプル間隔にゆらぎがあっても、時刻差 + 名目間隔で求める
        t = np.array([0.0, 0.016, 0.035, 0.050, 0.068, 0.083, 0.101])
        ax = np.zeros(7)
        fx = gm.detect_fixations_idt(t, ax, ax, min_duration_sec=0.1, max_dispersion_deg=1.0, sample_dt=DT)
        assert fx == [(0, 6)]


# ---------------------------------------------------------------------------
# 欠測ギャップと区間境界
# ---------------------------------------------------------------------------

class TestSplitting:
    def test_blink_splits_fixation_but_not_visit(self, aois):
        # 9サンプル（150 ms）の欠測（瞬目相当）：注視は分割するが、同じ訪問の続き（再訪にしない）
        pts = hold(CROSSHAIR, 20) + [None] * 9 + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        assert len(res["fixations"]) == 2
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["crosshair", "fixation_count"] == 2
        assert ms.loc["crosshair", "visit_count"] == 1
        assert ms.loc["crosshair", "revisit_count"] == 0
        f = res["fixations"]
        assert f["gap_before_prev_ms"].iloc[1] == pytest.approx(150.0, abs=0.01)
        assert res["gap_summary"]["counts"]["75-150ms"] == 1

    def test_long_gap_splits_visit(self, aois):
        # 18サンプル（300 ms）の欠測 > 200 ms：追跡ロスとして訪問を切る
        pts = hold(CROSSHAIR, 20) + [None] * 18 + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["crosshair", "visit_count"] == 2
        assert ms.loc["crosshair", "revisit_count"] == 1
        assert res["gap_summary"]["n_same_aoi_visit_breaks_by_long_gap"] == 1

    def test_visit_merge_threshold_boundary(self, aois):
        # 12サンプル = 200 ms ちょうどはつなぐ、13サンプル = 216.7 ms は切る
        for n_missing, visits in ((12, 1), (13, 2)):
            res = run(hold(CROSSHAIR, 20) + [None] * n_missing + hold(CROSSHAIR, 20), aois)
            assert res["metrics_session"].set_index("aoi").loc["crosshair", "visit_count"] == visits

    def test_visit_merge_disabled(self, aois):
        pts = hold(CROSSHAIR, 20) + [None] * 9 + hold(CROSSHAIR, 20)
        res = run(pts, aois, visit_merge_max_gap_ms=0.0)
        assert res["metrics_session"].set_index("aoi").loc["crosshair", "revisit_count"] == 1

    def test_transition_across_blink_is_counted(self, aois):
        # 瞬目中に視線が移った場合は遷移として数える。長い欠測をまたぐ場合は数えない
        res = run(hold(MINIMAP, 20) + [None] * 9 + hold(CROSSHAIR, 20), aois)
        tl = res["transitions_long"].set_index(["from_aoi", "to_aoi"])["count"]
        assert tl[("minimap", "crosshair")] == 1
        res = run(hold(MINIMAP, 20) + [None] * 30 + hold(CROSSHAIR, 20), aois)
        tl = res["transitions_long"].set_index(["from_aoi", "to_aoi"])["count"]
        assert tl[("minimap", "crosshair")] == 0

    def test_bridge_requires_every_gap_short(self, aois):
        # 注視 → 100 ms 欠測 → 注視にならない3サンプル → 300 ms 欠測 → 同じAOIの注視：切る
        pts = hold(CROSSHAIR, 20) + [None] * 6 + hold(EMPTY, 3) + [None] * 18 + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        assert res["metrics_session"].set_index("aoi").loc["crosshair", "visit_count"] == 2
        pts = hold(CROSSHAIR, 20) + [None] * 6 + hold(EMPTY, 3) + [None] * 6 + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        assert res["metrics_session"].set_index("aoi").loc["crosshair", "visit_count"] == 1

    def test_max_gap_is_missing_duration(self, aois):
        # 欠測の長さ = 時刻差 − 1サンプル間隔。4サンプル欠測 = 66.7 ms <= 75 は分割しない、5サンプル = 83 ms は分割
        assert len(run(hold(CROSSHAIR, 20) + [None] * 4 + hold(CROSSHAIR, 20), aois)["fixations"]) == 1
        assert len(run(hold(CROSSHAIR, 20) + [None] * 5 + hold(CROSSHAIR, 20), aois)["fixations"]) == 2

    def test_short_gap_keeps_one_fixation(self, aois):
        # 2サンプル（33 ms）の欠測 → 有効サンプル間 50 ms <= 75 ms
        pts = hold(CROSSHAIR, 20) + [None] * 2 + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        assert len(res["fixations"]) == 1
        assert res["fixations"]["n_samples"].iloc[0] == 40

    def test_segment_boundary_splits_fixation(self, aois):
        # 連続して同じ点を見ていても、区間境界で注視・訪問が分かれる
        pts = hold(CROSSHAIR, 60)
        seg = seg_table([(0.0, 30 * DT - 1e-9), (30 * DT - 1e-9, 61 * DT)])
        res = run(pts, aois, segments=seg)
        f = res["fixations"]
        assert len(f) == 2
        assert sorted(f["segment_id"].tolist()) == [0, 1]
        assert res["transitions_long"]["count"].sum() == 0

    def test_transitions_do_not_cross_segments(self, aois):
        # 区間0の最後が crosshair、区間1の最初が minimap。その間の遷移は数えない
        pts = hold(MINIMAP, 20) + hold(CROSSHAIR, 20) + hold(MINIMAP, 20) + hold(EMPTY, 20)
        seg = seg_table([(0.0, 40 * DT - 1e-9), (40 * DT - 1e-9, 81 * DT)])
        res = run(pts, aois, segments=seg)
        tl = res["transitions_long"].set_index(["from_aoi", "to_aoi"])["count"]
        assert tl[("minimap", "crosshair")] == 1
        assert tl[("crosshair", "minimap")] == 0
        assert tl[("minimap", OUTSIDE_LABEL)] == 1

    def test_samples_outside_segments_are_kept_but_not_counted(self, aois):
        pts = hold(MINIMAP, 30) + hold(CROSSHAIR, 30)
        seg = seg_table([(30 * DT - 1e-9, 61 * DT)])
        res = run(pts, aois, segments=seg)
        s = res["samples"]
        assert len(s) == 60  # 行は削除しない
        assert s["segment_id"].isna().sum() == 30
        assert (s["sample_class"] == gm.OUT_OF_SEGMENT).sum() == 30
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["minimap", "n_samples_in_aoi"] == 0
        assert ms.loc["minimap", "fixation_count"] == 0
        assert ms.loc["crosshair", "gaze_pct"] == pytest.approx(100.0)

    def test_segment_offset_conversion_and_validation(self):
        seg = seg_table([(10.0, 20.0)], offset=1.5)
        assert seg["gaze_start_sec"].iloc[0] == pytest.approx(11.5)
        assert seg["gaze_end_sec"].iloc[0] == pytest.approx(21.5)
        raw = pd.DataFrame([
            {"round": 1, "segment_type": "alive", "video_start_sec": 0, "video_end_sec": 10},
            {"round": 2, "segment_type": "alive", "video_start_sec": 5, "video_end_sec": 15},
        ])
        with pytest.raises(ValueError):
            gm.segments_from_video_time(raw, 0.0, ["alive"])
        raw_bad = pd.DataFrame([{"round": 1, "segment_type": "alive", "video_start_sec": 5, "video_end_sec": 5}])
        with pytest.raises(ValueError):
            gm.segments_from_video_time(raw_bad, 0.0, ["alive"])

    def test_segment_type_filter(self):
        raw = pd.DataFrame([
            {"round": 1, "segment_type": "buy", "video_start_sec": 0, "video_end_sec": 5},
            {"round": 1, "segment_type": "alive", "video_start_sec": 5, "video_end_sec": 10},
        ])
        seg = gm.segments_from_video_time(raw, 0.0, ["alive"])
        assert len(seg) == 1 and seg["segment_type"].iloc[0] == "alive"


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_all_aoi_rows_are_output_even_if_zero(self, aois):
        res = run(hold(CROSSHAIR, 30), aois)
        expected = [a["name"] for a in aois] + [OUTSIDE_LABEL, OFFSCREEN_LABEL]
        assert res["metrics_session"]["aoi"].tolist() == expected
        by_seg = res["metrics_by_segment"]
        assert by_seg.groupby("segment_id")["aoi"].apply(list).iloc[0] == expected
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["credits", "fixation_count"] == 0
        assert ms.loc["credits", "gaze_pct"] == 0.0
        assert math.isnan(ms.loc["credits", "mean_fixation_duration_sec"])
        assert res["transitions_matrix"].shape == (len(expected), len(expected))

    def test_gaze_pct_denominator_is_valid_samples(self, aois):
        pts = hold(CROSSHAIR, 30) + [None] * 20 + hold(EMPTY, 10)
        res = run(pts, aois)
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["crosshair", "gaze_pct_denominator_samples"] == 40
        assert ms.loc["crosshair", "gaze_pct"] == pytest.approx(75.0)
        assert ms.loc[OUTSIDE_LABEL, "gaze_pct"] == pytest.approx(25.0)

    def test_offscreen_denominator_option(self, aois):
        pts = hold(CROSSHAIR, 30) + hold((0.5, 1.05), 10)
        with_off = run(pts, aois).get("metrics_session").set_index("aoi")
        assert with_off.loc["crosshair", "gaze_pct"] == pytest.approx(75.0)
        assert with_off.loc[OFFSCREEN_LABEL, "gaze_pct"] == pytest.approx(25.0)
        without = run(pts, aois, offscreen_in_denominator=False)["metrics_session"].set_index("aoi")
        assert without.loc["crosshair", "gaze_pct"] == pytest.approx(100.0)
        assert math.isnan(without.loc[OFFSCREEN_LABEL, "gaze_pct"])

    def test_offscreen_is_not_outside_and_edge_is_recorded(self, aois):
        res = run(hold(CROSSHAIR, 10) + hold((0.5, 1.05), 10), aois)
        s = res["samples"]
        assert (s["sample_class"] == OFFSCREEN_LABEL).sum() == 10
        assert gm.offscreen_summary(s)["n_bottom"] == 10

    def test_sample_classes(self, aois):
        # 注視に属するAOI内 / 注視に属さないAOI内（3サンプルだけ通過）/ 無効
        pts = hold(CROSSHAIR, 20) + [MINIMAP, EMPTY, MINIMAP] + [None] * 2 + hold(EMPTY, 20)
        res = run(pts, aois)
        c = res["samples"]["sample_class"].value_counts()
        assert c["aoi_fixation"] == 20
        assert c["aoi_nonfixation"] == 2
        assert c["invalid"] == 2
        assert set(res["samples"]["sample_class"]) <= set(gm.SAMPLE_CLASSES)

    def test_ttff_and_censoring(self, aois):
        pts = hold(EMPTY, 30) + hold(MINIMAP, 30)
        seg = seg_table([(0.0, 61 * DT)])
        res = run(pts, aois, segments=seg)
        bs = res["metrics_by_segment"].set_index("aoi")
        assert bs.loc["minimap", "ttff_sec"] == pytest.approx(30 * DT)
        assert not bs.loc["minimap", "ttff_censored"]
        assert math.isnan(bs.loc["credits", "ttff_sec"])
        assert bs.loc["credits", "ttff_censored"]

    def test_session_aggregation_sums_counts_not_mean_of_ratios(self, aois):
        # 区間0：crosshair 100%（30サンプル）、区間1：crosshair 0%（90サンプル）
        pts = hold(CROSSHAIR, 30) + hold(EMPTY, 90)
        seg = seg_table([(0.0, 30 * DT - 1e-9), (30 * DT - 1e-9, 121 * DT)])
        res = run(pts, aois, segments=seg)
        ms = res["metrics_session"].set_index("aoi")
        assert ms.loc["crosshair", "gaze_pct"] == pytest.approx(25.0)  # 平均すると50%になってしまう

    def test_fixations_per_min(self, aois):
        pts = hold(CROSSHAIR, 20) + saccade(3) + hold(MINIMAP, 20) + saccade(3) + hold(CROSSHAIR, 20)
        res = run(pts, aois)
        ms = res["metrics_session"].set_index("aoi")
        valid_min = len(pts) * res["sample_dt"] / 60
        assert ms.loc["crosshair", "fixations_per_min"] == pytest.approx(2 / valid_min)
        assert ms.loc["crosshair", "revisit_count"] == 1

    def test_invalid_rows_are_not_removed(self, aois):
        pts = hold(CROSSHAIR, 10) + [None] * 5
        res = run(pts, aois)
        assert len(res["samples"]) == 15

    def test_non_monotonic_time_is_error(self, aois):
        df = make_df(hold(CROSSHAIR, 10))
        df = pd.concat([df.iloc[5:], df.iloc[:5]], ignore_index=True)
        with pytest.raises(ValueError):
            gm.analyze_session(df, aois, DISPLAY, gm.merge_params(None), None)


class TestParamsAndQc:
    def test_unknown_param_is_error(self):
        with pytest.raises(ValueError):
            gm.merge_params({"max_gap": 1})

    def test_default_params_file_matches_code(self):
        p = json.loads((ROOT / "src" / "aoi_detector" / "analysis_params_default.json").read_text(encoding="utf-8"))
        p.pop("_comment")
        assert p == gm.DEFAULT_PARAMS

    def test_time_base_comparison(self):
        df = make_df(hold(CROSSHAIR, 100))
        out = gm.time_base_comparison(df)
        assert out["available"] and out["interval_difference_sd_ms"] < 0.01

    def test_system_time_column_option(self, aois):
        res = run(hold(CROSSHAIR, 30), aois, duration_time_column="system_time_stamp_us")
        assert res["fixations"]["duration_sec"].iloc[0] == pytest.approx(30 * DT, abs=1e-5)

    def test_eye_distance_summary(self):
        df = make_df(hold(CROSSHAIR, 10))
        assert not gm.eye_distance_summary(df)["available"]
        df["left_gaze_origin_z_mm"] = 640.0
        df["right_gaze_origin_z_mm"] = 660.0
        df["left_gaze_origin_validity"] = 1
        df["right_gaze_origin_validity"] = 1
        assert gm.eye_distance_summary(df)["median_mm"] == pytest.approx(650.0)


class TestAoiCheck:
    @staticmethod
    def calib():
        def pt(label, kind, x, y, acc, bx, by, aoi=None):
            d = {"label": label, "kind": kind, "x": x, "y": y, "accuracy_deg": acc, "precision_rms_s2s_deg": 0.2,
                 "left": {"bias_x_norm": bx, "bias_y_norm": by}, "right": {"bias_x_norm": bx, "bias_y_norm": by}}
            if aoi:
                d["aoi"] = aoi
            return d
        return {
            "validation_points": [
                pt("grid_0.5_0.5", "grid", 0.5, 0.5, 1.37, 0.0, 0.01),
                pt("aoi_credits", "aoi", 0.96, 0.97, 0.67, 0.0, 0.02, "credits"),          # 下に 0.02 → 外
                pt("aoi_ally_team_status", "aoi", 0.3265, 0.0475, 1.34, 0.06, 0.0, "ally_team_status"),  # 横ずれ → 内
            ],
            "summary": {"mean_accuracy_deg": 0.8},
        }

    def test_size_table_flags(self, aois):
        from aoi_check import size_table
        t = size_table(aois, DISPLAY, 650, self.calib(), 1.0).set_index("aoi")
        assert t.loc["credits", "flag_below_min_size"]
        assert t.loc["credits", "flag_half_below_accuracy"]  # 0.84/2 < 0.67
        assert t.loc["credits", "flag_center_gaze_outside"]
        assert t.loc["credits", "accuracy_source"] == "calib: このAOIの中心"
        assert t.loc["credits", "bias_y_deg"] > 0  # 正は下方向
        # 横長のバーは、横方向のずれなら誤差が大きくても中心を見た視線は中に入る
        assert t.loc["ally_team_status", "flag_half_below_accuracy"]
        assert not t.loc["ally_team_status", "flag_center_gaze_outside"]
        # 中央のグリッド点と重複して省かれた crosshair は、近傍の検証点の値を使う
        assert t.loc["crosshair", "accuracy_deg"] == 1.37
        assert "grid_0.5_0.5" in t.loc["crosshair", "accuracy_source"]
        # 検証点の無い AOI は全点平均で、方向の判定はしない
        assert t.loc["minimap", "accuracy_deg"] == 0.8
        assert t.loc["minimap", "flag_center_gaze_outside"] is None
        assert t.loc["crosshair", "width_deg"] / 2 == pytest.approx(1.85, abs=0.01)
