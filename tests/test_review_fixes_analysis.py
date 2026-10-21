# -*- coding: utf-8 -*-
"""
2026-10-13 のコードレビュー指摘への対応（解析側）のテスト。
- 指摘1：PC 側の受信遅延（pc_time_sec だけが飛ぶ）で注視・訪問を切らない（既定の時刻は system_time_stamp_us）
- 指摘7：開始/終了が空の区間はエラー、サンプル0件の区間は消さずに残す、全区間0件は ver3 が止める
- 指摘10：aoi_fixation は「サンプルの AOI ＝ 属する注視の AOI」のときだけ
- 指摘5：ver3 は条件の違う既存の結果を上書きしない
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import gaze_metrics as gm
from aoi_geometry import load_aoi_config
from synthetic import CROSSHAIR, DISPLAY, DT, EMPTY, MINIMAP, hold, make_df

ROOT = Path(__file__).resolve().parent.parent
AOI_JSON = ROOT / "src" / "aoi_detector" / "valorant_hud_aoi_circular.json"


@pytest.fixture(scope="module")
def aois():
    a, _ = load_aoi_config(AOI_JSON)
    return a


@pytest.fixture(scope="module")
def ver3():
    spec = importlib.util.spec_from_file_location("aoi_analysis_ver3", ROOT / "src" / "aoi_detector" / "aoi_analysis-ver3.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def host_stall(df: pd.DataFrame, at: int, stall_sec: float) -> pd.DataFrame:
    """at 行目以降のコールバック到着（pc_time_sec）だけを stall_sec 遅らせる。デバイス時刻は等間隔のまま。"""
    df = df.copy()
    df.loc[at:, "pc_time_sec"] = df.loc[at:, "pc_time_sec"] + stall_sec
    return df


class TestTimeBase:
    def test_default_is_system_time(self):
        assert gm.DEFAULT_PARAMS["duration_time_column"] == "system_time_stamp_us"

    def test_host_stall_does_not_split_fixation(self, aois):
        df = host_stall(make_df(hold(CROSSHAIR, 120)), at=60, stall_sec=0.25)
        res = gm.analyze_session(df, aois, DISPLAY, gm.merge_params(None), None)
        f = res["fixations"]
        assert len(f) == 1
        assert f["duration_sec"].iloc[0] == pytest.approx(120 * DT, abs=1e-5)
        assert res["gap_summary"]["n_gaps"] == 0
        assert res["metrics_session"].set_index("aoi").loc["crosshair", "revisit_count"] == 0

    def test_pc_time_would_split_and_create_revisit(self, aois):
        # 旧既定（pc_time_sec）では、データは欠けていないのに注視が割れ、200 ms 超なので再訪にもなる
        df = host_stall(make_df(hold(CROSSHAIR, 120)), at=60, stall_sec=0.25)
        res = gm.analyze_session(df, aois, DISPLAY, gm.merge_params({"duration_time_column": "pc_time_sec"}), None)
        assert len(res["fixations"]) == 2
        assert res["metrics_session"].set_index("aoi").loc["crosshair", "revisit_count"] == 1

    def test_segments_still_use_pc_time(self, aois):
        # 区間付与は同期と同じ pc_time_sec のまま（動画時刻 + offset と比べるため）
        df = make_df(hold(CROSSHAIR, 60))
        seg = gm.whole_session_segment(np.array([30 * DT, 59 * DT]), DT)
        res = gm.analyze_session(df, aois, DISPLAY, gm.merge_params(None), seg)
        assert res["samples"]["segment_id"].notna().sum() == 30


class TestSegments:
    def test_blank_segment_time_is_error(self):
        raw = pd.DataFrame([
            {"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": 10.0},
            {"round": 2, "segment_type": "alive", "video_start_sec": 20.0, "video_end_sec": np.nan},
        ])
        with pytest.raises(ValueError, match="round=\\[2\\]"):
            gm.segments_from_video_time(raw, 0.0, ["alive"])

    def test_empty_segment_is_kept_in_counts(self, aois):
        raw = pd.DataFrame([
            {"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": 30 * DT},
            {"round": 2, "segment_type": "alive", "video_start_sec": 500.0, "video_end_sec": 600.0},  # 記録の外
        ])
        seg = gm.segments_from_video_time(raw, 0.0, ["alive"])
        res = gm.analyze_session(make_df(hold(CROSSHAIR, 60)), aois, DISPLAY, gm.merge_params(None), seg)
        counts = gm.segment_sample_counts(res["samples"], res["segments"], res["sample_dt"]).set_index("segment_id")
        assert counts.loc[0, "n_samples"] == 30
        assert counts.loc[1, "n_samples"] == 0
        assert pd.isna(counts.loc[1, "valid_rate"])


class TestSampleClass:
    def test_sample_in_aoi_but_fixation_elsewhere_is_nonfixation(self, aois):
        # 1つの注視：15サンプルは crosshair の円の外、5サンプルは円の内側の縁（分散は 1° 未満）。
        # 重心は円の外なので注視の AOI は outside。円内の5サンプルは「AOIへの注視」ではない
        # 半径は AOI JSON から取る（10/21 に radius_deg=2.0 へ変更。値を直書きすると定義の変更で壊れる）
        cx, cy = CROSSHAIR
        r = next(a for a in aois if a["name"] == "crosshair")["radius_x"]
        outside_pt, inside_pt = (cx + r + 0.003, cy), (cx + r - 0.001, cy)
        pts = [outside_pt] * 15 + [inside_pt] * 5
        res = gm.analyze_session(make_df(pts), aois, DISPLAY, gm.merge_params(None), None)
        assert len(res["fixations"]) == 1
        assert res["fixations"]["aoi"].iloc[0] == "outside"
        s = res["samples"]
        inside = s["sample_aoi"] == "crosshair"
        assert inside.sum() == 5
        assert (s.loc[inside, "sample_class"] == "aoi_nonfixation").all()

    def test_matching_fixation_is_aoi_fixation(self, aois):
        res = gm.analyze_session(make_df(hold(MINIMAP, 20)), aois, DISPLAY, gm.merge_params(None), None)
        assert (res["samples"]["sample_class"] == "aoi_fixation").all()


def write_csv(path: Path, points, **kw) -> Path:
    make_df(points, **kw).to_csv(path, index=False)
    return path


class TestVer3Cli:
    def test_refuses_to_overwrite_with_different_params(self, ver3, tmp_path):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000000.csv", hold(CROSSHAIR, 60) + hold(EMPTY, 60))
        out = tmp_path / "out"
        base = ["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(out)]
        ver3.main(base)
        ver3.main(base)  # 同じ条件の再実行は上書きしてよい
        fixed = ROOT / "src" / "aoi_detector" / "analysis_params_sensitivity_fixed650.json"
        with pytest.raises(SystemExit, match="aoi_result_fixed650"):
            ver3.main(base + ["--params", str(fixed)])
        qc = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))
        assert qc["params"]["eye_screen_distance_source"] == "measured"  # 主解析が残っている
        ver3.main(base + ["--params", str(fixed), "--overwrite"])
        qc = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))
        assert qc["params"]["eye_screen_distance_source"] == "fixed"
        assert qc["script"]["version"] == ver3.SCRIPT_VERSION

    def test_all_segments_empty_is_error(self, ver3, tmp_path):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000001.csv", hold(CROSSHAIR, 60))
        seg = tmp_path / "seg.csv"
        pd.DataFrame([{"round": 1, "segment_type": "alive", "video_start_sec": 100.0, "video_end_sec": 110.0}]
                     ).to_csv(seg, index=False)
        with pytest.raises(SystemExit, match="同期オフセット"):
            ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--segments", str(seg), "--offset", "0",
                       "--output-dir", str(tmp_path / "out")])

    def test_empty_segment_recorded_in_qc(self, ver3, tmp_path):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000002.csv", hold(CROSSHAIR, 60))
        seg = tmp_path / "seg.csv"
        pd.DataFrame([
            {"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": 0.5},
            {"round": 2, "segment_type": "alive", "video_start_sec": 100.0, "video_end_sec": 110.0},
        ]).to_csv(seg, index=False)
        out = tmp_path / "out"
        ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--segments", str(seg), "--offset", "0",
                   "--output-dir", str(out)])
        dq = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))["data_quality"]
        assert dq["empty_segment_rounds"] == [2]
        assert dq["valid_rate_by_segment"] == {"0": 1.0, "1": None}
        assert dq["n_samples_by_segment"]["1"] == 0


class TestDisplayTilt:
    """指摘12：aoi_check と calib_compare の傾きの定義・表示を1つの関数にまとめた。"""

    def test_same_definition_in_both_tools(self):
        import math
        from aoi_check import display_geometry_check
        from aoi_geometry import display_tilt_in_ucs_deg
        from calib_compare import display_geometry
        a = math.radians(20.0)
        da = {"top_left": [-300.0, 27.0 + 339.4 * math.cos(a), 3.0 + 339.4 * math.sin(a)],
              "top_right": [303.0, 27.0 + 339.4 * math.cos(a), 3.0 + 339.4 * math.sin(a)],
              "bottom_left": [-300.0, 27.0, 3.0], "width_mm": 603.0, "height_mm": 339.4}
        assert display_tilt_in_ucs_deg(da) == pytest.approx(20.0)
        assert display_geometry({"display_area_ucs_mm": da})["display_angle_deg"] == pytest.approx(20.0)
        assert display_geometry_check({"display_area_ucs_mm": da}, DISPLAY)["tracker_tilt_deg"] == pytest.approx(20.0)
        assert display_tilt_in_ucs_deg({}) is None
