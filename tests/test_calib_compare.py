# -*- coding: utf-8 -*-
"""calib_compare（キャリブレーション検証結果の比較）のテスト：ずれ方の分かっている calib JSON を合成して確認する。"""
import json
import math

import pytest

import calib_compare as cc

GRID = [(x, y) for y in (0.1, 0.5, 0.9) for x in (0.1, 0.5, 0.9)]
HUD = [("ally_team_status", 0.3265, 0.0475), ("minimap", 0.1185, 0.23), ("health_armor", 0.33, 0.94)]


def make_calib(v_scale=0.04, h_scale=0.0, v_offset=0.0, angle_deg=20.0, height_mm=339.4, z_mm=650.0,
               left_valid=None):
    """bias = offset − scale·(target − 0.5) になる calib JSON を作る（左右眼は同じずれ）。"""
    a = math.radians(angle_deg)
    bl = [-300.0, 27.0, 3.0]
    tl = [-300.0, 27.0 + height_mm * math.cos(a), 3.0 + height_mm * math.sin(a)]
    pts = []
    targets = [("grid_%s_%s" % (x, y), "grid", None, x, y) for x, y in GRID]
    targets += [("aoi_" + n, "aoi", n, x, y) for n, x, y in HUD]
    for label, kind, aoi, x, y in targets:
        eye = {"accuracy_deg": 0.5, "precision_rms_s2s_deg": 0.1, "valid_rate": 1.0,
               "bias_x_norm": -h_scale * (x - 0.5), "bias_y_norm": v_offset - v_scale * (y - 0.5)}
        left = dict(eye, accuracy_deg=0.8)
        if left_valid and label == left_valid[0]:
            left["valid_rate"] = left_valid[1]
        p = {"label": label, "kind": kind, "target_x": x, "target_y": y, "accuracy_deg": 0.65,
             "left": left, "right": dict(eye)}
        if aoi:
            p["aoi"] = aoi
        pts.append(p)
    pts[-1]["accuracy_deg"] = 1.2
    return {
        "created_at_utc": "2026-10-13T00:00:00+00:00", "subject_id": "TEST", "script": {"version": "1.2.0"},
        "parameters": {"calib_margin": 0.1},
        "display_area_ucs_mm": {"top_left": tl, "bottom_left": bl, "top_right": [303.0, tl[1], tl[2]],
                                "width_mm": 603.0, "height_mm": height_mm},
        "head_position": {"recorded": {"x_mid_mm": 0.0, "y_mid_mm": -10.0, "z_mid_mm": z_mm}},
        "validation_points": pts,
        "summary": {"mean_accuracy_deg": 0.7, "passed": True, "retried_points": ["grid_0.9_0.1"]},
    }


def test_vertical_scale_and_bars_are_recovered():
    row = cc.summarize_calib(make_calib(v_scale=0.04, h_scale=0.01), "a.json")
    assert row["vertical_scale_pct"] == pytest.approx(4.0)
    assert row["horizontal_scale_pct"] == pytest.approx(1.0)
    assert row["vertical_offset_deg"] == pytest.approx(0.0, abs=1e-9)
    # 上部HUD（y=0.0475）は下向き（正）、下部HUD（y=0.94）は上向き（負）。minimap（y=0.23）は上部に含めない
    exp_top = math.degrees(math.atan(0.04 * (0.5 - 0.0475) * 339.4 / 650.0))
    exp_bottom = math.degrees(math.atan(-0.04 * (0.94 - 0.5) * 339.4 / 650.0))
    assert row["top_bar_bias_y_deg"] == pytest.approx(exp_top)
    assert row["bottom_bar_bias_y_deg"] == pytest.approx(exp_bottom)
    assert row["hud_max_accuracy_deg"] == pytest.approx(1.2)
    assert row["hud_worst_point"] == "aoi_health_armor"
    assert row["n_retried"] == 1


def test_offset_is_separated_from_scale():
    row = cc.summarize_calib(make_calib(v_scale=0.0, v_offset=0.01), "b.json")
    assert row["vertical_scale_pct"] == pytest.approx(0.0, abs=1e-9)
    assert row["vertical_offset_deg"] == pytest.approx(math.degrees(math.atan(0.01 * 339.4 / 650.0)))


@pytest.mark.parametrize("angle", [10.0, 20.0])
def test_display_angle_from_ucs(angle):
    row = cc.summarize_calib(make_calib(angle_deg=angle), "c.json")
    assert row["display_angle_deg"] == pytest.approx(angle)


def test_eye_columns_and_one_eye_missing():
    calib = make_calib(left_valid=("grid_0.1_0.1", 0.67))
    calib["validation_points"][0]["left"]["bias_y_norm"] = None  # 片眼だけのずれでも計算できる
    row = cc.summarize_calib(calib, "d.json")
    assert row["left_mean_accuracy_deg"] == pytest.approx(0.8)
    assert row["right_mean_accuracy_deg"] == pytest.approx(0.5)
    assert row["left_min_valid_rate"] == pytest.approx(0.67)
    assert row["left_min_valid_point"] == "grid_0.1_0.1"
    assert not math.isnan(row["vertical_scale_pct"])


def test_old_calib_without_head_position_uses_default_distance():
    calib = make_calib()
    del calib["head_position"]
    row = cc.summarize_calib(calib, "e.json")
    assert row["head_z_mm"] is None
    assert row["top_bar_bias_y_deg"] > 0


def test_expand_inputs_and_group_summary(tmp_path):
    for i, (scale, angle) in enumerate([(0.04, 20.0), (0.02, 20.0), (0.01, 15.0)]):
        (tmp_path / f"calib_TEST_2026101{i}_000000.json").write_text(
            json.dumps(make_calib(v_scale=scale, angle_deg=angle)), encoding="utf-8")
    (tmp_path / "headpos_TEST_x.json").write_text("{}", encoding="utf-8")
    by_dir = cc.expand_inputs([str(tmp_path)])
    by_glob = cc.expand_inputs([str(tmp_path / "calib_TEST_*.json"), str(by_dir[0])])
    assert [p.name for p in by_dir] == [p.name for p in by_glob]
    assert len(by_dir) == 3
    df = cc.compare(by_dir)
    g = cc.group_summary(df, "display_angle_deg").set_index("display_angle_deg")
    assert g.loc[20.0, "n_runs"] == 2
    assert g.loc[20.0, "vertical_scale_pct"] == pytest.approx(3.0)
    assert g.loc[15.0, "vertical_scale_pct"] == pytest.approx(1.0)


def test_main_writes_csv(tmp_path, capsys):
    f = tmp_path / "calib_TEST_20261013_000000.json"
    f.write_text(json.dumps(make_calib()), encoding="utf-8")
    out = tmp_path / "out" / "cmp.csv"
    cc.main([str(f), "--out", str(out)])
    assert out.exists()
    assert "calib_margin ごとの平均" in capsys.readouterr().out
