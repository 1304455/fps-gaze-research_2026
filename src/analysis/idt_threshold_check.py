# -*- coding: utf-8 -*-
"""
I-DT の閾値（最短 100 ms・分散 1°）が自分のデータで妥当かを、予備実験（PL01〜PL03）で確かめる。

手順と見直しの目安は docs/LAB_CHECKLIST.md 4 に、結果を見る前（2026-11-10）に記録したもの。
このスクリプトはその手順 A〜C をそのまま計算し、目安に当たるかを判定する（目安の数値は定数で、
コマンドの引数では変えられない。結果を見てから基準を動かせないようにするため）。

  A 注視時間の分布   ：参加者ごとの注視時間の中央値・四分位と、最短付近（6〜7サンプル＝100〜117 ms）の割合
                       → 最短付近が 30% を超える参加者がいれば目安に該当
  B 分散とノイズ     ：較正の検証の両眼平均の精密度（precision、RMS-S2S）から、ノイズだけによる最短の窓の分散を見積もる
                       → 見積もりが分散の閾値（1°）の半分を超えれば目安に該当
  C 閾値の感度       ：分散 0.5 / 0.75 / 1.0 / 1.5 / 2.0° × 最短 60 / 100 / 150 ms で解析し直す
                       → 1.0° から 0.75° または 1.5° に変えると（最短 100 ms のまま）、1分あたりの注視数か
                         平均注視時間が 25% を超えて変わる参加者がいれば目安に該当
  D 目視（任意）     ：このスクリプトの対象外（gaze_visualizer_v3.py の scanpath で行う）

どれにも当たらなければ「100 ms・1° を変えない」。当たっても、その場で閾値を変えず、選択肢を出して決め直す。

B の見積もりの仮定：ノイズが軸ごとに独立な正規分布（SD σ）で、サンプル間に相関が無いとする。
  2次元の RMS-S2S は 2σ（各軸の差の分散が 2σ² で、2軸の和が 4σ²）。n サンプルの範囲の期待値は d2(n)·σ（管理図の係数）。
  分散 D は x の範囲 ＋ y の範囲なので、E[D] = 2·d2(n)·σ = d2(n)·RMS-S2S。n は最短の窓のサンプル数（60 Hz・100 ms で 6）。
  実際のノイズにはサンプル間の相関があり、RMS-S2S は低い周波数のゆれを小さく見積もるので、この値は目安である。

入力は ver3 の主解析の結果フォルダ（data/processed/aoi_result/<session_base>/）。qc.json に記録された入力ファイル
（視線CSV、AOI JSON、区間ファイル、同期オフセット、パラメータ）をそのまま使い、閾値だけを変えて解析し直す。
入力ファイルの sha256 が qc.json と違えば止める（主解析と別のデータで確かめないように）。
既定では参加者 ID が PL で始まるセッション（予備実験）だけを受け付ける（本実験のデータは使わない取り決め）。

使い方（PowerShell）
--------------------
python .\\src\\analysis\\idt_threshold_check.py --results `
    .\\data\\processed\\aoi_result\\<PL01の session_base> `
    .\\data\\processed\\aoi_result\\<PL02の session_base> `
    .\\data\\processed\\aoi_result\\<PL03の session_base>

出力：data/processed/idt_check/（--out-dir で変更）
  summary.json                 判定・目安の数値・入力の sha256・バージョン
  a_duration_by_participant.csv, a_duration_histogram.csv
  b_noise_by_participant.csv
  c_grid_by_participant.csv, c_grid_by_session.csv, c_sensitivity.csv
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

_HERE = Path(__file__).resolve().parent
_AOI_DIR = _HERE.parent / "aoi_detector"
for _p in (str(_AOI_DIR), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gaze_metrics as gm  # noqa: E402
from aoi_geometry import get_display, load_aoi_config  # noqa: E402

SCRIPT_VERSION = "1.0.0"
_PROJECT_ROOT = _HERE.parent.parent
DEFAULT_OUT_DIR = _PROJECT_ROOT / "data" / "processed" / "idt_check"

# --- 見直しの目安（docs/LAB_CHECKLIST.md 4、2026-11-10 に結果を見る前に記録。引数では変えない） ---
NEAR_MIN_SHARE_MAX = 0.30      # A：最短付近の注視の割合の上限
NOISE_SHARE_OF_THRESHOLD_MAX = 0.5  # B：ノイズの分散の見積もり ÷ 分散の閾値 の上限
CHANGE_MAX = 0.25              # C：1.0° → 0.75° / 1.5° での相対変化の上限
CRITERIA_RECORDED_AT = "docs/LAB_CHECKLIST.md 4（2026-11-10、結果を見る前に記録）"

# --- 主解析の閾値と、振る値（LAB_CHECKLIST 4 の C） ---
BASELINE_DISPERSION_DEG = 1.0
BASELINE_MIN_DURATION_MS = 100.0
GRID_DISPERSIONS_DEG = (0.5, 0.75, 1.0, 1.5, 2.0)
GRID_MIN_DURATIONS_MS = (60.0, 100.0, 150.0)
SENSITIVITY_DISPERSIONS_DEG = (0.75, 1.5)

# 主要比較の6つ（ANALYSIS_PLAN §3）。下部HUD（統合）は3つの AOI の合計
LOWER_HUD = ("health_armor", "abilities", "ammo_weapon")
LOWER_HUD_LABEL = "lower_hud_combined"
PRIMARY_AOIS = ("minimap", "crosshair", LOWER_HUD_LABEL, "ally_team_status", "round_timer", "enemy_team_status")

# n サンプルの範囲の期待値の係数 d2(n)（正規分布、管理図の係数表）
D2 = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078,
      11: 3.173, 12: 3.258}


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_commit() -> "str | None":
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT, capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _resolve(path_str: "str | None") -> "Path | None":
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def is_pilot(participant_id: "str | None") -> bool:
    """予備実験の参加者か（ID が PL で始まる。本実験は S01 など）。"""
    return bool(participant_id) and str(participant_id).strip().upper().startswith("PL")


def load_session(result_dir: Path, allow_non_pilot: bool = False) -> dict:
    """
    ver3 の結果フォルダの qc.json から、主解析と同じ入力を読み直す。
    戻り値：{"session_base", "participant_id", "df", "aois", "display", "params", "segments",
             "manifest_distance_mm", "calibration_file", "inputs"}
    """
    qc_path = result_dir / "qc.json"
    if not qc_path.exists():
        raise ValueError(f"{result_dir} に qc.json がありません（ver3 の結果フォルダを指定してください）。")
    with open(qc_path, "r", encoding="utf-8") as f:
        qc = json.load(f)
    session_base = qc.get("session_base") or result_dir.name
    manifest_row = qc.get("manifest_row") or {}
    participant_id = manifest_row.get("participant_id") or None
    if not allow_non_pilot and not is_pilot(participant_id):
        raise ValueError(
            f"{session_base}：参加者 ID が {participant_id!r} です。I-DT の閾値の確認は予備実験（PL01〜PL03）だけで行う"
            "取り決めです（LAB_CHECKLIST 4）。ver3 を --manifest 付きで実行した結果か確認してください。")
    inputs = qc.get("inputs") or {}
    seg_info = inputs.get("segments_file")
    if not seg_info:
        raise ValueError(f"{session_base}：区間ファイルなしの結果です。生存区間で確かめるので、区間付きの結果を指定してください。")
    offset = (qc.get("sync") or {}).get("offset_sec")
    if offset is None:
        raise ValueError(f"{session_base}：qc.json に同期オフセットがありません。")

    checked = {}
    for key, info in (("gaze_csv", inputs.get("gaze_csv")), ("aoi_config", inputs.get("aoi_config")),
                      ("segments_file", seg_info)):
        if not info or not info.get("path"):
            raise ValueError(f"{session_base}：qc.json の inputs.{key} がありません。")
        p = Path(info["path"])
        if not p.exists():
            raise ValueError(f"{session_base}：{key} のファイルが見つかりません：{p}")
        digest = sha256_of(p)
        if info.get("sha256") and digest != info["sha256"]:
            raise ValueError(f"{session_base}：{key}（{p.name}）の中身が主解析のときと違います（sha256 が不一致）。"
                             "主解析をやり直してから確かめてください。")
        checked[key] = {"path": str(p), "sha256": digest}

    params = gm.merge_params(qc.get("params"))
    aois, raw_aoi = load_aoi_config(checked["aoi_config"]["path"])
    display = get_display(raw_aoi)
    if display is None:
        raise ValueError(f"{session_base}：AOI JSON に display がありません。")
    seg_raw = gm.read_csv_text(checked["segments_file"]["path"])
    segments = gm.segments_from_video_time(seg_raw, float(offset), params["segment_types"])
    manifest_distance_mm = None
    if params["eye_screen_distance_source"] == "manifest":
        val = manifest_row.get("eye_screen_distance_cm")
        if not val:
            raise ValueError(f"{session_base}：eye_screen_distance_source=manifest ですが、台帳の距離がありません。")
        manifest_distance_mm = float(val) * 10.0
    df = pd.read_csv(checked["gaze_csv"]["path"])
    calib = _resolve(manifest_row.get("calibration_file"))
    return {
        "session_base": session_base,
        "participant_id": participant_id or session_base,
        "df": df, "aois": aois, "display": display, "params": params, "segments": segments,
        "manifest_distance_mm": manifest_distance_mm,
        "calibration_file": calib,
        "inputs": {**checked, "qc_json": str(qc_path), "calibration_file": str(calib) if calib else None},
    }


# ---------------------------------------------------------------------------
# 1回の解析から数を取り出す（参加者単位で足せるように、割合ではなく数で持つ）
# ---------------------------------------------------------------------------

def counts_from_result(res: dict, params: dict) -> dict:
    """analyze_session の結果から、C の指標の分子・分母を数で取り出す。"""
    s = res["samples"]
    in_seg = s["segment_id"].notna().to_numpy()
    valid = s["valid"].to_numpy(dtype=bool)
    offscreen = s["offscreen"].to_numpy(dtype=bool)
    use = in_seg & valid
    if not params["offscreen_in_denominator"]:
        use = use & ~offscreen
    denom = int(use.sum())
    in_fix = s["fixation_id"].notna().to_numpy()
    fix = res["fixations"]
    valid_time = float(res["metrics_session"]["valid_time_sec"].iloc[0]) if len(res["metrics_session"]) else 0.0
    is_aoi_fix = (s["sample_class"].to_numpy() == "aoi_fixation") & use
    sample_aoi = s["sample_aoi"].to_numpy()
    aoi_fix_counts = {}
    for name in PRIMARY_AOIS:
        names = LOWER_HUD if name == LOWER_HUD_LABEL else (name,)
        aoi_fix_counts[name] = int((is_aoi_fix & np.isin(sample_aoi, names)).sum())
    return {
        "n_fixations": int(len(fix)),
        "total_fixation_duration_sec": float(fix["duration_sec"].sum()) if len(fix) else 0.0,
        "valid_time_sec": valid_time,
        "denominator_samples": denom,
        "n_valid_in_fixation": int((use & in_fix).sum()),
        **{f"n_aoi_fixation__{k}": v for k, v in aoi_fix_counts.items()},
    }


def rates_from_counts(c: "dict | pd.Series") -> dict:
    """数から C の指標（1分あたりの注視数、平均注視時間、注視に入った割合、AOI への注視の割合）を出す。"""
    n_fix = c["n_fixations"]
    vt = c["valid_time_sec"]
    denom = c["denominator_samples"]
    out = {
        "fixations_per_min": n_fix / (vt / 60.0) if vt > 0 else np.nan,
        "mean_fixation_duration_ms": 1000.0 * c["total_fixation_duration_sec"] / n_fix if n_fix else np.nan,
        "fixation_sample_share": c["n_valid_in_fixation"] / denom if denom else np.nan,
    }
    for name in PRIMARY_AOIS:
        out[f"aoi_fixation_pct__{name}"] = 100.0 * c[f"n_aoi_fixation__{name}"] / denom if denom else np.nan
    return out


def run_grid(sess: dict, dispersions=GRID_DISPERSIONS_DEG, durations=GRID_MIN_DURATIONS_MS) -> tuple:
    """
    閾値の組み合わせごとに解析し直す。戻り値：(行のリスト, 主解析の閾値での注視の表, サンプル間隔)
    主解析の閾値（1.0°・100 ms）は必ず含める（A・B に使う）。
    """
    rows = []
    base_fix = None
    sample_dt = None
    combos = [(d, m) for m in durations for d in dispersions]
    if (BASELINE_DISPERSION_DEG, BASELINE_MIN_DURATION_MS) not in combos:
        combos.append((BASELINE_DISPERSION_DEG, BASELINE_MIN_DURATION_MS))
    for disp, min_ms in combos:
        p = dict(sess["params"], idt_max_dispersion_deg=float(disp), idt_min_duration_ms=float(min_ms))
        res = gm.analyze_session(sess["df"], sess["aois"], sess["display"], p, sess["segments"],
                                 sess["manifest_distance_mm"])
        rows.append({"session_base": sess["session_base"], "participant_id": sess["participant_id"],
                     "dispersion_deg": float(disp), "min_duration_ms": float(min_ms),
                     "eye_distance_mm": float(res["eye_distance_used"]["mm"]),
                     **counts_from_result(res, p)})
        if disp == BASELINE_DISPERSION_DEG and min_ms == BASELINE_MIN_DURATION_MS:
            base_fix = res["fixations"].assign(session_base=sess["session_base"],
                                               participant_id=sess["participant_id"])
            sample_dt = float(res["sample_dt"])
    return rows, base_fix, sample_dt


# ---------------------------------------------------------------------------
# A：注視時間の分布
# ---------------------------------------------------------------------------

def near_min_limit_sec(min_duration_ms: float, sample_dt: float) -> float:
    """最短付近の上限。注視時間 = 最後 − 最初 + 1サンプル間隔 なので、6サンプル＝100 ms、7サンプル≒117 ms。
    7サンプルまでを含め、8サンプル（≒133 ms）を含めないように、最短 + 1.5 サンプル間隔 とする。"""
    return min_duration_ms / 1000.0 + 1.5 * sample_dt


def duration_summary(fix: pd.DataFrame, sample_dt: float, min_duration_ms: float = BASELINE_MIN_DURATION_MS) -> dict:
    d = fix["duration_sec"].to_numpy(dtype=float)
    n = len(d)
    if n == 0:
        return {"n_fixations": 0, "median_ms": np.nan, "q1_ms": np.nan, "q3_ms": np.nan,
                "near_min_share": np.nan, "near_min_limit_ms": 1000.0 * near_min_limit_sec(min_duration_ms, sample_dt)}
    limit = near_min_limit_sec(min_duration_ms, sample_dt)
    return {
        "n_fixations": int(n),
        "median_ms": 1000.0 * float(np.median(d)),
        "q1_ms": 1000.0 * float(np.percentile(d, 25)),
        "q3_ms": 1000.0 * float(np.percentile(d, 75)),
        "near_min_share": float(np.mean(d <= limit + 1e-9)),
        "near_min_limit_ms": 1000.0 * limit,
    }


def duration_histogram(fix: pd.DataFrame, bin_ms: float = 20.0, max_ms: float = 1000.0) -> pd.DataFrame:
    """参加者ごとの注視時間のヒストグラム（max_ms 以上は最後の階級にまとめる）。0件の階級も行を出す。"""
    starts = np.arange(0.0, max_ms, bin_ms)               # 通常の階級 [lo, lo + bin_ms)
    bins = np.append(np.append(starts, max_ms), np.inf)   # 最後に [max_ms, ∞) の階級を足す
    rows = []
    for pid, g in fix.groupby("participant_id", sort=True):
        ms = 1000.0 * g["duration_sec"].to_numpy(dtype=float)
        counts, _ = np.histogram(ms, bins=bins)
        for lo, c in zip(np.append(starts, max_ms), counts):
            label = f"{lo:.0f}-{lo + bin_ms:.0f}" if lo < max_ms else f"{lo:.0f}以上"
            rows.append({"participant_id": pid, "bin_ms": label, "bin_start_ms": lo, "count": int(c)})
    return pd.DataFrame(rows, columns=["participant_id", "bin_ms", "bin_start_ms", "count"])


# ---------------------------------------------------------------------------
# B：分散とノイズ
# ---------------------------------------------------------------------------

def window_samples(min_duration_ms: float, sample_dt: float) -> int:
    """最短の窓のサンプル数（注視時間 = (n − 1)·dt + dt = n·dt なので n = 最短 ÷ dt）。"""
    return max(2, int(round(min_duration_ms / 1000.0 / sample_dt)))


def noise_dispersion_estimate(precision_rms_s2s_deg: float, n_samples: int) -> float:
    """ノイズだけによる n サンプルの窓の分散 D の期待値 ＝ d2(n)·RMS-S2S（仮定はモジュールの docstring）。"""
    if n_samples not in D2:
        raise ValueError(f"d2 の表に n={n_samples} がありません（{min(D2)}〜{max(D2)}）。")
    return D2[n_samples] * float(precision_rms_s2s_deg)


def calib_binocular_precision(calib_path: "Path | None") -> dict:
    """calib JSON の検証点（練習用の点は含まない）の、両眼平均の視線の精密度（RMS-S2S）の中央値。"""
    if calib_path is None:
        return {"available": False, "reason": "台帳に calibration_file がありません"}
    if not Path(calib_path).exists():
        return {"available": False, "reason": f"較正ファイルが見つかりません：{calib_path}"}
    with open(calib_path, "r", encoding="utf-8") as f:
        calib = json.load(f)
    vals = []
    for p in calib.get("validation_points", []):
        v = (p.get("binocular_avg") or {}).get("precision_rms_s2s_deg")
        if v is not None and np.isfinite(v):
            vals.append(float(v))
    if not vals:
        return {"available": False, "reason": "検証点に両眼平均の精密度がありません（calibrate_validate 1.4.0 より前の JSON）"}
    return {"available": True, "precision_rms_s2s_deg_median": float(np.median(vals)),
            "precision_rms_s2s_deg_max": float(np.max(vals)), "n_points": len(vals), "file": str(calib_path)}


# ---------------------------------------------------------------------------
# C：閾値の感度
# ---------------------------------------------------------------------------

COUNT_COLUMNS = ["n_fixations", "total_fixation_duration_sec", "valid_time_sec", "denominator_samples",
                 "n_valid_in_fixation"] + [f"n_aoi_fixation__{k}" for k in PRIMARY_AOIS]


def grid_by_participant(grid_sessions: pd.DataFrame) -> pd.DataFrame:
    """セッションの数を参加者単位で足してから割合を出す（割合の平均をとらない）。"""
    g = grid_sessions.groupby(["participant_id", "dispersion_deg", "min_duration_ms"], sort=True)[COUNT_COLUMNS].sum()
    g = g.reset_index()
    rates = pd.DataFrame([rates_from_counts(r) for _, r in g.iterrows()], index=g.index)
    return pd.concat([g, rates], axis=1)


def add_rates(grid_sessions: pd.DataFrame) -> pd.DataFrame:
    rates = pd.DataFrame([rates_from_counts(r) for _, r in grid_sessions.iterrows()], index=grid_sessions.index)
    return pd.concat([grid_sessions, rates], axis=1)


def sensitivity_table(grid_p: pd.DataFrame) -> pd.DataFrame:
    """主解析の閾値（1.0°・100 ms）に対する、0.75° と 1.5°（100 ms）の相対変化。"""
    rows = []
    for pid, g in grid_p.groupby("participant_id", sort=True):
        base = g[(g["dispersion_deg"] == BASELINE_DISPERSION_DEG) & (g["min_duration_ms"] == BASELINE_MIN_DURATION_MS)]
        if base.empty:
            continue
        b = base.iloc[0]
        for disp in SENSITIVITY_DISPERSIONS_DEG:
            alt = g[(g["dispersion_deg"] == disp) & (g["min_duration_ms"] == BASELINE_MIN_DURATION_MS)]
            if alt.empty:
                continue
            a = alt.iloc[0]
            row = {"participant_id": pid, "dispersion_deg": disp}
            for m in ("fixations_per_min", "mean_fixation_duration_ms"):
                bv, av = b[m], a[m]
                rel = (av - bv) / bv if (np.isfinite(bv) and bv != 0 and np.isfinite(av)) else np.nan
                row[f"{m}_baseline"] = bv
                row[f"{m}_alt"] = av
                row[f"{m}_rel_change"] = rel
                row[f"{m}_exceeds"] = bool(np.isfinite(rel) and abs(rel) > CHANGE_MAX)
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def judge(a_df: pd.DataFrame, b_df: pd.DataFrame, sens: pd.DataFrame) -> dict:
    """目安に当たるかを判定する。判定できなかった項目は「判定不能」として明示する（該当なしと混ぜない）。"""
    a_hit = a_df.loc[a_df["near_min_share"] > NEAR_MIN_SHARE_MAX, "participant_id"].tolist()
    a_ok = bool(a_df["near_min_share"].notna().any())
    b_avail = b_df[b_df["available"]] if len(b_df) else b_df
    b_hit = (b_avail.loc[b_avail["noise_share_of_threshold"] > NOISE_SHARE_OF_THRESHOLD_MAX, "participant_id"].tolist()
             if len(b_avail) else [])
    b_missing = b_df.loc[~b_df["available"], "participant_id"].tolist() if len(b_df) else []
    c_hit = []
    if len(sens):
        hit = sens[sens["fixations_per_min_exceeds"] | sens["mean_fixation_duration_ms_exceeds"]]
        c_hit = sorted(set(hit["participant_id"]))
    flags = {
        "A": {"hit": bool(a_hit), "participants": a_hit, "judged": a_ok,
              "rule": f"最短付近（6〜7サンプル）の注視の割合 > {NEAR_MIN_SHARE_MAX:.0%} の参加者がいる"},
        "B": {"hit": bool(b_hit), "participants": b_hit, "judged": len(b_avail) > 0,
              "not_judged_participants": b_missing,
              "rule": f"ノイズの分散の見積もり > 分散の閾値の {NOISE_SHARE_OF_THRESHOLD_MAX:.0%}"},
        "C": {"hit": bool(c_hit), "participants": c_hit, "judged": len(sens) > 0,
              "rule": f"1.0° → 0.75° / 1.5°（100 ms）で、1分あたりの注視数か平均注視時間の変化 > {CHANGE_MAX:.0%}"},
    }
    any_hit = any(f["hit"] for f in flags.values())
    all_judged = all(f["judged"] for f in flags.values()) and not b_missing
    if any_hit:
        verdict = "見直しの目安に該当する項目がある。閾値はその場で変えず、選択肢を出して決め直す（本実験の集計前に記録を残す）"
    elif all_judged:
        verdict = "見直しの目安に該当なし。最短 100 ms・分散 1° を変えない"
    else:
        verdict = "判定できなかった項目がある（該当なしとは言えない）。不足を補ってからやり直す"
    return {"flags": flags, "any_hit": any_hit, "all_judged": all_judged, "verdict": verdict}


# ---------------------------------------------------------------------------
# 全体
# ---------------------------------------------------------------------------

def run_check(sessions: list[dict], dispersions=GRID_DISPERSIONS_DEG, durations=GRID_MIN_DURATIONS_MS) -> dict:
    grid_rows, fixes, dts, calib_files = [], [], {}, {}
    for sess in sessions:
        rows, base_fix, dt = run_grid(sess, dispersions, durations)
        grid_rows += rows
        fixes.append(base_fix)
        dts.setdefault(sess["participant_id"], []).append(dt)
        calib_files.setdefault(sess["participant_id"], []).append(sess["calibration_file"])
    grid_s = add_rates(pd.DataFrame(grid_rows))
    grid_p = grid_by_participant(grid_s)
    fix_all = pd.concat(fixes, ignore_index=True)

    # A
    a_rows = []
    for pid in sorted(dts):
        dt = float(np.median(dts[pid]))
        f = fix_all[fix_all["participant_id"] == pid]
        a_rows.append({"participant_id": pid, "sample_dt_ms": 1000.0 * dt, **duration_summary(f, dt)})
    a_df = pd.DataFrame(a_rows)
    hist = duration_histogram(fix_all)

    # B（参加者に複数の較正があれば、悪い方＝精密度の大きい方で判定する）
    b_rows = []
    for pid in sorted(dts):
        dt = float(np.median(dts[pid]))
        n = window_samples(BASELINE_MIN_DURATION_MS, dt)
        f = fix_all[fix_all["participant_id"] == pid]
        infos = [calib_binocular_precision(p) for p in calib_files[pid]]
        avail = [i for i in infos if i["available"]]
        row = {"participant_id": pid, "window_samples": n, "d2": D2.get(n, np.nan),
               "dispersion_threshold_deg": BASELINE_DISPERSION_DEG,
               "fixation_dispersion_median_deg": float(f["dispersion_deg"].median()) if len(f) else np.nan,
               "fixation_dispersion_share_ge_90pct": (float(np.mean(f["dispersion_deg"] >= 0.9 * BASELINE_DISPERSION_DEG))
                                                      if len(f) else np.nan)}
        if avail:
            worst = max(avail, key=lambda i: i["precision_rms_s2s_deg_median"])
            est = noise_dispersion_estimate(worst["precision_rms_s2s_deg_median"], n)
            row.update(available=True, precision_rms_s2s_deg_median=worst["precision_rms_s2s_deg_median"],
                       n_calib_points=worst["n_points"], calibration_file=worst["file"],
                       noise_dispersion_estimate_deg=est, noise_share_of_threshold=est / BASELINE_DISPERSION_DEG,
                       reason="")
        else:
            row.update(available=False, precision_rms_s2s_deg_median=np.nan, n_calib_points=0, calibration_file="",
                       noise_dispersion_estimate_deg=np.nan, noise_share_of_threshold=np.nan,
                       reason="; ".join(i["reason"] for i in infos))
        b_rows.append(row)
    b_df = pd.DataFrame(b_rows)

    sens = sensitivity_table(grid_p)
    return {"grid_sessions": grid_s, "grid_participants": grid_p, "a": a_df, "histogram": hist, "b": b_df,
            "sensitivity": sens, "judgement": judge(a_df, b_df, sens)}


def to_jsonable(o):
    if isinstance(o, dict):
        return {k: to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def format_report(result: dict) -> str:
    j = result["judgement"]
    lines = [f"I-DT の閾値の確認（idt_threshold_check {SCRIPT_VERSION}）。目安は {CRITERIA_RECORDED_AT}", ""]
    lines.append("A 注視時間の分布（主解析の閾値 1.0°・100 ms）")
    for _, r in result["a"].iterrows():
        lines.append(f"  {r['participant_id']}: 注視 {r['n_fixations']} 件、中央値 {r['median_ms']:.0f} ms"
                     f"（四分位 {r['q1_ms']:.0f}〜{r['q3_ms']:.0f}）、最短付近（≤{r['near_min_limit_ms']:.0f} ms）"
                     f" {r['near_min_share']:.1%}")
    lines.append("B 分散とノイズ")
    for _, r in result["b"].iterrows():
        if r["available"]:
            lines.append(f"  {r['participant_id']}: 精密度（RMS-S2S、中央値）{r['precision_rms_s2s_deg_median']:.3f}° → "
                         f"{int(r['window_samples'])} サンプルの窓のノイズの分散 ≈ {r['noise_dispersion_estimate_deg']:.2f}°"
                         f"（閾値の {r['noise_share_of_threshold']:.0%}）。注視の分散の中央値 {r['fixation_dispersion_median_deg']:.2f}°")
        else:
            lines.append(f"  {r['participant_id']}: 判定不能（{r['reason']}）")
    lines.append("C 閾値の感度（100 ms、1.0° に対する変化）")
    for _, r in result["sensitivity"].iterrows():
        lines.append(f"  {r['participant_id']} {r['dispersion_deg']}°: 注視数/分 {r['fixations_per_min_rel_change']:+.1%}、"
                     f"平均注視時間 {r['mean_fixation_duration_ms_rel_change']:+.1%}")
    lines.append("")
    for k, f in j["flags"].items():
        state = "該当" if f["hit"] else ("該当なし" if f["judged"] else "判定不能")
        who = f"（{', '.join(f['participants'])}）" if f["participants"] else ""
        lines.append(f"  {k}: {state}{who}  基準：{f['rule']}")
    lines += ["", f"判定：{j['verdict']}"]
    return "\n".join(lines)


def build_arg_parser():
    p = argparse.ArgumentParser(description="I-DT の閾値を予備実験のデータで確かめる（LAB_CHECKLIST 4 の手順 A〜C）。")
    p.add_argument("--results", nargs="+", required=True, help="ver3 の結果フォルダ（qc.json のあるフォルダ）")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="出力先（既定 data/processed/idt_check）")
    p.add_argument("--allow-non-pilot", action="store_true",
                   help="予備実験（PL〜）以外のセッションも受け付ける（テスト用。卒論の確認には使わない）")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    if out_dir.resolve().is_relative_to((_PROJECT_ROOT / "data" / "raw").resolve()):
        raise SystemExit("エラー: data/raw の下には出力しません。")
    sessions = []
    for d in args.results:
        try:
            sessions.append(load_session(Path(d), allow_non_pilot=args.allow_non_pilot))
        except ValueError as e:
            raise SystemExit(f"エラー: {e}")
        print(f"読み込み：{sessions[-1]['session_base']}（{sessions[-1]['participant_id']}）")
    print(f"閾値の組み合わせ {len(GRID_DISPERSIONS_DEG) * len(GRID_MIN_DURATIONS_MS)} 通りで解析し直します（1セッション数十秒が目安）")
    result = run_check(sessions)

    out_dir.mkdir(parents=True, exist_ok=True)

    def write(df: pd.DataFrame, name: str):
        df.to_csv(out_dir / name, index=False, encoding="utf-8-sig")

    write(result["a"], "a_duration_by_participant.csv")
    write(result["histogram"], "a_duration_histogram.csv")
    write(result["b"], "b_noise_by_participant.csv")
    write(result["grid_sessions"], "c_grid_by_session.csv")
    write(result["grid_participants"], "c_grid_by_participant.csv")
    write(result["sensitivity"], "c_sensitivity.csv")
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": {"name": Path(__file__).name, "version": SCRIPT_VERSION, "git_commit": git_commit()},
        "criteria": {"recorded_at": CRITERIA_RECORDED_AT, "near_min_share_max": NEAR_MIN_SHARE_MAX,
                     "noise_share_of_threshold_max": NOISE_SHARE_OF_THRESHOLD_MAX, "change_max": CHANGE_MAX},
        "baseline": {"dispersion_deg": BASELINE_DISPERSION_DEG, "min_duration_ms": BASELINE_MIN_DURATION_MS},
        "grid": {"dispersions_deg": list(GRID_DISPERSIONS_DEG), "min_durations_ms": list(GRID_MIN_DURATIONS_MS)},
        "noise_model": "E[D] = d2(n)·RMS-S2S（軸ごとに独立な正規ノイズ、サンプル間の相関なしを仮定。目安）",
        "near_min_definition": "注視時間 ≤ 最短 + 1.5 サンプル間隔（60 Hz で 6〜7 サンプル）",
        "sessions": [{"session_base": s["session_base"], "participant_id": s["participant_id"],
                      "inputs": s["inputs"], "params": s["params"]} for s in sessions],
        "judgement": result["judgement"],
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(to_jsonable(summary), f, ensure_ascii=False, indent=2)
    print(format_report(result))
    print(f"\n出力：{out_dir}")


if __name__ == "__main__":
    main()
