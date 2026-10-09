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
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

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
}
# 全セッションで一致していなければならない解析パラメータ（違うと合算できない）
PARAMS_MUST_MATCH = [
    "idt_min_duration_ms", "idt_max_dispersion_deg", "max_gap_ms", "offscreen_in_denominator",
    "duration_time_column", "segment_types", "split_visits_at_gaps", "eye_screen_distance_source",
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
    """合算してはいけないパラメータ違い・AOI定義違いを列挙する。"""
    problems = []
    for key in PARAMS_MUST_MATCH:
        vals = {json.dumps(q["params"].get(key)) for q in qcs}
        if len(vals) > 1:
            problems.append(f"params.{key} がセッション間で異なります: {sorted(vals)}")
    aoi_hash = {q["inputs"]["aoi_config"]["sha256"] for q in qcs}
    if len(aoi_hash) > 1:
        problems.append(f"AOI設定ファイルがセッション間で異なります（sha256 {len(aoi_hash)}種類）")
    return problems


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
    m = metrics.merge(manifest[["session_base", "participant_id", "group"]], on="session_base", how="left")
    if m["participant_id"].isna().any():
        missing = sorted(m.loc[m["participant_id"].isna(), "session_base"].unique())
        raise ValueError(f"台帳に participant_id が無いセッションがあります: {missing}")

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
            gaze_pct_defined=("gaze_pct", lambda s: s.notna().all()),
        ).reset_index()
        g = g.join(per_p, on="participant_id").join(n_sess, on="participant_id")
        g["gaze_pct"] = np.where(
            g["gaze_pct_defined"] & (g["gaze_pct_denominator_samples"] > 0),
            100.0 * g["n_samples_in_aoi"] / g["gaze_pct_denominator_samples"].where(g["gaze_pct_denominator_samples"] > 0),
            np.nan,
        )
        g["fixations_per_min"] = g["fixation_count"] / (g["valid_time_sec"] / 60.0)
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
    res = qc.get("resolution_check", {}) or {}
    calib_passed = calib_acc = None
    calib_file = _resolve((manifest_row or {}).get("calibration_file"))
    if calib_file and calib_file.exists():
        with open(calib_file, "r", encoding="utf-8") as f:
            summ = json.load(f).get("summary", {})
        calib_passed, calib_acc = summ.get("passed"), summ.get("mean_accuracy_deg")
    measured_cm = (manifest_row or {}).get("eye_screen_distance_cm")

    row = {
        "session_base": qc.get("session_base"),
        "participant_id": (manifest_row or {}).get("participant_id"),
        "group": (manifest_row or {}).get("group"),
        "in_manifest": manifest_row is not None,
        "valid_rate_in_segments": dq.get("valid_rate_in_segments"),
        "analysis_time_sec": dq.get("analysis_time_sec"),
        "n_segments": dq.get("n_segments"),
        "n_fixations": dq.get("n_fixations"),
        "offscreen_rate_of_valid": (dq.get("offscreen") or {}).get("rate_of_valid"),
        "offscreen_top": (dq.get("offscreen") or {}).get("n_top"),
        "offscreen_bottom": (dq.get("offscreen") or {}).get("n_bottom"),
        "sync_method": sync.get("method"),
        "sync_within_expected_band": sync.get("within_expected_band"),
        "eye_tracker_distance_median_mm": eye.get("median_mm"),
        "eye_screen_distance_measured_mm": float(measured_cm) * 10 if measured_cm else None,
        "calibration_passed": calib_passed,
        "calibration_mean_accuracy_deg": calib_acc,
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

    flags = [row[k] for k in row if k.startswith("flag_")]
    row["exclude_candidate"] = any(f is True for f in flags)
    row["n_criteria_evaluated"] = sum(f is not None for f in flags)
    return row


def main(argv=None):
    p = argparse.ArgumentParser(description="aoi_analysis-ver3 の結果をセッション横断で集計し、QCレポートを作ります。")
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    p.add_argument("--manifest", type=Path, required=True, help="セッション台帳CSV")
    p.add_argument("--qc-criteria", type=Path, default=None, help="除外基準JSON（未指定ならフラグは評価しない）")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--allow-mixed-params", action="store_true", help="解析パラメータが違うセッションの合算を許す（非推奨）")
    args = p.parse_args(argv)

    criteria = dict(DEFAULT_QC_CRITERIA)
    if args.qc_criteria:
        with open(args.qc_criteria, "r", encoding="utf-8") as f:
            user = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        unknown = sorted(set(user) - set(DEFAULT_QC_CRITERIA))
        if unknown:
            raise SystemExit(f"エラー: 未知の除外基準があります: {unknown}")
        criteria.update(user)

    manifest = pd.read_csv(args.manifest, dtype=str, encoding="utf-8-sig")
    dirs = find_sessions(args.results_dir)
    if not dirs:
        raise SystemExit(f"エラー: {args.results_dir} に解析結果がありません。")
    loaded = [load_session(d) for d in dirs]
    metrics = pd.concat([m for m, _ in loaded], ignore_index=True)
    qcs = [q for _, q in loaded]

    problems = check_params_consistent(qcs)
    if problems:
        for pr in problems:
            print(f"⚠ {pr}")
        if not args.allow_mixed_params:
            raise SystemExit("エラー: 定義の違う結果は合算できません。同じパラメータで解析し直してください。")

    mrows = {r["session_base"]: {k: (None if pd.isna(v) else v) for k, v in r.items()}
             for r in manifest.to_dict("records")}
    qc_report = pd.DataFrame([qc_row(q, mrows.get(q.get("session_base")), criteria) for q in qcs])
    not_in_manifest = qc_report.loc[~qc_report["in_manifest"], "session_base"].tolist()
    if not_in_manifest:
        print(f"⚠ 台帳に無いセッション（参加者集計から外れます）: {not_in_manifest}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    master_long(metrics, manifest).to_csv(args.out_dir / "master_long.csv", index=False, encoding="utf-8-sig")
    in_m = metrics[metrics["session_base"].isin(mrows)]
    if not in_m.empty:
        participant_metrics(in_m, manifest, qc_report).to_csv(
            args.out_dir / "participant_aoi_metrics.csv", index=False, encoding="utf-8-sig")
    qc_report.to_csv(args.out_dir / "qc_report.csv", index=False, encoding="utf-8-sig")
    with open(args.out_dir / "aggregate_provenance.json", "w", encoding="utf-8") as f:
        json.dump({"sessions": [q.get("session_base") for q in qcs], "qc_criteria": criteria,
                   "params_problems": problems, "manifest": str(args.manifest.resolve())},
                  f, ensure_ascii=False, indent=2)
    n_flag = int(qc_report["exclude_candidate"].sum())
    print(f"セッション数: {len(qcs)}  除外候補: {n_flag}  （評価した基準数: {int(qc_report['n_criteria_evaluated'].max())}）")
    print(f"完了: {args.out_dir}")


if __name__ == "__main__":
    main()
