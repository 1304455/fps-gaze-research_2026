# -*- coding: utf-8 -*-
"""
2026-10-13 のコードレビュー（2回目）への対応（集計・解析）のテスト。
- 集計：group の表記ゆれ、区間なしの結果の混入、台帳の空行、分母0のセッション、感度分析の上書き、
        台帳だけにあるセッション、改行コードの違う AOI、cp932 の台帳
- 解析：上書き防止の比較項目と順序、manifest モードの params、区間の coverage、system_time の逆行、
        パラメータの検査、compare_with_proc の照合
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import aggregate_sessions as agg
import gaze_metrics as gm
from aoi_geometry import aoi_config_content_sha256, load_aoi_config
from synthetic import CROSSHAIR, DISPLAY, DT, EMPTY, hold, make_df

from test_aggregate_sessions import write_session

ROOT = Path(__file__).resolve().parent.parent
AOI_JSON = ROOT / "src" / "aoi_detector" / "valorant_hud_aoi_circular.json"


@pytest.fixture(scope="module")
def ver3():
    spec = importlib.util.spec_from_file_location("aoi_analysis_ver3", ROOT / "src" / "aoi_detector" / "aoi_analysis-ver3.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def two_sessions(tmp_path):
    res = tmp_path / "res"
    write_session(res, "s_a", hold(CROSSHAIR, 30) + hold(EMPTY, 10))
    write_session(res, "s_b", hold(CROSSHAIR, 10) + hold(EMPTY, 110))
    return tmp_path, res


def write_manifest(path: Path, rows: list[dict], encoding="utf-8-sig") -> Path:
    pd.DataFrame(rows).to_csv(path, index=False, encoding=encoding)
    return path


def run_agg(res, manifest, out, *extra):
    agg.main(["--results-dir", str(res), "--manifest", str(manifest), "--out-dir", str(out), *extra])


def edit_qc(res, name, fn):
    path = res / name / "qc.json"
    q = json.loads(path.read_text(encoding="utf-8"))
    fn(q)
    path.write_text(json.dumps(q), encoding="utf-8")


class TestAggregate:
    def test_group_variants_of_same_participant(self, two_sessions):
        tmp, res = two_sessions
        # 末尾の空白は同じ群として扱う（strip）。Gaze % は分かれずに 40/160
        m = write_manifest(tmp / "m.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                                           {"session_base": "s_b", "participant_id": "P1", "group": "low "}])
        run_agg(res, m, tmp / "o1")
        p = pd.read_csv(tmp / "o1" / "participant_aoi_metrics.csv")
        row = p[(p["session_set"] == "all") & (p["aoi"] == "crosshair")]
        assert len(row) == 1 and row["gaze_pct"].iloc[0] == pytest.approx(100 * 40 / 160)
        # 大文字・小文字の違いは別の値なのでエラー（どちらが正しいかは実験者が決める）
        m = write_manifest(tmp / "m2.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                                            {"session_base": "s_b", "participant_id": "P1", "group": "Low"}])
        with pytest.raises(SystemExit, match="P1"):
            run_agg(res, m, tmp / "o2")

    def test_whole_session_result_is_rejected_by_default(self, two_sessions):
        tmp, res = two_sessions
        edit_qc(res, "s_b", lambda q: q["inputs"].update({"segments_file": None}))
        m = write_manifest(tmp / "m.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                                           {"session_base": "s_b", "participant_id": "P1", "group": "low"}])
        with pytest.raises(SystemExit, match="区間ファイル"):
            run_agg(res, m, tmp / "o1")
        run_agg(res, m, tmp / "o2", "--allow-whole-session")
        qc = pd.read_csv(tmp / "o2" / "qc_report.csv").set_index("session_base")
        assert qc.loc["s_a", "has_segments_file"] and not qc.loc["s_b", "has_segments_file"]

    def test_blank_rows_in_manifest(self, two_sessions):
        tmp, res = two_sessions
        m = tmp / "m.csv"
        m.write_text("session_base,participant_id,group\ns_a,P1,low\ns_b,P1,low\n,,\n,,\n", encoding="utf-8")
        run_agg(res, m, tmp / "o")  # 以前は MergeError で止まっていた
        assert (tmp / "o" / "qc_report.csv").exists()
        m.write_text("session_base,participant_id,group\ns_a,P1,low\n,P1,low\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="3 行目|\\[3\\]"):
            run_agg(res, m, tmp / "o2")

    def test_zero_denominator_session_does_not_erase_participant(self, two_sessions):
        tmp, res = two_sessions
        # 有効サンプル0の試合（分母0、gaze_pct=NaN）を同じ参加者に足しても、他の試合の値は残る
        aois, _ = load_aoi_config(AOI_JSON)
        r = gm.analyze_session(make_df([None] * 30, eye_z_mm=None), aois, DISPLAY,
                               gm.merge_params({"eye_screen_distance_source": "fixed"}), None)
        assert r["metrics_session"]["gaze_pct"].isna().all()
        d = res / "s_c"
        d.mkdir()
        r["metrics_session"].to_csv(d / "aoi_metrics_session.csv", index=False, encoding="utf-8-sig")
        (d / "qc.json").write_text((res / "s_a" / "qc.json").read_text(encoding="utf-8").replace('"s_a"', '"s_c"'),
                                   encoding="utf-8")
        m = write_manifest(tmp / "m.csv", [{"session_base": s, "participant_id": "P1", "group": "low"}
                                           for s in ("s_a", "s_b", "s_c")])
        run_agg(res, m, tmp / "o")
        p = pd.read_csv(tmp / "o" / "participant_aoi_metrics.csv")
        row = p[(p["session_set"] == "all") & (p["aoi"] == "crosshair")].iloc[0]
        assert row["gaze_pct"] == pytest.approx(100 * 40 / 160)
        assert row["n_sessions"] == 3

    def test_zero_valid_time_gives_nan_not_inf(self):
        metrics = pd.DataFrame([{"session_base": "s", "aoi": "crosshair", "n_samples_in_aoi": 0, "fixation_count": 0,
                                 "total_fixation_duration_sec": 0.0, "visit_count": 0, "revisit_count": 0,
                                 "ttff_censored_segments": 1, "gaze_pct": np.nan, "valid_time_sec": 0.0,
                                 "gaze_pct_denominator_samples": 0, "analysis_time_sec": 10.0}])
        manifest = pd.DataFrame([{"session_base": "s", "participant_id": "P1", "group": "low"}])
        out = agg.participant_metrics(metrics, manifest)
        assert np.isnan(out["fixations_per_min"].iloc[0])

    def test_sensitivity_aggregate_does_not_overwrite_main(self, two_sessions):
        tmp, res = two_sessions
        m = write_manifest(tmp / "m.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                                           {"session_base": "s_b", "participant_id": "P1", "group": "low"}])
        out = tmp / "aggregate"
        run_agg(res, m, out)
        prov = json.loads((out / "aggregate_provenance.json").read_text(encoding="utf-8"))
        assert prov["results_dir"] == str(res.resolve())
        assert prov["eye_screen_distance_source"] == "measured"
        assert prov["params"]["idt_min_duration_ms"] == 100.0
        run_agg(res, m, out)  # 同じ results_dir からの再実行は上書きしてよい
        res2 = tmp / "aoi_result_fixed650"
        write_session(res2, "s_a", hold(CROSSHAIR, 30) + hold(EMPTY, 10))
        write_session(res2, "s_b", hold(CROSSHAIR, 10) + hold(EMPTY, 110))
        with pytest.raises(SystemExit, match="aggregate_fixed650"):
            run_agg(res2, m, out)
        run_agg(res2, m, out, "--overwrite")

    def test_manifest_session_without_result_is_reported(self, two_sessions, capsys):
        tmp, res = two_sessions
        m = write_manifest(tmp / "m.csv", [{"session_base": s, "participant_id": "P1", "group": "low"}
                                           for s in ("s_a", "s_b", "s_missing")])
        run_agg(res, m, tmp / "o")
        assert "s_missing" in capsys.readouterr().out
        qc = pd.read_csv(tmp / "o" / "qc_report.csv").set_index("session_base")
        assert not qc.loc["s_missing", "has_result"] and qc.loc["s_a", "has_result"]
        prov = json.loads((tmp / "o" / "aggregate_provenance.json").read_text(encoding="utf-8"))
        assert prov["manifest_sessions_without_results"] == ["s_missing"]

    def test_aoi_compared_by_content_not_line_endings(self, two_sessions, tmp_path):
        tmp, res = two_sessions
        text = AOI_JSON.read_text(encoding="utf-8")
        lf, crlf = tmp_path / "lf.json", tmp_path / "crlf.json"
        lf.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
        crlf.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        sha = [aoi_config_content_sha256(load_aoi_config(p)[1]) for p in (lf, crlf)]
        assert sha[0] == sha[1]
        # ファイルの sha は違っても、中身のハッシュが同じなら合算できる
        edit_qc(res, "s_a", lambda q: q["inputs"].update({"aoi_config": {"sha256": "aaa", "content_sha256": sha[0]}}))
        edit_qc(res, "s_b", lambda q: q["inputs"].update({"aoi_config": {"sha256": "bbb", "content_sha256": sha[1]}}))
        m = write_manifest(tmp / "m.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                                           {"session_base": "s_b", "participant_id": "P1", "group": "low"}])
        run_agg(res, m, tmp / "o")

    def test_cp932_manifest_is_readable(self, two_sessions, capsys):
        tmp, res = two_sessions
        m = write_manifest(tmp / "m.csv", [{"session_base": "s_a", "participant_id": "P1", "group": "low", "notes": "回線落ち"},
                                           {"session_base": "s_b", "participant_id": "P1", "group": "low", "notes": "なし"}],
                           encoding="cp932")
        run_agg(res, m, tmp / "o")
        assert "CSV UTF-8" in capsys.readouterr().out


class TestParams:
    def test_distance_without_source_is_error(self):
        with pytest.raises(ValueError, match="eye_screen_distance_source"):
            gm.merge_params({"eye_screen_distance_mm": 600})
        assert gm.merge_params({"eye_screen_distance_mm": 600, "eye_screen_distance_source": "fixed"})

    def test_visit_merge_must_not_be_shorter_than_max_gap(self):
        with pytest.raises(ValueError, match="visit_merge_max_gap_ms"):
            gm.merge_params({"max_gap_ms": 250})
        with pytest.raises(ValueError, match="visit_merge_max_gap_ms"):
            gm.merge_params({"visit_merge_max_gap_ms": 0})

    def test_system_time_going_backwards_is_explained(self):
        df = make_df(hold(CROSSHAIR, 30))
        df.loc[10, "system_time_stamp_us"] -= 30_000
        with pytest.raises(ValueError, match="逆行.*pc_time_sec"):
            gm.duration_time_seconds(df, "system_time_stamp_us")


def write_csv(path: Path, points, **kw) -> Path:
    make_df(points, **kw).to_csv(path, index=False)
    return path


def write_segments(path: Path, spans) -> Path:
    pd.DataFrame([{"round": i + 1, "segment_type": "alive", "video_start_sec": s, "video_end_sec": e}
                  for i, (s, e) in enumerate(spans)]).to_csv(path, index=False)
    return path


class TestVer3:
    def test_overwrite_guard_covers_segments_and_sync_and_runs_before_analysis(self, ver3, tmp_path, monkeypatch):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000010.csv", hold(CROSSHAIR, 120))
        seg = write_segments(tmp_path / "seg.csv", [(0.0, 1.0)])
        out = tmp_path / "out"
        base = ["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(out)]
        ver3.main(base + ["--segments", str(seg), "--offset", "0"])
        ver3.main(base + ["--segments", str(seg), "--offset", "0"])  # 同じ条件は上書きしてよい

        # 中止する場合は解析（I-DT）を実行しない
        def must_not_run(*a, **k):
            raise AssertionError("解析の前に中止されていない")
        monkeypatch.setattr(ver3.gm, "analyze_session", must_not_run)
        with pytest.raises(SystemExit, match="区間ファイルの有無"):
            ver3.main(base)  # 区間ファイルの付け忘れ
        with pytest.raises(SystemExit, match="同期オフセット"):
            ver3.main(base + ["--segments", str(seg), "--offset", "0.1"])
        write_segments(seg, [(0.0, 1.5)])
        with pytest.raises(SystemExit, match="区間ファイルの中身"):
            ver3.main(base + ["--segments", str(seg), "--offset", "0"])
        monkeypatch.undo()
        ver3.main(base + ["--segments", str(seg), "--offset", "0", "--overwrite"])
        qc = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))
        assert qc["inputs"]["aoi_config"]["content_sha256"] == aoi_config_content_sha256(load_aoi_config(AOI_JSON)[1])

    def test_manifest_distance_does_not_rewrite_params(self, ver3, tmp_path):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000011.csv", hold(CROSSHAIR, 60))
        m = write_manifest(tmp_path / "m.csv", [{"session_base": csv.stem, "participant_id": "P1", "group": "low",
                                                 "game_resolution": "3840x2160", "aspect": "16:9",
                                                 "eye_screen_distance_cm": "52"}])
        params = tmp_path / "p.json"
        params.write_text(json.dumps({"eye_screen_distance_source": "manifest"}), encoding="utf-8")
        out = tmp_path / "out"
        ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(out), "--manifest", str(m),
                   "--params", str(params)])
        qc = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))
        assert qc["params"]["eye_screen_distance_mm"] == 650.0  # 基準値は残る
        assert qc["eye_screen_distance_used"]["mm"] == pytest.approx(520.0)
        assert qc["eye_screen_distance_used"]["reference_mm"] == 650.0

    def test_partially_recorded_segment_is_flagged(self, ver3, tmp_path, capsys):
        # 記録は 1 秒、区間2は 0.5〜2.5 秒（後半 1.5 秒は記録の外）
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000012.csv", hold(CROSSHAIR, 60))
        seg = write_segments(tmp_path / "seg.csv", [(0.0, 0.4), (0.5, 2.5)])
        out = tmp_path / "out"
        ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(out),
                   "--segments", str(seg), "--offset", "0"])
        assert "coverage" in capsys.readouterr().out
        dq = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))["data_quality"]
        assert dq["low_coverage_rounds"] == [2]
        assert dq["sample_coverage_by_segment"]["0"] == pytest.approx(1.0, abs=0.05)
        assert dq["sample_coverage_by_segment"]["1"] == pytest.approx(0.25, abs=0.05)
        assert dq["valid_rate_by_segment"]["1"] == 1.0  # valid_rate だけでは分からない
        assert dq["min_segment_coverage"] == pytest.approx(0.25, abs=0.05)

    def test_compare_with_proc_tolerates_excel_rounding(self, ver3, tmp_path):
        pts = hold(CROSSHAIR, 60) + hold(EMPTY, 60)
        df = make_df(pts)
        df["pc_time_sec"] = df["pc_time_sec"] + 0.123456789  # 実データと同じく端数のある時刻
        csv = tmp_path / "gaze_T_1_default_20261013_000013.csv"
        df.to_csv(csv, index=False)
        seg = write_segments(tmp_path / "seg.csv", [(0.0, 1.0)])
        # 旧方式：区間と同じ行を残し、Excel で時刻を4桁に丸めて保存した CSV
        in_seg = (df["pc_time_sec"] >= 0.123456789) & (df["pc_time_sec"] < 1.123456789)
        proc = df[in_seg].copy()
        proc["pc_time_sec"] = proc["pc_time_sec"].round(4)
        proc_path = tmp_path / "proc.csv"
        proc.to_csv(proc_path, index=False)
        out = tmp_path / "out"
        ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(out),
                   "--segments", str(seg), "--offset", "0.123456789", "--compare-proc", str(proc_path)])
        cmp = json.loads((out / csv.stem / "qc.json").read_text(encoding="utf-8"))["compare_with_proc"]
        assert cmp["n_unmatched_old"] == 0
        assert cmp["jaccard"] == pytest.approx(1.0)
        assert cmp["n_common"] == 60

    def test_cp932_segments_file(self, ver3, tmp_path):
        csv = write_csv(tmp_path / "gaze_T_1_default_20261013_000014.csv", hold(CROSSHAIR, 60))
        seg = tmp_path / "seg.csv"
        pd.DataFrame([{"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": 0.9,
                       "notes": "途中で回線落ち"}]).to_csv(seg, index=False, encoding="cp932")
        ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--output-dir", str(tmp_path / "out"),
                   "--segments", str(seg), "--offset", "0"])
