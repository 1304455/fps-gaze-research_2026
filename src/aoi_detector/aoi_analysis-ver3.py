# -*- coding: utf-8 -*-
"""
AOI 解析スクリプト ver.3（I-DT 注視検出・分析区間対応）

旧版（archive/aoi_analysis-ver2.py。中身は ver.3 相当の改良版）からの主な変更
------------------------------------------------------------------------------
1. 「注視」を I-DT（分散閾値法）で検出する。旧版の fixation_count は
   「同一AOIに属する連続サンプル区間（run）の数」で、注視検出をしていなかった
2. 分析区間（data/annotations/<session_base>_segments.csv）に対応。区間外のサンプルは
   行を削除せず segment_id=NaN として残し、集計から外す。注視・訪問・遷移は
   区間境界と欠測ギャップをまたがない（手作業の行削除による連結を防ぐ）
3. 注視時間を「サンプル数×dt」ではなく時刻差（最初〜最後のサンプル＋1サンプル間隔）で求める
4. 画面外の有効サンプル（offscreen）を outside と区別する。どの辺の外かを QC に残す
5. 全カテゴリ（設定AOI + outside + offscreen）の行を常に出力する（0件も明示）
6. 定義・パラメータ・入力ハッシュ・git コミットを qc.json に残す（provenance）

ロジック本体は gaze_metrics.py / aoi_geometry.py（テストから import するため）。

使い方（PowerShell）
--------------------
python .\\src\\aoi_detector\\aoi_analysis-ver3.py `
    --input .\\data\\raw\\gaze_P01_1_default_20261020_140358.csv `
    --aoi .\\src\\aoi_detector\\valorant_hud_aoi_circular.json `
    --segments .\\data\\annotations\\gaze_P01_1_default_20261020_140358_segments.csv `
    --sync "C:\\Users\\kawalab\\Videos\\2026-10-20 14-04-19_sync.json" `
    --manifest .\\data\\sessions_manifest.csv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import gaze_metrics as gm  # noqa: E402
from aoi_geometry import find_overlapping_aois, get_display, load_aoi_config  # noqa: E402

SCRIPT_VERSION = "3.0.0-idt-segments"
_PROJECT_ROOT = _THIS_DIR.parent.parent
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "processed" / "aoi_result"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_commit() -> "str | None":
    try:
        out = subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        commit = out.stdout.strip() or None
        dirty = subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return f"{commit}{'-dirty' if dirty else ''}" if commit else None
    except Exception:
        return None


def load_sync(args) -> dict:
    """同期オフセットを決める。--offset が --sync より優先。"""
    if args.offset is not None:
        return {"offset_sec": args.offset, "method": "cli_offset", "source_file": None}
    if args.sync is None:
        return {"offset_sec": None, "method": None, "source_file": None}
    with open(args.sync, "r", encoding="utf-8") as f:
        data = json.load(f)
    method = data.get("method", "legacy_unknown")
    if "request_based" in method and not args.allow_request_based_sync:
        raise SystemExit(
            f"エラー: sync の method={method} です。request_based は卒論の集計に使わない取り決めです。"
            "どうしても使う場合は --allow-request-based-sync を指定してください。"
        )
    sync_csv = data.get("csv")
    if sync_csv and Path(sync_csv).stem != Path(args.input).stem:
        print(f"⚠ 警告: sync JSON の csv（{Path(sync_csv).name}）と --input の名前が一致しません。")
    source = data.get("source") or {}
    return {
        "offset_sec": float(data["offset_sec"]),
        "method": method,
        "source_file": str(Path(args.sync).resolve()),
        "delta_sec_confirmed_based": source.get("delta_sec_confirmed_based"),
        "first_sample_pc_time_sec": source.get("first_sample_pc_time_sec"),
        "within_expected_band": source.get("within_expected_band"),
    }


def load_manifest_row(path: "str | None", session_base: str) -> "dict | None":
    if not path:
        return None
    m = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    if "session_base" not in m.columns:
        raise SystemExit("エラー: セッション台帳に session_base 列がありません。")
    hit = m[m["session_base"] == session_base]
    if hit.empty:
        print(f"⚠ 警告: セッション台帳に {session_base} の行がありません。")
        return None
    if len(hit) > 1:
        raise SystemExit(f"エラー: セッション台帳に {session_base} の行が複数あります。")
    return {k: (None if pd.isna(v) else v) for k, v in hit.iloc[0].to_dict().items()}


def check_resolution(manifest_row: "dict | None", display, allow_mismatch: bool) -> dict:
    """
    台帳のゲーム解像度・アスペクトが AOI 定義の前提と一致するか（T5-4）。
    一致しなければエラー（--allow-resolution-mismatch で警告に落とせる）。
    """
    result = {"checked": False}
    if manifest_row is None or display is None:
        return result
    expected = f"{display.width_px}x{display.height_px}"
    res = (manifest_row.get("game_resolution") or "").replace(" ", "").lower().replace("×", "x")
    aspect = (manifest_row.get("aspect") or "").replace(" ", "")
    result = {"checked": True, "expected": expected, "game_resolution": res or None, "aspect": aspect or None}
    problems = []
    if not res:
        problems.append("台帳の game_resolution が空です")
    elif res != expected:
        problems.append(f"game_resolution={res}（AOI定義は {expected}）")
    if aspect and aspect != "16:9":
        problems.append(f"aspect={aspect}（AOI定義は 16:9）")
    result["ok"] = not problems
    result["problems"] = problems
    if problems:
        msg = "AOI定義の前提と実験条件が一致しません: " + "; ".join(problems)
        if allow_mismatch:
            print(f"⚠ 警告: {msg}")
        else:
            raise SystemExit(f"エラー: {msg}（--allow-resolution-mismatch で続行可）")
    return result


def compare_with_proc(df: pd.DataFrame, samples: pd.DataFrame, proc_path: str) -> dict:
    """
    旧方式（Excelで行削除した *_proc.csv）と新方式（区間ファイル）で、
    分析対象の有効サンプルがどれだけ一致するかを pc_time_sec で突き合わせる（T3-3）。
    """
    proc = pd.read_csv(proc_path)
    gm.validate_columns(proc)
    proc_valid = gm.compute_valid_mask(proc)
    old_t = set(np.round(pd.to_numeric(proc.loc[proc_valid, "pc_time_sec"]).to_numpy(), 6))
    new_t = set(np.round(samples.loc[samples["valid"] & samples["segment_id"].notna(), "pc_time_sec"].to_numpy(), 6))
    both = old_t & new_t
    return {
        "proc_file": str(Path(proc_path).resolve()),
        "n_valid_old_proc": len(old_t),
        "n_valid_new_segments": len(new_t),
        "n_common": len(both),
        "n_only_old": len(old_t - new_t),
        "n_only_new": len(new_t - old_t),
        "jaccard": (len(both) / len(old_t | new_t)) if (old_t | new_t) else math.nan,
    }


def to_jsonable(o):
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def build_arg_parser():
    p = argparse.ArgumentParser(description="Tobii視線CSVを I-DT 注視検出＋分析区間で AOI 解析します（ver.3）。")
    p.add_argument("--input", required=True, help="視線CSV（data/raw の生データ。上書きはしない）")
    p.add_argument("--aoi", required=True, help="AOI設定JSON（display を含むもの）")
    p.add_argument("--params", default=None, help="解析パラメータJSON（未指定なら既定値。analysis_params_default.json 参照）")
    p.add_argument("--segments", default=None, help="区間ファイル（data/annotations/<session_base>_segments.csv）")
    p.add_argument("--sync", default=None, help="gaze_visualizer_v3.py が保存した *_sync.json")
    p.add_argument("--offset", type=float, default=None, help="同期オフセット秒（gaze_time = video_time + offset）。--sync より優先")
    p.add_argument("--allow-request-based-sync", action="store_true", help="request_based の sync を許可する（非推奨）")
    p.add_argument("--manifest", default=None, help="セッション台帳CSV（data/sessions_manifest.csv）")
    p.add_argument("--allow-resolution-mismatch", action="store_true", help="台帳の解像度がAOI定義と違ってもエラーにしない")
    p.add_argument("--session-base", default=None, help="セッション名（既定：入力CSVのファイル名から拡張子を除いたもの）")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力の親フォルダ（この下に <session_base>/ を作る）")
    p.add_argument("--compare-proc", default=None, help="旧方式の *_proc.csv。新方式との有効サンプルの一致を qc.json に記録する")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    session_base = args.session_base or input_path.stem

    # --- 設定 ---
    user_params = None
    if args.params:
        with open(args.params, "r", encoding="utf-8") as f:
            user_params = json.load(f)
    params = gm.merge_params(user_params)
    aois, raw_aoi = load_aoi_config(args.aoi)
    display = get_display(raw_aoi)
    if display is None:
        raise SystemExit("エラー: AOI設定JSONに display（画面の物理寸法）がありません。視角での注視検出に必要です。")

    manifest_row = load_manifest_row(args.manifest, session_base)
    resolution_check = check_resolution(manifest_row, display, args.allow_resolution_mismatch)

    distance_note = "固定値（eye_screen_distance_mm）"
    if params["eye_screen_distance_source"] == "manifest":
        val = (manifest_row or {}).get("eye_screen_distance_cm")
        if not val:
            raise SystemExit("エラー: eye_screen_distance_source=manifest ですが、台帳に eye_screen_distance_cm がありません。")
        params["eye_screen_distance_mm"] = float(val) * 10.0
        distance_note = "セッション台帳の実測値（eye_screen_distance_cm × 10）"

    # --- 入力 ---
    df = pd.read_csv(input_path)
    sync = load_sync(args)
    if args.segments:
        if sync["offset_sec"] is None:
            raise SystemExit("エラー: 区間ファイルを使う場合は --sync か --offset が必要です（区間は動画時刻のため）。")
        seg_raw = pd.read_csv(args.segments, encoding="utf-8-sig")
        segments = gm.segments_from_video_time(seg_raw, sync["offset_sec"], params["segment_types"])
        if segments.empty:
            raise SystemExit(f"エラー: segment_type が {params['segment_types']} の区間がありません。")
    else:
        segments = None
        print("⚠ 警告: 区間ファイルが指定されていません。記録全体（購入フェーズ・観戦を含む）を1区間として解析します。"
              "卒論の主解析には使わないでください。")

    # --- 解析 ---
    res = gm.analyze_session(df, aois, display, params, segments)

    # --- 出力 ---
    out_dir = Path(args.output_dir) / session_base
    if out_dir.resolve().is_relative_to((_PROJECT_ROOT / "data" / "raw").resolve()):
        raise SystemExit("エラー: data/raw の下には出力しません。")
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(df_out: pd.DataFrame, name: str, index=False):
        df_out.to_csv(out_dir / name, index=index, encoding="utf-8-sig")

    write(res["samples"], "samples_classified.csv")
    write(res["fixations"], "fixations.csv")
    write(res["visits"], "visits.csv")
    write(res["segments"], "segments_used.csv")
    write(res["metrics_by_segment"], "aoi_metrics_by_segment.csv")
    write(res["metrics_session"], "aoi_metrics_session.csv")
    write(res["transitions_long"], "transitions_long.csv")
    write(res["transitions_matrix"], "transitions_matrix.csv", index=True)

    samples = res["samples"]
    in_seg = samples["segment_id"].notna()
    seg_valid = samples[in_seg].groupby("segment_id")["valid"].agg(["size", "sum"])
    qc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": {"name": Path(__file__).name, "version": SCRIPT_VERSION, "git_commit": git_commit()},
        "session_base": session_base,
        "inputs": {
            "gaze_csv": {"path": str(input_path.resolve()), "sha256": sha256_of(input_path)},
            "aoi_config": {"path": str(Path(args.aoi).resolve()), "sha256": sha256_of(Path(args.aoi))},
            "params_file": ({"path": str(Path(args.params).resolve()), "sha256": sha256_of(Path(args.params))}
                            if args.params else None),
            "segments_file": ({"path": str(Path(args.segments).resolve()), "sha256": sha256_of(Path(args.segments))}
                              if args.segments else None),
            "manifest_file": str(Path(args.manifest).resolve()) if args.manifest else None,
        },
        "sync": sync,
        "params": params,
        "eye_screen_distance_used": {"mm": params["eye_screen_distance_mm"], "source": distance_note},
        "display": display.as_dict(),
        "aoi": {
            "screen_name": raw_aoi.get("screen_name"),
            "priority_order": [a["name"] for a in aois],
            "priority_rule": "重なりがある場合は AOI JSON の記載順で先に一致したものに割り当てる",
            "overlaps": find_overlapping_aois(aois),
            "radius_sources": {a["name"]: a["radius_source"] for a in aois if a["type"] == "circle"},
            "boundary_rule": "矩形は [min, max)、円（楕円）は境界を含む",
        },
        "definitions": {
            "valid": "gaze_missing=False かつ 左右いずれかの gaze_point_validity=1 かつ center_x/center_y が非NaN",
            "offscreen": "有効サンプルのうち center_x/center_y が [0,1] の外",
            "gaze_pct": "区間内で sample_aoi がそのAOIの有効サンプル数 / 分母。分母は区間内の有効サンプル数"
                        + ("（offscreen を含む）" if params["offscreen_in_denominator"] else "（offscreen を除く）"),
            "fixation": "I-DT。区間ごと・欠測ギャップ（max_gap_ms 超）で分割した系列の内部で検出。"
                        "分散 = (max θx − min θx) + (max θy − min θy)。θ は画面中央正面・距離 D を仮定した視角",
            "fixation_duration": "最後のサンプル時刻 − 最初のサンプル時刻 + 名目サンプル間隔",
            "fixation_aoi": "注視の重心（サンプル座標の平均）が入る AOI",
            "visit": "同一AOIに割り当てられた連続注視のまとまり。区間をまたがない"
                     + ("。欠測ギャップもまたがない" if params["split_visits_at_gaps"] else ""),
            "revisit_count": "区間ごとに max(訪問数 − 1, 0) を求め、セッションでは合計",
            "fixations_per_min": "注視数 / (区間内の有効サンプル数 × 名目サンプル間隔 / 60)",
            "ttff_sec": "区間の開始から、そのAOIへの最初の注視の開始まで。注視が無い区間は NaN かつ ttff_censored=True",
            "session_aggregation": "割合の平均ではなく、サンプル数・件数・時間を合算してから比率を出す",
            "sample_class": "aoi_fixation / aoi_nonfixation / outside / offscreen / invalid（モジュール docstring 参照）",
        },
        "data_quality": {
            "n_rows": int(len(samples)),
            "n_valid": int(samples["valid"].sum()),
            "valid_rate_all": float(samples["valid"].mean()) if len(samples) else None,
            "n_rows_in_segments": int(in_seg.sum()),
            "valid_rate_in_segments": float(samples.loc[in_seg, "valid"].mean()) if in_seg.any() else None,
            "n_segments": int(len(res["segments"])),
            "analysis_time_sec": float((res["segments"]["gaze_end_sec"] - res["segments"]["gaze_start_sec"]).sum()),
            "valid_rate_by_segment": {
                str(int(k)): (float(r["sum"] / r["size"]) if r["size"] else None) for k, r in seg_valid.iterrows()
            },
            "nominal_sample_interval_sec": res["sample_dt"],
            "n_fixations": int(len(res["fixations"])),
            "offscreen": gm.offscreen_summary(samples),
            "time_base_comparison": gm.time_base_comparison(df),
            "eye_distance_from_tracker": gm.eye_distance_summary(df),
        },
        "manifest_row": manifest_row,
        "resolution_check": resolution_check,
    }
    if args.compare_proc:
        qc["compare_with_proc"] = compare_with_proc(df, samples, args.compare_proc)
    with open(out_dir / "qc.json", "w", encoding="utf-8") as f:
        json.dump(to_jsonable(qc), f, ensure_ascii=False, indent=2)

    # --- コンソール要約 ---
    ms = res["metrics_session"]
    zero = ms.loc[(ms["fixation_count"] == 0) & ms["aoi"].isin([a["name"] for a in aois]), "aoi"].tolist()
    print(f"区間数: {qc['data_quality']['n_segments']}  分析時間: {qc['data_quality']['analysis_time_sec']:.1f} s  "
          f"区間内有効率: {qc['data_quality']['valid_rate_in_segments']}")
    print(f"注視数: {qc['data_quality']['n_fixations']}  offscreen: {qc['data_quality']['offscreen']}")
    if zero:
        print(f"⚠ 注視0件のAOI: {zero}")
        print("  → 0件は「見ていない」とは限りません。qc.json の offscreen（どの辺の外か）と、")
        print("    キャリブレーション検証（calib_*.json）の画面端の精度を併せて確認してください。")
    print(f"完了: {out_dir}")


if __name__ == "__main__":
    main()
