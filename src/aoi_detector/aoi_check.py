# -*- coding: utf-8 -*-
"""
AOI 設定の妥当性チェック（IMPROVEMENT_PLAN T5。オフライン専用）。

1. 各AOIの幅・高さを視角 [deg] で一覧にする（画面の物理寸法と眼−画面距離から計算）
2. キャリブレーション検証の結果（calib_*.json）があれば、AOIごとの測定精度と比べて
   「測れない大きさ」の AOI に警告を出す
     flag_below_min_size        ：短辺 < --min-size-deg（一般的な最小AOIサイズ 1〜1.5° の案）
     flag_half_below_accuracy   ：短辺の半分 < その位置の accuracy（中心を見ても外れうる）
3. OBS録画の1フレーム（src/test/extract_frames.py で抽出）に AOI を描画した画像を出す。
   ライブの PyQt オーバーレイの代わりに、試合映像上で AOI の位置ずれ（特に画面端）を確認する

使い方（PowerShell）
--------------------
python .\\src\\aoi_detector\\aoi_check.py --aoi .\\src\\aoi_detector\\valorant_hud_aoi_circular.json `
    --calib .\\data\\raw\\calib_P01_20261020_133000.json --frame .\\data\\processed\\frame_1000.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from aoi_geometry import aoi_extent_deg, find_overlapping_aois, get_display, load_aoi_config  # noqa: E402

_PROJECT_ROOT = _THIS_DIR.parent.parent
DEFAULT_OUT_DIR = _PROJECT_ROOT / "data" / "processed" / "aoi_check"
COLORS_BGR = [(0, 0, 255), (0, 160, 0), (255, 0, 0), (0, 165, 255), (160, 0, 160),
              (255, 255, 0), (0, 255, 255), (255, 0, 255), (128, 128, 255)]


def size_table(aois: list[dict], display, distance_mm: float, calib: "dict | None",
               min_size_deg: float) -> pd.DataFrame:
    """AOIごとの視角サイズ表。calib があれば精度との比較列を付ける。"""
    point_acc = {}
    mean_acc = None
    if calib:
        for p in calib.get("validation_points", []):
            if p.get("kind") == "aoi" and p.get("accuracy_deg") is not None:
                point_acc[p["aoi"]] = p["accuracy_deg"]
        mean_acc = (calib.get("summary") or {}).get("mean_accuracy_deg")
    rows = []
    for a in aois:
        ext = aoi_extent_deg(a, display, distance_mm)
        min_dim = min(ext["width_deg"], ext["height_deg"])
        acc = point_acc.get(a["name"], mean_acc)
        rows.append({
            "aoi": a["name"],
            "type": a["type"],
            "width_deg": ext["width_deg"],
            "height_deg": ext["height_deg"],
            "min_dim_deg": min_dim,
            "width_px": ext["width_px"],
            "height_px": ext["height_px"],
            "width_mm": ext["width_mm"],
            "height_mm": ext["height_mm"],
            "radius_source": a.get("radius_source", ""),
            "accuracy_deg": acc,
            "accuracy_source": ("calib: このAOIの中心" if a["name"] in point_acc
                                else ("calib: 全点平均" if acc is not None else "")),
            "flag_below_min_size": min_dim < min_size_deg,
            "flag_half_below_accuracy": (min_dim / 2 < acc) if acc is not None else None,
        })
    return pd.DataFrame(rows)


def draw_aois_on_frame(frame_path: Path, aois: list[dict], out_path: Path) -> None:
    """フレーム画像に AOI を描画する（画像の解像度に合わせて正規化座標を拡大）。"""
    import cv2

    img = cv2.imread(str(frame_path))
    if img is None:
        raise SystemExit(f"エラー: 画像を読めません: {frame_path}")
    h, w = img.shape[:2]
    if abs(w / h - 16 / 9) > 0.01:
        print(f"⚠ 警告: フレームのアスペクト比が 16:9 ではありません（{w}x{h}）。AOI の前提が崩れています。")
    overlay = img.copy()
    thick = max(2, w // 960)
    for i, a in enumerate(aois):
        c = COLORS_BGR[i % len(COLORS_BGR)]
        if a["type"] == "circle":
            center = (int(a["center_x"] * w), int(a["center_y"] * h))
            axes = (int(a["radius_x"] * w), int(a["radius_y"] * h))
            cv2.ellipse(overlay, center, axes, 0, 0, 360, c, -1)
            cv2.ellipse(img, center, axes, 0, 0, 360, c, thick)
            top, bottom, left = center[1] - axes[1], center[1] + axes[1], center[0] - axes[0]
        else:
            p0 = (int(a["x_min"] * w), int(a["y_min"] * h))
            p1 = (int(a["x_max"] * w), int(a["y_max"] * h))
            cv2.rectangle(overlay, p0, p1, c, -1)
            cv2.rectangle(img, p0, p1, c, thick)
            top, bottom, left = p0[1], p1[1], p0[0]
        # 上に文字を置く余白がなければ（画面上端のAOI）、下側に置く
        font_scale = w / 2400
        text_h = int(30 * font_scale) + 8
        label_xy = (left, top - 8) if top - text_h > 0 else (left, bottom + text_h)
        cv2.putText(img, a["name"], label_xy, cv2.FONT_HERSHEY_SIMPLEX, font_scale, c, thick, cv2.LINE_AA)
    img = cv2.addWeighted(overlay, 0.15, img, 0.85, 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def main(argv=None):
    p = argparse.ArgumentParser(description="AOI 設定の視角サイズと測定精度・位置ずれを確認します。")
    p.add_argument("--aoi", required=True, type=Path, help="AOI設定JSON（display を含むもの）")
    p.add_argument("--distance-mm", type=float, default=None, help="眼−画面距離（既定: JSON の viewing_distance_mm）")
    p.add_argument("--calib", type=Path, default=None, help="calibrate_validate.py の結果 JSON")
    p.add_argument("--min-size-deg", type=float, default=1.0, help="最小AOIサイズの目安（短辺, deg）")
    p.add_argument("--frame", type=Path, default=None, help="AOIを描画するフレーム画像（OBS録画から抽出したもの）")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = p.parse_args(argv)

    aois, raw = load_aoi_config(args.aoi)
    display = get_display(raw)
    if display is None:
        raise SystemExit("エラー: AOI設定JSONに display（画面の物理寸法）がありません。")
    distance = args.distance_mm or raw.get("viewing_distance_mm")
    if distance is None:
        raise SystemExit("エラー: 眼−画面距離が不明です（--distance-mm か JSON の viewing_distance_mm）。")
    calib = None
    if args.calib:
        with open(args.calib, "r", encoding="utf-8") as f:
            calib = json.load(f)

    table = size_table(aois, display, float(distance), calib, args.min_size_deg)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.out_dir / f"{args.aoi.stem}_size_deg.csv"
    table.to_csv(out_csv, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    print(f"画面: {display.name}  {display.width_mm}×{display.height_mm} mm  眼−画面距離: {distance} mm")
    cols = ["aoi", "type", "width_deg", "height_deg", "width_px", "height_px", "accuracy_deg",
            "flag_below_min_size", "flag_half_below_accuracy"]
    print(table[cols].round(2).to_string(index=False))
    for a in aois:
        if a["type"] == "circle":
            ext = table.set_index("aoi").loc[a["name"]]
            print(f"  {a['name']}: 半径 {ext['width_deg'] / 2:.2f}°（距離 {distance} mm、{a['radius_source']}）")
    small = table.loc[table["flag_below_min_size"], "aoi"].tolist()
    if small:
        print(f"⚠ 短辺が {args.min_size_deg}° 未満のAOI: {small}")
    if calib:
        bad = table.loc[table["flag_half_below_accuracy"] == True, "aoi"].tolist()  # noqa: E712
        if bad:
            print(f"⚠ 測定精度に対して小さすぎるAOI（短辺/2 < accuracy）: {bad}")
            print("  → これらのAOIの『注視0件』は「見ていない」と解釈できません。")
    overlaps = find_overlapping_aois(aois)
    if overlaps:
        print(f"⚠ 重なっているAOI: {overlaps}")
    print(f"保存: {out_csv}")

    if args.frame:
        out_img = args.out_dir / f"{args.frame.stem}_aoi.png"
        draw_aois_on_frame(args.frame, aois, out_img)
        print(f"保存: {out_img}")


if __name__ == "__main__":
    main()
