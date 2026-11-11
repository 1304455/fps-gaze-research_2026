# -*- coding: utf-8 -*-
"""idt_threshold_check（I-DT の閾値を予備実験で確かめる。LAB_CHECKLIST 4 の手順 A〜C）のテスト。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import idt_threshold_check as itc
from synthetic import CROSSHAIR, DT, EMPTY, MINIMAP, make_df

ROOT = Path(__file__).resolve().parent.parent
AOI_JSON = ROOT / "src" / "aoi_detector" / "valorant_hud_aoi_circular.json"


# ---------------------------------------------------------------------------
# B のノイズの見積もり
# ---------------------------------------------------------------------------

def test_window_samples_at_60hz():
    assert itc.window_samples(100.0, DT) == 6
    assert itc.window_samples(150.0, DT) == 9


def test_noise_estimate_formula_matches_simulation():
    """E[D] = d2(n)·RMS-S2S が、独立な正規ノイズのシミュレーションと合う（仮定の下での式の確認）。"""
    rng = np.random.default_rng(0)
    sigma, n, reps = 0.05, 6, 20000
    xy = rng.normal(0.0, sigma, size=(reps, n, 2))
    s2s = np.sqrt((np.diff(xy, axis=1) ** 2).sum(axis=2))
    rms = float(np.sqrt(np.mean(s2s ** 2)))
    d = (xy[:, :, 0].max(1) - xy[:, :, 0].min(1)) + (xy[:, :, 1].max(1) - xy[:, :, 1].min(1))
    assert rms == pytest.approx(2 * sigma, rel=0.02)
    assert itc.noise_dispersion_estimate(rms, n) == pytest.approx(float(d.mean()), rel=0.02)


def test_noise_estimate_rejects_unknown_n():
    with pytest.raises(ValueError):
        itc.noise_dispersion_estimate(0.1, 50)


def test_calib_precision_uses_binocular_validation_points(tmp_path):
    p = tmp_path / "calib.json"
    p.write_text(json.dumps({"validation_points": [
        {"binocular_avg": {"precision_rms_s2s_deg": 0.10}},
        {"binocular_avg": {"precision_rms_s2s_deg": 0.30}},
        {"binocular_avg": {"precision_rms_s2s_deg": 0.20}},
        {"binocular_avg": {}},
    ], "validation_warmup": {"binocular_avg": {"precision_rms_s2s_deg": 5.0}}}), encoding="utf-8")
    info = itc.calib_binocular_precision(p)
    assert info["available"] and info["n_points"] == 3
    assert info["precision_rms_s2s_deg_median"] == pytest.approx(0.20)
    assert not itc.calib_binocular_precision(None)["available"]
    assert not itc.calib_binocular_precision(tmp_path / "none.json")["available"]


# ---------------------------------------------------------------------------
# A：最短付近の割合
# ---------------------------------------------------------------------------

def test_near_min_counts_6_and_7_samples_but_not_8():
    fix = pd.DataFrame({"duration_sec": [6 * DT, 7 * DT, 8 * DT, 30 * DT]})
    s = itc.duration_summary(fix, DT)
    assert s["n_fixations"] == 4
    assert s["near_min_share"] == pytest.approx(0.5)
    assert s["near_min_limit_ms"] == pytest.approx(125.0)


def test_histogram_keeps_empty_bins_and_caps_long_fixations():
    fix = pd.DataFrame({"participant_id": ["PL01"] * 3, "duration_sec": [0.105, 0.110, 3.0]})
    h = itc.duration_histogram(fix, bin_ms=20.0, max_ms=1000.0)
    assert len(h) == 51  # 0〜1000 の 50 階級 ＋「1000以上」
    assert int(h.loc[h["bin_start_ms"] == 100.0, "count"].iloc[0]) == 2
    assert int(h.loc[h["bin_ms"] == "1000以上", "count"].iloc[0]) == 1
    assert int(h["count"].sum()) == 3


# ---------------------------------------------------------------------------
# C の感度と判定
# ---------------------------------------------------------------------------

def _grid_p(rows):
    return pd.DataFrame(rows, columns=["participant_id", "dispersion_deg", "min_duration_ms",
                                       "fixations_per_min", "mean_fixation_duration_ms"])


def test_sensitivity_flags_change_over_25_percent():
    g = _grid_p([
        ("PL01", 1.0, 100.0, 100.0, 300.0),
        ("PL01", 0.75, 100.0, 80.0, 280.0),   # −20%：目安以内
        ("PL01", 1.5, 100.0, 70.0, 400.0),    # −30% / +33%：目安を超える
        ("PL01", 1.0, 150.0, 10.0, 10.0),     # 最短 150 ms の行は判定に使わない
    ])
    s = itc.sensitivity_table(g).set_index("dispersion_deg")
    assert not s.loc[0.75, "fixations_per_min_exceeds"]
    assert s.loc[1.5, "fixations_per_min_exceeds"] and s.loc[1.5, "mean_fixation_duration_ms_exceeds"]
    assert s.loc[0.75, "fixations_per_min_rel_change"] == pytest.approx(-0.2)


def _a(share):
    return pd.DataFrame([{"participant_id": "PL01", "near_min_share": share}])


def _b(avail, share):
    return pd.DataFrame([{"participant_id": "PL01", "available": avail, "noise_share_of_threshold": share}])


def _sens(exceeds):
    return pd.DataFrame([{"participant_id": "PL01", "fixations_per_min_exceeds": exceeds,
                          "mean_fixation_duration_ms_exceeds": False}])


def test_judge_no_hit():
    j = itc.judge(_a(0.10), _b(True, 0.2), _sens(False))
    assert not j["any_hit"] and j["all_judged"]
    assert "変えない" in j["verdict"]


@pytest.mark.parametrize("a,b,c,key", [(0.31, 0.2, False, "A"), (0.1, 0.51, False, "B"), (0.1, 0.2, True, "C")])
def test_judge_each_criterion(a, b, c, key):
    j = itc.judge(_a(a), _b(True, b), _sens(c))
    assert j["any_hit"] and j["flags"][key]["hit"]
    assert "決め直す" in j["verdict"]


def test_judge_boundary_is_not_hit():
    """目安は「超える」。ちょうど 30% / 50% は該当しない。"""
    j = itc.judge(_a(0.30), _b(True, 0.5), _sens(False))
    assert not j["any_hit"]


def test_judge_missing_calibration_is_not_reported_as_no_hit():
    j = itc.judge(_a(0.1), _b(False, np.nan), _sens(False))
    assert not j["any_hit"] and not j["all_judged"]
    assert "判定できなかった" in j["verdict"]
    assert j["flags"]["B"]["not_judged_participants"] == ["PL01"]


# ---------------------------------------------------------------------------
# 結果フォルダから読み直して通す（合成データ）
# ---------------------------------------------------------------------------

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def jittered_holds(rng, plan, jitter=0.0008):
    pts = []
    for (x, y), n in plan:
        pts += [(x + rng.normal(0, jitter), y + rng.normal(0, jitter)) for _ in range(n)]
    return pts


def make_result_dir(tmp_path: Path, name: str, participant_id: str, seed: int = 0, calib_precision=0.08,
                    with_calib=True) -> Path:
    rng = np.random.default_rng(seed)
    plan = [(CROSSHAIR, 40), (EMPTY, 20), (MINIMAP, 30), (CROSSHAIR, 15), ((0.13, 0.95), 25), (CROSSHAIR, 50)] * 4
    pts = jittered_holds(rng, plan)
    csv = tmp_path / f"{name}.csv"
    make_df(pts).to_csv(csv, index=False)
    total = len(pts) * DT
    seg = tmp_path / f"{name}_segments.csv"
    pd.DataFrame([
        {"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": total / 2,
         "end_reason": "death", "side": "attack", "notes": "", "start_reason": "buy_end"},
        {"round": 2, "segment_type": "alive", "video_start_sec": total / 2, "video_end_sec": total,
         "end_reason": "round_end", "side": "attack", "notes": "", "start_reason": "buy_end"},
    ]).to_csv(seg, index=False)
    manifest_row = {"participant_id": participant_id}
    if with_calib:
        calib = tmp_path / f"calib_{name}.json"
        calib.write_text(json.dumps({"validation_points": [
            {"binocular_avg": {"precision_rms_s2s_deg": calib_precision}}] * 5}), encoding="utf-8")
        manifest_row["calibration_file"] = str(calib)
    out = tmp_path / "aoi_result" / name
    out.mkdir(parents=True)
    qc = {
        "session_base": name,
        "inputs": {"gaze_csv": {"path": str(csv), "sha256": _sha(csv)},
                   "aoi_config": {"path": str(AOI_JSON), "sha256": _sha(AOI_JSON)},
                   "segments_file": {"path": str(seg), "sha256": _sha(seg)}},
        "sync": {"offset_sec": 0.0},
        "params": {"eye_screen_distance_source": "fixed"},  # 合成データは眼の距離が一定
        "manifest_row": manifest_row,
    }
    (out / "qc.json").write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    return out


def test_load_session_rejects_main_experiment_data(tmp_path):
    d = make_result_dir(tmp_path, "gaze_S01_1", "S01")
    with pytest.raises(ValueError, match="予備実験"):
        itc.load_session(d)
    assert itc.load_session(d, allow_non_pilot=True)["participant_id"] == "S01"


def test_load_session_rejects_changed_input(tmp_path):
    d = make_result_dir(tmp_path, "gaze_PL01_1", "PL01")
    csv = tmp_path / "gaze_PL01_1.csv"
    csv.write_text(csv.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256"):
        itc.load_session(d)


def test_load_session_requires_segments(tmp_path):
    d = make_result_dir(tmp_path, "gaze_PL01_1", "PL01")
    qc = json.loads((d / "qc.json").read_text(encoding="utf-8"))
    qc["inputs"]["segments_file"] = None
    (d / "qc.json").write_text(json.dumps(qc), encoding="utf-8")
    with pytest.raises(ValueError, match="区間"):
        itc.load_session(d)


def test_run_check_end_to_end(tmp_path):
    dirs = [make_result_dir(tmp_path, "gaze_PL01_1", "PL01", seed=1),
            make_result_dir(tmp_path, "gaze_PL01_2", "PL01", seed=2),   # 2試合した人：足してから割る
            make_result_dir(tmp_path, "gaze_PL02_1", "PL02", seed=3, with_calib=False)]
    sessions = [itc.load_session(d) for d in dirs]
    r = itc.run_check(sessions)

    gs, gp = r["grid_sessions"], r["grid_participants"]
    assert len(gs) == 3 * 15
    assert len(gp) == 2 * 15
    # 参加者の数は、セッションの数を足したもの（割合の平均ではない）
    b1 = gs[(gs["participant_id"] == "PL01") & (gs["dispersion_deg"] == 1.0) & (gs["min_duration_ms"] == 100.0)]
    p1 = gp[(gp["participant_id"] == "PL01") & (gp["dispersion_deg"] == 1.0) & (gp["min_duration_ms"] == 100.0)].iloc[0]
    assert p1["n_fixations"] == b1["n_fixations"].sum()
    assert p1["fixations_per_min"] == pytest.approx(b1["n_fixations"].sum() / (b1["valid_time_sec"].sum() / 60))
    # 主要6つの AOI への注視の割合が出る（下部HUD（統合）を含む）
    for name in itc.PRIMARY_AOIS:
        assert f"aoi_fixation_pct__{name}" in gp.columns
    assert p1["aoi_fixation_pct__crosshair"] > 0
    assert p1["aoi_fixation_pct__minimap"] > 0

    # 分散の閾値を大きくすると注視は長く（少なく）なる方向
    g100 = gp[(gp["participant_id"] == "PL01") & (gp["min_duration_ms"] == 100.0)].set_index("dispersion_deg")
    assert g100.loc[2.0, "mean_fixation_duration_ms"] >= g100.loc[0.5, "mean_fixation_duration_ms"]

    a = r["a"].set_index("participant_id")
    assert a.loc["PL01", "n_fixations"] == int(b1["n_fixations"].sum())
    assert a.loc["PL01", "near_min_share"] < itc.NEAR_MIN_SHARE_MAX  # 合成データの注視は長い

    b = r["b"].set_index("participant_id")
    assert b.loc["PL01", "available"] and not b.loc["PL02", "available"]
    assert b.loc["PL01", "noise_dispersion_estimate_deg"] == pytest.approx(2.534 * 0.08)

    s = r["sensitivity"]
    assert set(s["participant_id"]) == {"PL01", "PL02"} and set(s["dispersion_deg"]) == {0.75, 1.5}

    j = r["judgement"]
    # PL02 は較正ファイルが無いので、該当なしでも「判定できなかった」になる
    assert not j["all_judged"]
    assert j["flags"]["B"]["not_judged_participants"] == ["PL02"]


def test_main_writes_outputs(tmp_path, capsys):
    d = make_result_dir(tmp_path, "gaze_PL01_1", "PL01", seed=1)
    out = tmp_path / "idt_check"
    itc.main(["--results", str(d), "--out-dir", str(out)])
    for name in ("summary.json", "a_duration_by_participant.csv", "a_duration_histogram.csv",
                 "b_noise_by_participant.csv", "c_grid_by_session.csv", "c_grid_by_participant.csv",
                 "c_sensitivity.csv"):
        assert (out / name).exists(), name
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["criteria"] == {"recorded_at": itc.CRITERIA_RECORDED_AT, "near_min_share_max": 0.30,
                                   "noise_share_of_threshold_max": 0.5, "change_max": 0.25}
    assert summary["sessions"][0]["inputs"]["gaze_csv"]["sha256"]
    text = capsys.readouterr().out
    assert "判定：" in text


def test_criteria_cannot_be_changed_from_command_line():
    """目安の数値は結果を見る前に決めたもの。引数で変えられないことを確かめる。"""
    opts = {a.dest for a in itc.build_arg_parser()._actions}
    assert opts == {"help", "results", "out_dir", "allow_non_pilot"}
