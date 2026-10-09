# -*- coding: utf-8 -*-
"""aggregate_sessions（T6）のテスト：ver3 の出力を合成して作り、横断集計とQCフラグを確認する。"""
import json

import pandas as pd
import pytest

import aggregate_sessions as agg
import gaze_metrics as gm
from aoi_geometry import load_aoi_config
from synthetic import CROSSHAIR, DISPLAY, EMPTY, hold, make_df

from test_gaze_metrics import AOI_JSON


def write_session(root, name, points):
    aois, _ = load_aoi_config(AOI_JSON)
    res = gm.analyze_session(make_df(points), aois, DISPLAY, gm.merge_params(None), None)
    d = root / name
    d.mkdir(parents=True)
    res["metrics_session"].to_csv(d / "aoi_metrics_session.csv", index=False, encoding="utf-8-sig")
    qc = {
        "session_base": name,
        "params": gm.merge_params(None),
        "inputs": {"aoi_config": {"sha256": "x"}},
        "sync": {"method": "metadata_confirmed_based", "within_expected_band": True},
        "data_quality": {"valid_rate_in_segments": 0.6 if name.endswith("b") else 0.95,
                         "analysis_time_sec": 100.0, "n_segments": 1},
        "resolution_check": {"ok": True},
    }
    (d / "qc.json").write_text(json.dumps(qc), encoding="utf-8")


@pytest.fixture
def setup(tmp_path):
    res = tmp_path / "res"
    # セッションa：crosshair 30/40、セッションb：crosshair 10/120（同じ参加者）
    write_session(res, "s_a", hold(CROSSHAIR, 30) + hold(EMPTY, 10))
    write_session(res, "s_b", hold(CROSSHAIR, 10) + hold(EMPTY, 110))
    manifest = tmp_path / "m.csv"
    pd.DataFrame([{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                  {"session_base": "s_b", "participant_id": "P1", "group": "low"}]).to_csv(manifest, index=False)
    return tmp_path, res, manifest


def test_participant_pct_is_pooled_not_mean_of_ratios(setup):
    tmp, res, manifest = setup
    out = tmp / "out"
    agg.main(["--results-dir", str(res), "--manifest", str(manifest), "--out-dir", str(out)])
    p = pd.read_csv(out / "participant_aoi_metrics.csv")
    row = p[(p["session_set"] == "all") & (p["aoi"] == "crosshair")].iloc[0]
    assert row["gaze_pct"] == pytest.approx(100 * 40 / 160)  # 平均すると (75 + 8.3)/2 = 41.7% になってしまう
    assert row["n_sessions"] == 2
    long = pd.read_csv(out / "master_long.csv")
    assert set(long["aoi"]) >= {"credits", "outside", "offscreen"}  # 0件AOIも残る
    qc = pd.read_csv(out / "qc_report.csv")
    assert not qc["exclude_candidate"].any()  # 基準未指定ならフラグは立たない
    assert qc["n_criteria_evaluated"].max() == 0


def test_flags_and_exclusion_set(setup):
    tmp, res, manifest = setup
    crit = tmp / "c.json"
    crit.write_text(json.dumps({"min_valid_rate_in_segments": 0.7}), encoding="utf-8")
    out = tmp / "out"
    agg.main(["--results-dir", str(res), "--manifest", str(manifest), "--qc-criteria", str(crit), "--out-dir", str(out)])
    qc = pd.read_csv(out / "qc_report.csv").set_index("session_base")
    assert qc.loc["s_b", "exclude_candidate"] and not qc.loc["s_a", "exclude_candidate"]
    p = pd.read_csv(out / "participant_aoi_metrics.csv")
    ex = p[(p["session_set"] == "excluding_flagged") & (p["aoi"] == "crosshair")].iloc[0]
    assert ex["gaze_pct"] == pytest.approx(75.0)
    assert len(pd.read_csv(out / "master_long.csv")["session_base"].unique()) == 2  # 削除はしない


def test_mixed_params_rejected(setup):
    tmp, res, manifest = setup
    q = json.loads((res / "s_b" / "qc.json").read_text(encoding="utf-8"))
    q["params"]["max_gap_ms"] = 100
    (res / "s_b" / "qc.json").write_text(json.dumps(q), encoding="utf-8")
    with pytest.raises(SystemExit):
        agg.main(["--results-dir", str(res), "--manifest", str(manifest), "--out-dir", str(tmp / "o")])
