# -*- coding: utf-8 -*-
"""
キャリブレーション検証の結果（calibrate_validate.py の calib_*.json）を並べて比較する（オフライン専用）。

画面設定（Eye Tracker Manager の寸法・角度）や較正点の位置（--calib-margin）を変えたときに、
どの条件で上下端のずれが小さくなるかを同じ物差しで比べるための診断ツール。研究データは書き換えない。

1ファイル1行で出す主な列
  display_width_mm / display_height_mm  トラッカーに設定されていた画面の寸法
  display_angle_deg                     トラッカー座標系（UCS）から見た画面の傾き（＝Spark と画面の相対角度の設定値）
  mean_accuracy_deg / hud_max_accuracy_deg / passed
  left_/right_mean_accuracy_deg, left_/right_min_valid_rate（どの点で最低かも出す）
  vertical_scale_pct      縦方向の中央寄り（%）。検証点のずれ(y) を「目標の位置 − 0.5」に一次回帰した傾きの符号反転。
                          正 = 端の点ほど中央寄りに測られている。0 が理想
  vertical_offset_deg     同じ回帰の切片（画面中央での縦の平行ずれ。正 = 下向き）
  horizontal_scale_pct / horizontal_offset_deg   横方向の同じ量
  top_bar_bias_y_deg      上部HUD（AOI中心の検証点で target_y < 0.2）の縦のずれの平均。正 = 下向き（中央寄り）
  bottom_bar_bias_y_deg   下部HUD（target_y > 0.8）の縦のずれの平均。負 = 上向き（中央寄り）
ずれは左右眼の bias_*_norm の平均（片眼のみなら片眼）。度への換算は atan(ずれ[mm] / 眼−トラッカー距離)。

使い方（PowerShell。ワイルドカードはスクリプト側で展開する）
------------------------------------------------------------
python .\\src\\analysis\\calib_compare.py ".\\data\\raw\\calib_TEST_20261013_*.json"
python .\\src\\analysis\\calib_compare.py .\\data\\raw --group-by display_angle_deg
"""

from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = _PROJECT_ROOT / "data" / "processed" / "calib_compare" / "calib_compare.csv"
DEFAULT_DISTANCE_MM = 650.0
TOP_BAR_MAX_Y = 0.2
BOTTOM_BAR_MIN_Y = 0.8

PRINT_COLUMNS = [
    "file", "calib_margin", "display_angle_deg", "display_width_mm", "mean_accuracy_deg",
    "hud_max_accuracy_deg", "passed", "vertical_scale_pct", "horizontal_scale_pct",
    "top_bar_bias_y_deg", "bottom_bar_bias_y_deg", "left_mean_accuracy_deg", "right_mean_accuracy_deg",
    "n_retried", "head_z_mm",
]
GROUP_COLUMNS = [
    "mean_accuracy_deg", "hud_max_accuracy_deg", "vertical_scale_pct", "horizontal_scale_pct",
    "top_bar_bias_y_deg", "bottom_bar_bias_y_deg", "left_mean_accuracy_deg", "right_mean_accuracy_deg",
]


def expand_inputs(items: list[str]) -> list[Path]:
    """パス・ワイルドカード・ディレクトリ（中の calib_*.json）を展開する。重複は除き、名前順に並べる。"""
    out = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            out.extend(sorted(p.glob("calib_*.json")))
        elif any(ch in item for ch in "*?["):
            out.extend(Path(x) for x in sorted(glob.glob(item)))
        else:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return sorted(uniq, key=lambda x: x.name)


def display_geometry(calib: dict) -> dict:
    """UCS 上の画面の寸法と傾き。傾きは UCS の y 軸と画面の縦辺の角度（上辺がユーザー側に来る向きを正）。
    Spark は画面の下で上向きに取り付けるので、この角度が Eye Tracker Manager に設定された相対角度になる。"""
    da = calib.get("display_area_ucs_mm") or {}
    tl, bl = da.get("top_left"), da.get("bottom_left")
    angle = None
    if tl and bl:
        dy, dz = tl[1] - bl[1], tl[2] - bl[2]
        angle = math.degrees(math.atan2(dz, dy))
    return {"display_width_mm": da.get("width_mm"), "display_height_mm": da.get("height_mm"),
            "display_angle_deg": angle}


def eye_mean(point: dict, key: str) -> float:
    """左右眼の値の平均（片眼のみ有効ならその眼）。どちらも無ければ NaN。"""
    vals = []
    for eye in ("left", "right"):
        v = (point.get(eye) or {}).get(key)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(float(v))
    return float(np.mean(vals)) if vals else float("nan")


def fit_scale(targets: np.ndarray, biases: np.ndarray) -> "tuple[float, float]":
    """bias = a + s·(target − 0.5) を最小二乗で当てはめ、(中央寄り% = −s·100, 切片 a) を返す。"""
    ok = ~np.isnan(biases)
    t, b = targets[ok] - 0.5, biases[ok]
    if len(t) < 3 or np.ptp(t) == 0:
        return float("nan"), float("nan")
    s, a = np.polyfit(t, b, 1)
    return float(-s * 100.0), float(a)


def norm_to_deg(bias_norm: float, size_mm: "float | None", distance_mm: float) -> float:
    if size_mm is None or math.isnan(bias_norm):
        return float("nan")
    return math.degrees(math.atan(bias_norm * size_mm / distance_mm))


def summarize_calib(calib: dict, name: str = "") -> dict:
    """calib JSON 1つを1行の要約にする。"""
    geo = display_geometry(calib)
    head = ((calib.get("head_position") or {}).get("recorded") or {})
    dist = head.get("z_mid_mm") or DEFAULT_DISTANCE_MM
    pts = calib.get("validation_points", [])
    summ = calib.get("summary") or {}
    params = calib.get("parameters") or {}

    tx = np.array([p["target_x"] for p in pts], dtype=float)
    ty = np.array([p["target_y"] for p in pts], dtype=float)
    bx = np.array([eye_mean(p, "bias_x_norm") for p in pts], dtype=float)
    by = np.array([eye_mean(p, "bias_y_norm") for p in pts], dtype=float)
    v_scale, v_off = fit_scale(ty, by)
    h_scale, h_off = fit_scale(tx, bx)

    hud = [p for p in pts if p.get("kind") == "aoi" and p.get("accuracy_deg") is not None]
    worst = max(hud, key=lambda p: p["accuracy_deg"]) if hud else None
    top = [eye_mean(p, "bias_y_norm") for p in pts if p.get("kind") == "aoi" and p["target_y"] < TOP_BAR_MAX_Y]
    bottom = [eye_mean(p, "bias_y_norm") for p in pts if p.get("kind") == "aoi" and p["target_y"] > BOTTOM_BAR_MIN_Y]
    h_mm = geo["display_height_mm"]
    w_mm = geo["display_width_mm"]

    row = {
        "file": name,
        "created_at_utc": calib.get("created_at_utc"),
        "subject_id": calib.get("subject_id"),
        "script_version": (calib.get("script") or {}).get("version"),
        "calib_margin": params.get("calib_margin", 0.1),
        **geo,
        "head_x_mm": head.get("x_mid_mm"),
        "head_y_mm": head.get("y_mid_mm"),
        "head_z_mm": head.get("z_mid_mm"),
        "passed": summ.get("passed"),
        "mean_accuracy_deg": summ.get("mean_accuracy_deg"),
        "mean_precision_rms_s2s_deg": summ.get("mean_precision_rms_s2s_deg"),
        "hud_max_accuracy_deg": worst["accuracy_deg"] if worst else None,
        "hud_worst_point": worst["label"] if worst else None,
        "n_retried": len(summ.get("retried_points", [])),
        "vertical_scale_pct": v_scale,
        "vertical_offset_deg": norm_to_deg(v_off, h_mm, dist),
        "horizontal_scale_pct": h_scale,
        "horizontal_offset_deg": norm_to_deg(h_off, w_mm, dist),
        "top_bar_bias_y_deg": norm_to_deg(float(np.nanmean(top)), h_mm, dist) if top else float("nan"),
        "bottom_bar_bias_y_deg": norm_to_deg(float(np.nanmean(bottom)), h_mm, dist) if bottom else float("nan"),
    }
    for eye in ("left", "right"):
        accs = [(p.get(eye) or {}).get("accuracy_deg") for p in pts]
        accs = [a for a in accs if a is not None]
        row[f"{eye}_mean_accuracy_deg"] = float(np.mean(accs)) if accs else float("nan")
        rates = [((p.get(eye) or {}).get("valid_rate"), p["label"]) for p in pts]
        rates = [r for r in rates if r[0] is not None]
        if rates:
            low = min(rates, key=lambda r: r[0])
            row[f"{eye}_min_valid_rate"] = low[0]
            row[f"{eye}_min_valid_point"] = low[1]
        else:
            row[f"{eye}_min_valid_rate"] = float("nan")
            row[f"{eye}_min_valid_point"] = None
    return row


def compare(paths: list[Path]) -> pd.DataFrame:
    rows = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            rows.append(summarize_calib(json.load(f), p.name))
    return pd.DataFrame(rows)


def group_summary(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """条件ごとの平均と回数。角度は 0.1° 単位に丸めてまとめる。"""
    key = df[by].round(1) if by == "display_angle_deg" else df[by]
    g = df.assign(_key=key).groupby("_key", dropna=False)
    out = g[GROUP_COLUMNS].mean()
    out.insert(0, "n_runs", g.size())
    out.index.name = by
    return out.reset_index()


def main(argv=None):
    p = argparse.ArgumentParser(description="calib_*.json を並べて、精度と上下端のずれを比較します。")
    p.add_argument("inputs", nargs="+", help="calib JSON のパス、ワイルドカード、またはディレクトリ")
    p.add_argument("--group-by", default="calib_margin",
                   choices=["calib_margin", "display_angle_deg", "subject_id", "none"],
                   help="条件ごとの平均を出す列（既定: calib_margin）")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="1ファイル1行の CSV の保存先")
    args = p.parse_args(argv)

    paths = expand_inputs(args.inputs)
    missing = [str(x) for x in paths if not x.exists()]
    if missing:
        raise SystemExit(f"エラー: ファイルがありません: {missing}")
    if not paths:
        raise SystemExit("エラー: calib JSON が見つかりません。")
    df = compare(paths)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 50)
    print(df[PRINT_COLUMNS].round(2).to_string(index=False))
    print("\n縦/横の中央寄り% は 0 が理想（正 = 端ほど中央寄り）。top/bottom_bar は正 = 下向き、負 = 上向き。")
    if df["display_width_mm"].round(1).nunique() > 1 or df["display_angle_deg"].round(1).nunique() > 1:
        print("⚠ 画面設定（寸法・角度）の違う回が混ざっています。条件の比較では揃えてください。")
    if args.group_by != "none":
        print(f"\n--- {args.group_by} ごとの平均 ---")
        print(group_summary(df, args.group_by).round(2).to_string(index=False))
    print(f"\n保存: {args.out}")


if __name__ == "__main__":
    main()
