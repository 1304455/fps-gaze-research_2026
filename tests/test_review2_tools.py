# -*- coding: utf-8 -*-
"""
2026-10-13 のコードレビュー（2回目）への対応（ツール・重複の整理）のテスト。
- segment_annotator：手で編集した区間 CSV を退避してから上書きし、events が無いときは上書きしない
- calib_compare：--validate-only の回を calib_margin のグループに混ぜない
- 共通化：aoi_center / eye_mean（aoi_geometry）、区間 CSV の列定義（gaze_metrics）
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import calib_compare as cc
import gaze_metrics as gm
import segment_annotator as sa
from aoi_check import _mean_bias
from aoi_geometry import aoi_center, eye_mean

from test_calib_compare import make_calib

EVENTS = [
    {"kind": "buy_end", "video_sec": 30.0, "side": "attack"}, {"kind": "death", "video_sec": 55.5, "side": "attack"},
    {"kind": "buy_end", "video_sec": 130.0, "side": "attack"}, {"kind": "round_end", "video_sec": 200.0, "side": "attack"},
]


class TestAnnotatorKeepsManualEdits:
    def paths(self, tmp_path):
        return tmp_path / "s_segments.csv", tmp_path / "s_segments_events.json"

    def test_unchanged_csv_needs_no_backup(self, tmp_path):
        csv_path, ev_path = self.paths(tmp_path)
        assert not sa.check_existing_segments_csv(csv_path, EVENTS)  # CSV なし
        sa.save_segments(csv_path, ev_path, EVENTS, Path("v.mp4"), 60.0, backup_pending=False)
        assert not sa.check_existing_segments_csv(csv_path, EVENTS)  # 自分で書いたものと同じ

    def test_edited_csv_is_backed_up_before_overwrite(self, tmp_path, capsys):
        csv_path, ev_path = self.paths(tmp_path)
        sa.save_segments(csv_path, ev_path, EVENTS, Path("v.mp4"), 60.0, backup_pending=False)
        df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str)
        df.loc[1, "notes"] = "R2 は途中で回線落ち"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        pending = sa.check_existing_segments_csv(csv_path, EVENTS)
        assert pending and "backup" in capsys.readouterr().out
        msg, backup = sa.save_segments(csv_path, ev_path, EVENTS, Path("v.mp4"), 60.0, backup_pending=pending)
        assert backup is not None and backup.name.startswith("s_segments.backup_")
        assert pd.read_csv(backup, encoding="utf-8-sig").loc[1, "notes"] == "R2 は途中で回線落ち"  # 手の編集が残る
        assert len(pd.read_csv(csv_path, encoding="utf-8-sig")) == 2

    def test_csv_without_events_is_not_overwritten(self, tmp_path):
        csv_path, _ = self.paths(tmp_path)
        sa.write_segments_csv(csv_path, sa.events_to_segments(EVENTS)[0])  # 手作業（別の場所）で作った CSV
        with pytest.raises(SystemExit, match="events"):
            sa.check_existing_segments_csv(csv_path, [])
        sa.write_segments_csv(csv_path, [])  # 見出しだけなら消えるものが無いので起動してよい
        assert not sa.check_existing_segments_csv(csv_path, [])

    def test_columns_are_shared_with_reader(self):
        assert sa.SEGMENT_CSV_COLUMNS is gm.SEGMENT_COLUMNS


class TestCalibCompareValidateOnly:
    def test_validate_only_is_not_grouped_with_margin(self):
        sdk = make_calib()
        sdk["calibration"] = {"status": "calibration_status_success", "succeeded": True}
        etm = make_calib()
        etm["calibration"] = {"status": "not_performed (--validate-only)"}  # parameters.calib_margin は 0.1 のまま
        df = pd.DataFrame([cc.summarize_calib(sdk, "a.json"), cc.summarize_calib(etm, "b.json")])
        assert df.loc[0, "calib_margin"] == 0.1 and pd.isna(df.loc[1, "calib_margin"])
        assert df.loc[1, "calibration_status"].startswith("not_performed")
        g = cc.group_summary(df, "calib_margin")
        assert len(g) == 2 and set(g["n_runs"]) == {1}

    def test_old_calib_without_calibration_key_keeps_margin(self):
        assert cc.summarize_calib(make_calib(), "old.json")["calib_margin"] == 0.1


class TestSharedHelpers:
    def test_aoi_center(self):
        assert aoi_center({"type": "circle", "center_x": 0.2, "center_y": 0.3}) == (0.2, 0.3)
        assert aoi_center({"type": "rectangle", "x_min": 0.1, "x_max": 0.3, "y_min": 0.6, "y_max": 1.0}) == \
            pytest.approx((0.2, 0.8))

    def test_eye_mean_and_aoi_check_agree(self):
        p = {"left": {"bias_x_norm": 0.01, "bias_y_norm": 0.03}, "right": {"bias_x_norm": 0.03, "bias_y_norm": None}}
        assert eye_mean(p, "bias_x_norm") == pytest.approx(0.02)
        assert eye_mean(p, "bias_y_norm") == pytest.approx(0.03)  # 片眼だけならその眼
        assert _mean_bias(p) == pytest.approx((0.02, 0.03))
        none = {"left": {"bias_x_norm": None}, "right": None}
        assert np.isnan(eye_mean(none, "bias_x_norm"))
        assert _mean_bias(none) == (None, None)
