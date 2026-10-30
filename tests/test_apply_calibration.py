# -*- coding: utf-8 -*-
"""apply_calibration（較正データの入れ直し）と calibrate_validate 1.8.0 の較正データの記録のテスト。"""
import hashlib
import json

import pytest

from apply_calibration import apply_and_verify, load_candidate, same_display_area, select_best
from calibrate_validate import calibration_data_record, retrieve_calibration

DA = {"top_left": [-298.35, 354.66, 72.47], "top_right": [298.35, 354.66, 72.47], "bottom_left": [-298.35, 26.39, 2.69]}


def _write_calib(tmp_path, name, *, passed=False, hud_failed=(), mean=1.2, succeeded=True, data=b"cal",
                 created="2026-10-30T03:00:00+00:00", subject="P01", with_file=True):
    data_file = None
    if with_file and succeeded:
        data_file = name.replace(".json", ".calibdata")
        (tmp_path / data_file).write_bytes(data)
    calib = {
        "subject_id": subject, "created_at_utc": created,
        "eyetracker": {"serial_number": "TPE01-X"}, "display_area_ucs_mm": DA,
        "calibration": {"succeeded": succeeded},
        "summary": {"passed": passed, "hud_points_failed": list(hud_failed), "mean_accuracy_deg": mean},
        "active_calibration": {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "file": data_file},
    }
    path = tmp_path / name
    path.write_text(json.dumps(calib), encoding="utf-8")
    return path


def test_rule_fewest_hud_failures_then_mean(tmp_path):
    a = _write_calib(tmp_path, "calib_P01_1.json", hud_failed=["aoi_minimap", "aoi_credits"], mean=0.80)
    b = _write_calib(tmp_path, "calib_P01_2.json", hud_failed=["aoi_minimap"], mean=1.10)
    c = _write_calib(tmp_path, "calib_P01_3.json", hud_failed=["aoi_abilities"], mean=0.95)
    best = select_best([load_candidate(p) for p in (a, b, c)])
    assert best["calib_json"] == "calib_P01_3.json"  # 不合格1点どうしなら全点平均が小さい方


def test_passed_run_is_preferred(tmp_path):
    a = _write_calib(tmp_path, "calib_P01_1.json", hud_failed=[], mean=1.05)  # HUD は全部通ったが平均で不合格
    b = _write_calib(tmp_path, "calib_P01_2.json", passed=True, hud_failed=[], mean=0.90)
    assert select_best([load_candidate(p) for p in (a, b)])["calib_json"] == "calib_P01_2.json"


def test_tie_prefers_newer_run(tmp_path):
    a = _write_calib(tmp_path, "calib_P01_1.json", hud_failed=["x"], mean=1.1, created="2026-10-30T03:00:00+00:00")
    b = _write_calib(tmp_path, "calib_P01_2.json", hud_failed=["y"], mean=1.1, created="2026-10-30T03:10:00+00:00")
    assert select_best([load_candidate(p) for p in (a, b)])["calib_json"] == "calib_P01_2.json"


def test_ineligible_runs_are_excluded(tmp_path):
    failed_sdk = _write_calib(tmp_path, "calib_P01_1.json", succeeded=False, mean=0.5)
    no_file = _write_calib(tmp_path, "calib_P01_2.json", mean=0.6, with_file=False)
    ok = _write_calib(tmp_path, "calib_P01_3.json", hud_failed=["a", "b", "c"], mean=1.9)
    cands = [load_candidate(p) for p in (failed_sdk, no_file, ok)]
    assert cands[0]["reason_excluded"] and cands[1]["reason_excluded"] and cands[2]["reason_excluded"] is None
    assert select_best(cands)["calib_json"] == "calib_P01_3.json"
    assert select_best(cands[:2]) is None


class _FakeEyetracker:
    def __init__(self, data=None, broken=False, corrupt=False):
        self.data, self.broken, self.corrupt = data, broken, corrupt

    def retrieve_calibration_data(self):
        if self.broken:
            raise RuntimeError("接続エラー")
        return (self.data + b"!") if (self.corrupt and self.data) else self.data

    def apply_calibration_data(self, data):
        self.data = data


def test_apply_and_verify():
    assert apply_and_verify(_FakeEyetracker(), b"calibration-bytes")["verified"]
    assert not apply_and_verify(_FakeEyetracker(corrupt=True), b"calibration-bytes")["verified"]


def test_retrieve_calibration_and_record():
    data, err = retrieve_calibration(_FakeEyetracker(b"abc"))
    assert data == b"abc" and err is None
    rec = calibration_data_record(data, err, "calib_P01_x.calibdata")
    assert rec == {"sha256": hashlib.sha256(b"abc").hexdigest(), "size_bytes": 3, "file": "calib_P01_x.calibdata",
                   "error": None}
    assert retrieve_calibration(_FakeEyetracker(None)) == (None, "装置に較正データがありません")
    data, err = retrieve_calibration(_FakeEyetracker(broken=True))
    assert data is None and "接続エラー" in err
    assert calibration_data_record(None, err)["sha256"] is None


def test_display_area_comparison():
    moved = {**DA, "top_left": [-298.35, 354.66, 80.0]}
    assert same_display_area(DA, DA) and not same_display_area(DA, moved) and same_display_area(None, DA)
