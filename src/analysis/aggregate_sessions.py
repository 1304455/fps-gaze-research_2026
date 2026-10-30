# -*- coding: utf-8 -*-
"""
セッション横断の集計と QC レポート（IMPROVEMENT_PLAN T6）。

入力：aoi_analysis-ver3.py の出力（data/processed/aoi_result/<session_base>/）とセッション台帳
出力（data/processed/aggregate/）
  master_long.csv              整然データ（1行 = 参加者 × セッション × AOI × 指標）
  participant_aoi_metrics.csv  参加者 × AOI。割合の平均ではなく、サンプル数・件数・時間を
                               合算してから比率を出す（試合時間の違いで重みが歪まないように）
  qc_report.csv                1セッション1行。除外基準に該当するセッションは削除せずフラグを付ける

除外基準の数値は研究上の判断なので既定では評価しない（null）。--qc-criteria の JSON で与える
（docs/templates/qc_criteria_template.json）。

使い方（PowerShell）
--------------------
python .\\src\\analysis\\aggregate_sessions.py --manifest .\\data\\sessions_manifest.csv `
    --qc-criteria .\\data\\qc_criteria.json
# 感度分析（650 mm 固定）の集計は出力先を分ける（同じ出力先に別の --results-dir の集計があると中止する）
python .\\src\\analysis\\aggregate_sessions.py --manifest .\\data\\sessions_manifest.csv `
    --results-dir .\\data\\processed\\aoi_result_fixed650 --out-dir .\\data\\processed\\aggregate_fixed650

区間ファイルなし（記録全体）で解析した結果は既定で合算しない（--allow-whole-session）。
台帳は Excel で編集したら『CSV UTF-8（コンマ区切り）』で保存する（cp932 でも警告付きで読む）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_AOI_DIR = Path(__file__).resolve().parent.parent / "aoi_detector"
if str(_AOI_DIR) not in sys.path:
    sys.path.insert(0, str(_AOI_DIR))

from gaze_metrics import read_csv_text  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RESULTS_DIR = _PROJECT_ROOT / "data" / "processed" / "aoi_result"
DEFAULT_OUT_DIR = _PROJECT_ROOT / "data" / "processed" / "aggregate"

METRIC_COLUMNS = [
    "gaze_pct", "n_samples_in_aoi", "gaze_pct_denominator_samples", "fixation_count", "fixations_per_min",
    "total_fixation_duration_sec", "mean_fixation_duration_sec", "visit_count", "revisit_count",
    "ttff_sec_mean_uncensored", "ttff_censored_segments", "analysis_time_sec", "valid_time_sec",
]
MANIFEST_CONDITION_COLUMNS = [
    "participant_id", "group", "rank_current", "rank_peak", "map", "agent", "role", "start_side",
    "game_resolution", "aspect", "minimap_settings", "eye_screen_distance_cm",
]
DEFAULT_QC_CRITERIA = {
    "min_valid_rate_in_segments": None,
    "min_analysis_time_sec": None,
    "require_sync_confirmed_based": None,
    "require_sync_within_expected_band": None,
    "require_calibration_passed": None,
    "require_resolution_ok": None,
    "max_eye_distance_deviation_mm": None,
    # I-DT の視角換算に実際に使った距離（qc.json の eye_screen_distance_used.mm）と基準値（650 mm）の差の上限
    "max_eye_distance_from_reference_mm": None,
    # 区間の行数 / (区間長 ÷ サンプル間隔) の最小値の下限（区間の一部が記録の範囲外にあるセッションの検出）
    "min_segment_coverage": None,
}
GROUP_MISSING_LABEL = "未記入"
GROUP_VALUES = ("low", "high")
# 全セッションで一致していなければならない解析パラメータ（違うと合算できない）
PARAMS_MUST_MATCH = [
    "idt_min_duration_ms", "idt_max_dispersion_deg", "max_gap_ms", "offscreen_in_denominator",
    "duration_time_column", "segment_types", "visit_merge_max_gap_ms", "eye_screen_distance_source",
]


def find_sessions(results_dir: Path) -> list[Path]:
    return sorted(p for p in results_dir.iterdir()
                  if p.is_dir() and (p / "aoi_metrics_session.csv").exists() and (p / "qc.json").exists())


def load_session(d: Path) -> tuple[pd.DataFrame, dict]:
    metrics = pd.read_csv(d / "aoi_metrics_session.csv", encoding="utf-8-sig")
    with open(d / "qc.json", "r", encoding="utf-8") as f:
        qc = json.load(f)
    metrics.insert(0, "session_base", qc.get("session_base", d.name))
    return metrics, qc


def check_params_consistent(qcs: list[dict]) -> list[str]:
    """合算してはいけないパラメータ違い・AOI定義違い・解析スクリプトの版の違いを列挙する。"""
    problems = []
    for key in PARAMS_MUST_MATCH:
        vals = {json.dumps(q["params"].get(key)) for q in qcs}
        if len(vals) > 1:
            problems.append(f"params.{key} がセッション間で異なります: {sorted(vals)}")
    # 距離を固定値にしたセッション同士は、その値も一致していなければならない（I-DT の閾値の視角が変わる）。
    # measured / manifest はセッションごとに違うのが正しいので比べない
    fixed = {json.dumps(q["params"].get("eye_screen_distance_mm")) for q in qcs
             if q["params"].get("eye_screen_distance_source") == "fixed"}
    if len(fixed) > 1:
        problems.append(f"eye_screen_distance_source=fixed なのに eye_screen_distance_mm が異なります: {sorted(fixed)}")
    versions = {json.dumps((q.get("script") or {}).get("version")) for q in qcs}
    if len(versions) > 1:
        problems.append(f"aoi_analysis の版（script.version）がセッション間で異なります: {sorted(versions)}")
    # AOI は中身のハッシュ（改行コードに依存しない）で比べる。古い qc.json に無ければファイルの sha256
    aoi_cfgs = [q["inputs"]["aoi_config"] for q in qcs]
    key = "content_sha256" if all(a.get("content_sha256") for a in aoi_cfgs) else "sha256"
    aoi_hash = {a.get(key) for a in aoi_cfgs}
    if len(aoi_hash) > 1:
        problems.append(f"AOI設定がセッション間で異なります（{key} {len(aoi_hash)}種類）")
    return problems


def whole_session_sessions(qcs: list[dict]) -> list[str]:
    """区間ファイルなし（記録全体を1区間。購入フェーズ・観戦を含む）で解析されたセッション。"""
    return [q.get("session_base") for q in qcs if (q.get("inputs") or {}).get("segments_file") is None]


def params_warnings(qcs: list[dict]) -> list[str]:
    """合算は止めないが、記録しておくべき違い（git コミットの違いなど）。"""
    commits = {(q.get("script") or {}).get("git_commit") for q in qcs}
    if len(commits) > 1:
        return [f"解析時の git コミットがセッション間で異なります: {sorted(str(c) for c in commits)}"
                "（script.version が同じなら定義は同じ前提。気になる場合は同じコミットで解析し直す）"]
    return []


def load_manifest(path: Path) -> pd.DataFrame:
    """
    台帳を読み、Excel で編集したときに入りがちな揺れを整える。
    - 全列が空の行（末尾の ",,,," など）は捨てる
    - 文字列の前後の空白を除く（"low " と "low" を別の群にしないため）。空文字は欠損にする
    """
    m = read_csv_text(path, dtype=str)
    m = m.apply(lambda s: s.str.strip())
    m = m.replace("", np.nan)
    return m.dropna(how="all")  # index は CSV のデータ行の位置のまま（エラー表示で行番号に使う）


def validate_manifest(manifest: pd.DataFrame) -> None:
    """
    台帳の整合性を検査する。
    - session_base が空の行（他の列には値がある）・重複はエラー（merge で指標の行が増え、Gaze % が水増しされるため）
    - 1人の参加者に group が2種類以上（空欄を含む）あるとエラー（参加者が2行に分かれ、どちらの比率も過小になるため）
    - group が low / high 以外なら警告
    """
    if "session_base" not in manifest.columns:
        raise SystemExit("エラー: 台帳に session_base 列がありません。")
    blank = manifest.index[manifest["session_base"].isna()]
    if len(blank):
        raise SystemExit(f"エラー: 台帳に session_base が空の行があります（CSV の {[int(i) + 2 for i in blank]} 行目）。")
    sb = manifest["session_base"]
    dup = sorted(sb[sb.duplicated()].unique())
    if dup:
        raise SystemExit(f"エラー: 台帳に同じ session_base の行が複数あります: {dup}。1セッション1行にしてください。")
    if "participant_id" in manifest.columns and "group" in manifest.columns:
        g = manifest.dropna(subset=["participant_id"]).assign(group=lambda d: d["group"].fillna(""))
        n_groups = g.groupby("participant_id")["group"].nunique()
        bad = {pid: sorted(g.loc[g["participant_id"] == pid, "group"].unique()) for pid in n_groups[n_groups > 1].index}
        if bad:
            raise SystemExit(f"エラー: 同じ参加者に group が複数あります（空欄は ''）: {bad}。1人1群にそろえてください。")
        odd = sorted(set(manifest["group"].dropna()) - set(GROUP_VALUES))
        if odd:
            print(f"⚠ 台帳の group に {list(GROUP_VALUES)} 以外の値があります: {odd}（その値の群として集計します）")


def master_long(metrics: pd.DataFrame, manifest: "pd.DataFrame | None") -> pd.DataFrame:
    cols = [c for c in METRIC_COLUMNS if c in metrics.columns]
    long = metrics.melt(id_vars=["session_base", "aoi"], value_vars=cols, var_name="metric", value_name="value")
    if manifest is not None:
        keep = ["session_base"] + [c for c in MANIFEST_CONDITION_COLUMNS if c in manifest.columns]
        long = long.merge(manifest[keep], on="session_base", how="left")
    lead = ["session_base"] + [c for c in MANIFEST_CONDITION_COLUMNS if c in long.columns] + ["aoi", "metric", "value"]
    return long[lead]


def participant_metrics(metrics: pd.DataFrame, manifest: pd.DataFrame,
                        session_flags: "pd.DataFrame | None" = None) -> pd.DataFrame:
    """
    参加者 × AOI の集計。サンプル数・件数・時間を合算してから比率を出す。
    session_flags があれば exclude_candidate のセッションを除いた版も出す（元データは消さない）。
    """
    m = metrics.merge(manifest[["session_base", "participant_id", "group"]], on="session_base", how="left",
                      validate="many_to_one")
    if m["participant_id"].isna().any():
        missing = sorted(m.loc[m["participant_id"].isna(), "session_base"].unique())
        raise ValueError(f"台帳に participant_id が無いセッションがあります: {missing}")
    # group が空の行は groupby で黙って消えるので、ラベルを付けて残す
    no_group = m["group"].isna() | (m["group"].astype(str).str.strip() == "")
    if no_group.any():
        print(f"⚠ 台帳の group が空の参加者があります（『{GROUP_MISSING_LABEL}』として集計に残します）: "
              f"{sorted(m.loc[no_group, 'participant_id'].unique())}")
        m.loc[no_group, "group"] = GROUP_MISSING_LABEL

    def agg(df: pd.DataFrame) -> pd.DataFrame:
        sess = df.drop_duplicates(["session_base"])[["participant_id", "session_base", "valid_time_sec",
                                                     "gaze_pct_denominator_samples", "analysis_time_sec"]]
        per_p = sess.groupby("participant_id")[["valid_time_sec", "gaze_pct_denominator_samples",
                                                "analysis_time_sec"]].sum()
        n_sess = sess.groupby("participant_id")["session_base"].nunique().rename("n_sessions")
        g = df.groupby(["participant_id", "group", "aoi"], sort=False).agg(
            n_samples_in_aoi=("n_samples_in_aoi", "sum"),
            fixation_count=("fixation_count", "sum"),
            total_fixation_duration_sec=("total_fixation_duration_sec", "sum"),
            visit_count=("visit_count", "sum"),
            revisit_count=("revisit_count", "sum"),
            ttff_censored_segments=("ttff_censored_segments", "sum"),
            # metrics_session と同じく、どれか1セッションで定義されていれば合算値で比率を出す。
            # 分母0のセッション（gaze_pct=NaN）は分子・分母とも0を足すだけなので、他のセッションの値を消さない。
            # offscreen を分母から外す設定の offscreen 行は全セッションで NaN なので NaN のまま
            gaze_pct_defined=("gaze_pct", lambda s: s.notna().any()),
        ).reset_index()
        g = g.join(per_p, on="participant_id").join(n_sess, on="participant_id")
        g["gaze_pct"] = np.where(
            g["gaze_pct_defined"] & (g["gaze_pct_denominator_samples"] > 0),
            100.0 * g["n_samples_in_aoi"] / g["gaze_pct_denominator_samples"].where(g["gaze_pct_denominator_samples"] > 0),
            np.nan,
        )
        g["fixations_per_min"] = (g["fixation_count"] / (g["valid_time_sec"] / 60.0)).where(g["valid_time_sec"] > 0)
        g["mean_fixation_duration_sec"] = (g["total_fixation_duration_sec"] / g["fixation_count"]).where(g["fixation_count"] > 0)
        return g.drop(columns=["gaze_pct_defined"])

    out = agg(m)
    out.insert(0, "session_set", "all")
    if session_flags is not None:
        keep = session_flags.loc[~session_flags["exclude_candidate"].fillna(False).astype(bool), "session_base"]
        mk = m[m["session_base"].isin(keep)]
        if not mk.empty:
            ex = agg(mk)
            ex.insert(0, "session_set", "excluding_flagged")
            out = pd.concat([out, ex], ignore_index=True)
    return out


def _resolve(path_str: "str | None") -> "Path | None":
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def qc_row(qc: dict, manifest_row: "dict | None", criteria: dict) -> dict:
    dq = qc.get("data_quality", {})
    sync = qc.get("sync", {}) or {}
    eye = dq.get("eye_distance_from_tracker", {}) or {}
    used = qc.get("eye_screen_distance_used", {}) or {}
    res = qc.get("resolution_check", {}) or {}
    calib_passed = calib_acc = calib_criteria = calib_acc_bin = None
    calib_file = _resolve((manifest_row or {}).get("calibration_file"))
    if calib_file and calib_file.exists():
        with open(calib_file, "r", encoding="utf-8") as f:
            summ = json.load(f).get("summary", {})
        calib_passed, calib_acc = summ.get("passed"), summ.get("mean_accuracy_deg")
        # 合否の基準の版（calibrate_validate 1.9.0 以降。無ければ旧基準＝左右眼の誤差の平均、credits を含む）
        calib_criteria = summ.get("criteria_version") or "legacy-per-eye"
        calib_acc_bin = summ.get("mean_accuracy_binocular_deg")
    measured_cm = (manifest_row or {}).get("eye_screen_distance_cm")

    row = {
        "session_base": qc.get("session_base"),
        "participant_id": (manifest_row or {}).get("participant_id"),
        "group": (manifest_row or {}).get("group"),
        "in_manifest": manifest_row is not None,
        "has_result": True,
        "has_segments_file": (qc.get("inputs") or {}).get("segments_file") is not None,
        "valid_rate_in_segments": dq.get("valid_rate_in_segments"),
        "analysis_time_sec": dq.get("analysis_time_sec"),
        "n_segments": dq.get("n_segments"),
        "min_segment_coverage": dq.get("min_segment_coverage"),
        "n_fixations": dq.get("n_fixations"),
        "offscreen_rate_of_valid": (dq.get("offscreen") or {}).get("rate_of_valid"),
        "offscreen_top": (dq.get("offscreen") or {}).get("n_top"),
        "offscreen_bottom": (dq.get("offscreen") or {}).get("n_bottom"),
        "sync_method": sync.get("method"),
        "sync_within_expected_band": sync.get("within_expected_band"),
        "eye_tracker_distance_median_mm": eye.get("median_mm"),
        "eye_screen_distance_used_mm": used.get("mm"),
        "eye_screen_distance_source": used.get("source"),
        "eye_screen_distance_reference_mm": used.get("reference_mm", (qc.get("params") or {}).get("eye_screen_distance_mm")),
        "eye_screen_distance_measured_mm": float(measured_cm) * 10 if measured_cm else None,
        "calibration_passed": calib_passed,
        "calibration_mean_accuracy_deg": calib_acc,
        "calibration_criteria_version": calib_criteria,
        "calibration_mean_accuracy_binocular_deg": calib_acc_bin,
        "resolution_ok": res.get("ok"),
        "pc_vs_system_interval_sd_ms": (dq.get("time_base_comparison") or {}).get("interval_difference_sd_ms"),
        "script_version": (qc.get("script") or {}).get("version"),
        "git_commit": (qc.get("script") or {}).get("git_commit"),
    }

    def flag(name, value):
        row[f"flag_{name}"] = value

    c = criteria
    v = row["valid_rate_in_segments"]
    flag("low_valid_rate", None if c["min_valid_rate_in_segments"] is None or v is None
         else v < c["min_valid_rate_in_segments"])
    a = row["analysis_time_sec"]
    flag("short_analysis_time", None if c["min_analysis_time_sec"] is None or a is None
         else a < c["min_analysis_time_sec"])
    flag("sync_not_confirmed_based", None if not c["require_sync_confirmed_based"]
         else not str(row["sync_method"] or "").startswith("metadata_confirmed_based"))
    flag("sync_out_of_band", None if not c["require_sync_within_expected_band"]
         else row["sync_within_expected_band"] is not True)
    flag("calibration_failed", None if not c["require_calibration_passed"] else row["calibration_passed"] is not True)
    flag("resolution_mismatch", None if not c["require_resolution_ok"] else row["resolution_ok"] is not True)
    dev = c["max_eye_distance_deviation_mm"]
    meas, med = row["eye_screen_distance_measured_mm"], row["eye_tracker_distance_median_mm"]
    flag("eye_distance_deviation", None if dev is None or meas is None or med is None else abs(meas - med) > dev)
    ref_dev = c["max_eye_distance_from_reference_mm"]
    u, r = row["eye_screen_distance_used_mm"], row["eye_screen_distance_reference_mm"]
    flag("eye_distance_from_reference", None if ref_dev is None or u is None or r is None else abs(u - r) > ref_dev)
    cov_min, cov = c["min_segment_coverage"], row["min_segment_coverage"]
    flag("low_segment_coverage", None if cov_min is None or cov is None else cov < cov_min)

    flags = [row[k] for k in row if k.startswith("flag_")]
    row["exclude_candidate"] = any(f is True for f in flags)
    row["n_criteria_evaluated"] = sum(f is not None for f in flags)
    return row


def missing_result_row(manifest_row: dict) -> dict:
    """台帳にあるのに解析結果が無いセッションの qc_report 行（「出力に無い」を「無い」と区別して残す）。"""
    return {"session_base": manifest_row.get("session_base"), "participant_id": manifest_row.get("participant_id"),
            "group": manifest_row.get("group"), "in_manifest": True, "has_result": False,
            "exclude_candidate": False, "n_criteria_evaluated": 0}


def check_existing_aggregate(out_dir: Path, results_dir: Path, overwrite: bool) -> None:
    """
    出力先に別の --results-dir（例：感度分析 aoi_result_fixed650）から作った集計があれば上書きしない。
    主解析の集計が感度分析の値で黙って置き換わるのを防ぐ。
    """
    prov = out_dir / "aggregate_provenance.json"
    if overwrite or not prov.exists():
        return
    try:
        with open(prov, "r", encoding="utf-8") as f:
            old = json.load(f).get("results_dir")
    except (OSError, ValueError):
        raise SystemExit(f"エラー: 既存の {prov} を読めません。確認してから --overwrite で上書きしてください。")
    if old is not None and Path(old) != results_dir.resolve():
        raise SystemExit(
            f"エラー: {out_dir} には別の解析結果（{old}）から作った集計があります。上書きしないよう中止しました。\n"
            "  感度分析の集計は出力先を分けてください。例：\n"
            "    --out-dir .\\data\\processed\\aggregate_fixed650\n"
            "  意図して置き換える場合だけ --overwrite を付けてください。")


def main(argv=None):
    p = argparse.ArgumentParser(description="aoi_analysis-ver3 の結果をセッション横断で集計し、QCレポートを作ります。")
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    p.add_argument("--manifest", type=Path, required=True, help="セッション台帳CSV")
    p.add_argument("--qc-criteria", type=Path, default=None, help="除外基準JSON（未指定ならフラグは評価しない）")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help="出力先（既定: data/processed/aggregate）。感度分析の集計は別にする（例: aggregate_fixed650）")
    p.add_argument("--allow-mixed-params", action="store_true", help="解析パラメータが違うセッションの合算を許す（非推奨）")
    p.add_argument("--allow-whole-session", action="store_true",
                   help="区間ファイルなし（記録全体）で解析したセッションの合算を許す（動作確認用。主解析には使わない）")
    p.add_argument("--overwrite", action="store_true", help="出力先に別の --results-dir から作った集計があっても上書きする")
    args = p.parse_args(argv)

    criteria = dict(DEFAULT_QC_CRITERIA)
    if args.qc_criteria:
        with open(args.qc_criteria, "r", encoding="utf-8") as f:
            user = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        unknown = sorted(set(user) - set(DEFAULT_QC_CRITERIA))
        if unknown:
            raise SystemExit(f"エラー: 未知の除外基準があります: {unknown}")
        criteria.update(user)

    manifest = load_manifest(args.manifest)
    validate_manifest(manifest)
    dirs = find_sessions(args.results_dir)
    if not dirs:
        raise SystemExit(f"エラー: {args.results_dir} に解析結果がありません。")
    check_existing_aggregate(args.out_dir, args.results_dir, args.overwrite)
    loaded = [load_session(d) for d in dirs]
    metrics = pd.concat([m for m, _ in loaded], ignore_index=True)
    qcs = [q for _, q in loaded]

    problems = check_params_consistent(qcs)
    if problems:
        for pr in problems:
            print(f"⚠ {pr}")
        if not args.allow_mixed_params:
            raise SystemExit("エラー: 定義の違う結果は合算できません。同じパラメータで解析し直してください。")
    whole = whole_session_sessions(qcs)
    if whole:
        print(f"⚠ 区間ファイルなし（記録全体。購入フェーズ・観戦を含む）で解析したセッション: {whole}")
        if not args.allow_whole_session:
            raise SystemExit("エラー: 主解析は分析区間（alive）だけを集計します。区間ファイルを付けて解析し直してください"
                             "（動作確認として合算する場合だけ --allow-whole-session）。")
    warnings = params_warnings(qcs)
    for w in warnings:
        print(f"⚠ {w}")

    mrows = {r["session_base"]: {k: (None if pd.isna(v) else v) for k, v in r.items()}
             for r in manifest.to_dict("records")}
    qc_report = pd.DataFrame([qc_row(q, mrows.get(q.get("session_base")), criteria) for q in qcs])
    not_in_manifest = qc_report.loc[~qc_report["in_manifest"], "session_base"].tolist()
    if not_in_manifest:
        print(f"⚠ 台帳に無いセッション（参加者集計から外れます）: {not_in_manifest}")
    analyzed = {q.get("session_base") for q in qcs}
    no_result = [sb for sb in mrows if sb not in analyzed]
    if no_result:
        print(f"⚠ 台帳にあるが解析結果が無いセッション（qc_report に has_result=False で残します）: {no_result}")
        qc_report = pd.concat([qc_report, pd.DataFrame([missing_result_row(mrows[sb]) for sb in no_result])],
                              ignore_index=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    master_long(metrics, manifest).to_csv(args.out_dir / "master_long.csv", index=False, encoding="utf-8-sig")
    in_m = metrics[metrics["session_base"].isin(mrows)]
    if not in_m.empty:
        participant_metrics(in_m, manifest, qc_report).to_csv(
            args.out_dir / "participant_aoi_metrics.csv", index=False, encoding="utf-8-sig")
    qc_report.to_csv(args.out_dir / "qc_report.csv", index=False, encoding="utf-8-sig")
    with open(args.out_dir / "aggregate_provenance.json", "w", encoding="utf-8") as f:
        json.dump({"sessions": [q.get("session_base") for q in qcs], "qc_criteria": criteria,
                   "params_problems": problems, "params_warnings": warnings,
                   "manifest": str(args.manifest.resolve()),
                   "results_dir": str(args.results_dir.resolve()),
                   # 全セッションで一致を確認済みの解析パラメータ（--allow-mixed-params のときは先頭セッションの値）
                   "params": qcs[0].get("params"),
                   "eye_screen_distance_source": (qcs[0].get("params") or {}).get("eye_screen_distance_source"),
                   "whole_session_sessions": whole,
                   "manifest_sessions_without_results": no_result},
                  f, ensure_ascii=False, indent=2)
    n_flag = int(qc_report["exclude_candidate"].astype(bool).sum())
    print(f"セッション数: {len(qcs)}  除外候補: {n_flag}  （評価した基準数: {int(qc_report['n_criteria_evaluated'].max())}）")
    print(f"完了: {args.out_dir}")


if __name__ == "__main__":
    main()
