# -*- coding: utf-8 -*-
"""
計測スクリプト v5 の CSV 出力のテスト（T1）。Tobii SDK・pygame・screeninfo はスタブにして、
GazeRecorder.gaze_data_callback に偽の視線サンプルを渡し、
- 既存列の順序が変わっていない（新列は末尾に追加）
- 新列に値が入り、欠測は "NaN"
- 旧版解析（archive/aoi_analysis-ver2.py）・gaze_visualizer_v3.load_rows・ver3 解析がそのまま読める
ことを確認する。実機での確認は研究室PCで行う（docs/LAB_CHECKLIST.md）。
"""
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent

V4_FIELDNAMES = [
    "wall_timestamp_local", "wall_timestamp_utc", "pc_time_sec", "device_time_stamp_us",
    "system_time_stamp_us", "left_x", "left_y", "right_x", "right_y", "center_x", "center_y",
    "left_gaze_point_validity", "right_gaze_point_validity", "gaze_missing",
]


@pytest.fixture(scope="module")
def tcap():
    stubs = {}
    for name in ("tobii_research", "pygame", "screeninfo"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            if name == "tobii_research":
                mod.EYETRACKER_GAZE_DATA = "gaze"
            stubs[name] = mod
            sys.modules[name] = mod
    try:
        yield importlib.import_module("tobii_capture_with_sync_flash_v5")
    finally:
        for name in stubs:
            sys.modules.pop(name, None)


def sample(t_us, valid=True):
    def eye(x, y):
        return {
            "gaze_point_on_display_area": (x, y) if valid else (float("nan"), float("nan")),
            "gaze_point_validity": 1 if valid else 0,
            "gaze_origin_in_user_coordinate_system": (30.0, 10.0, 648.5) if valid else None,
            "gaze_origin_validity": 1 if valid else 0,
            "pupil_diameter": 3.4 if valid else None,
            "pupil_validity": 1 if valid else 0,
        }
    d = {"device_time_stamp": t_us, "system_time_stamp": t_us + 1000}
    for side, (x, y) in (("left", (0.49, 0.5)), ("right", (0.51, 0.5))):
        for k, v in eye(x, y).items():
            d[f"{side}_{k}"] = v
    if not valid:
        d["left_gaze_point_on_display_area"] = None
        d["right_gaze_point_on_display_area"] = None
    return d


def test_v5_csv_is_backward_compatible(tcap, tmp_path):
    assert tcap.FIELDNAMES[:len(V4_FIELDNAMES)] == V4_FIELDNAMES
    assert len(tcap.FIELDNAMES) == len(V4_FIELDNAMES) + 8

    csv_path = tmp_path / "gaze_T_1_default_20261020_000000.csv"
    rec = tcap.GazeRecorder(csv_path, eyetracker=None, target_fps=60)
    rec.open_writer()
    import time
    rec.start_perf = time.perf_counter()
    for i in range(30):
        rec.gaze_data_callback(sample(i * 16667, valid=(i % 10 != 9)))
    rec.close_writer()

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    assert list(df.columns) == tcap.FIELDNAMES
    assert df.loc[0, "left_gaze_origin_z_mm"] == pytest.approx(648.5)
    assert df.loc[0, "left_pupil_diameter_mm"] == pytest.approx(3.4)
    assert df.loc[9, "gaze_missing"] == True  # noqa: E712
    raw = csv_path.read_text(encoding="utf-8-sig").splitlines()[10]
    assert raw.count("NaN") >= 4  # 欠測は空欄ではなく "NaN"

    # gaze_visualizer_v3.load_rows がそのまま読める（cv2 が無い環境ではスキップ）
    pytest.importorskip("cv2")
    vis = importlib.import_module("gaze_visualizer_v3")
    rows = vis.load_rows(csv_path)
    assert len(rows) == 30

    # 旧版（ver2）解析の有効判定がそのまま動く
    spec = importlib.util.spec_from_file_location("aoi_v2", ROOT / "archive" / "aoi_analysis-ver2.py")
    v2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v2)
    v2.validate_columns(df)
    assert int(v2.compute_valid_mask(df).sum()) == 27

    # ver3 の眼−トラッカー距離の要約
    import gaze_metrics as gm
    eye = gm.eye_distance_summary(df)
    assert eye["available"] and eye["median_mm"] == pytest.approx(648.5)
