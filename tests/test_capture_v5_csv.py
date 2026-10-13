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

V50_ADDED = [
    "left_gaze_origin_z_mm", "right_gaze_origin_z_mm", "left_gaze_origin_validity", "right_gaze_origin_validity",
    "left_pupil_diameter_mm", "right_pupil_diameter_mm", "left_pupil_validity", "right_pupil_validity",
]
V51_ADDED = ["left_gaze_origin_x_mm", "left_gaze_origin_y_mm", "right_gaze_origin_x_mm", "right_gaze_origin_y_mm"]

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
    # v5.0 の8列の順序も変えず、v5.1.0 の4列（眼位置 x/y）は末尾に追加
    assert tcap.FIELDNAMES[len(V4_FIELDNAMES):len(V4_FIELDNAMES) + 8] == V50_ADDED
    assert tcap.FIELDNAMES[len(V4_FIELDNAMES) + 8:] == V51_ADDED

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
    assert df.loc[0, "left_gaze_origin_x_mm"] == pytest.approx(30.0)
    assert df.loc[0, "right_gaze_origin_y_mm"] == pytest.approx(10.0)
    assert pd.isna(df.loc[9, "left_gaze_origin_x_mm"])
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


def _redact(tcap, msg, *args, redact_token=False):
    import logging
    rec = logging.LogRecord("obsws_python.baseclient.ObsClient", logging.INFO, __file__, 1, msg, args, None)
    out = tcap.RedactingFormatter(redact_token=redact_token).format(rec)
    assert rec.getMessage() == (msg % args if args else msg)  # レコード自体は書き換えない
    return out


def test_log_redacts_obs_password(tcap):
    # obsws-python の実際の出力形式（%s 展開前・展開後の両方）
    msg = _redact(tcap, "Connecting with parameters: host='192.168.0.2' port=4455 password='s3cr3t-Pw' subs=0 timeout=3.0")
    assert "s3cr3t-Pw" not in msg
    assert "password=***" in msg and "port=4455" in msg and "subs=0" in msg
    msg = _redact(tcap, "Connecting with parameters: %s", "host='h' password=\"abc123\" timeout=3")
    assert "abc123" not in msg
    assert "password=***" in msg


def test_log_redacts_token_only_when_requested(tcap):
    line = "[ControlHTTP] 192.168.0.5 - \"GET /start?token=vYH_TNXQ3cs HTTP/1.1\" 200 -"
    assert "vYH_TNXQ3cs" in _redact(tcap, line, redact_token=False)
    red = _redact(tcap, line, redact_token=True)
    assert "vYH_TNXQ3cs" not in red and "token=*** HTTP/1.1" in red


def test_log_file_does_not_contain_secrets(tcap, tmp_path, capsys):
    import logging
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers = []
    try:
        log_path = tmp_path / "x_log.txt"
        tcap.setup_logging(log_path)
        logging.getLogger("obsws_python.baseclient.ObsClient").warning("password='pw-in-log'")
        logging.info("制御用HTTPサーバを起動しました: http://10.0.0.1:8765/?token=tok-in-log")
        for h in root.handlers:
            h.flush()
        text = log_path.read_text(encoding="utf-8")
        assert "pw-in-log" not in text and "tok-in-log" not in text
        assert "password=***" in text and "token=***" in text
        # コンソール：パスワードは伏せるが、実験者が開く URL の token はそのまま出す
        # （2026-10-13 レビュー指摘13：ファイル用の伏せ字がコンソールにも漏れていた）
        console = capsys.readouterr().out
        assert "pw-in-log" not in console
        assert "token=tok-in-log" in console
    finally:
        for h in root.handlers:
            h.close()
        root.handlers, root.level = saved[0], saved[1]


def test_obs_controller_quiets_obsws_logger():
    import logging
    sys.path.insert(0, str(ROOT))
    importlib.import_module("src.obs_controller_v2")
    assert logging.getLogger("obsws_python").getEffectiveLevel() >= logging.WARNING
