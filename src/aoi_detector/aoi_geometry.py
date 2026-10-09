# -*- coding: utf-8 -*-
"""
AOI と画面の幾何（正規化座標 ↔ mm ↔ 視角）を扱うモジュール。

解析（gaze_metrics）、AOI妥当性チェック（aoi_check）、キャリブレーション検証
（calibrate_validate）から共通で import する。ハイフンを含むバージョン付き
ファイルは import できないため、ロジックはここに置く。

座標系
------
- 正規化座標：Tobii display area 準拠。左上 (0,0)、右下 (1,1)。画面外は 0 未満・1 超
- 視角への変換：眼が画面中央の正面、距離 D [mm] にあると仮定し、
      theta_x = atan((x - 0.5) * W / D)、theta_y = atan((y - 0.5) * H / D)
  （W, H は画面の表示領域の物理寸法 [mm]）。卒論ではこの近似を明記する。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

OUTSIDE_LABEL = "outside"
OFFSCREEN_LABEL = "offscreen"
INVALID_LABEL = "invalid"

# normalized座標系で許容する緩めの範囲（ピクセル座標混入の検出用）
NORMALIZED_SANITY_RANGE = (-1.5, 2.5)


# ---------------------------------------------------------------------------
# 画面の物理寸法
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DisplayGeometry:
    """表示領域の物理寸法と解像度。"""

    width_mm: float
    height_mm: float
    width_px: int
    height_px: int
    name: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DisplayGeometry":
        try:
            return cls(
                width_mm=float(d["width_mm"]),
                height_mm=float(d["height_mm"]),
                width_px=int(d["width_px"]),
                height_px=int(d["height_px"]),
                name=str(d.get("name", "")),
            )
        except KeyError as e:
            raise ValueError(
                f"display 設定に {e} がありません（width_mm, height_mm, width_px, height_px が必要）"
            ) from None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "width_px": self.width_px,
            "height_px": self.height_px,
        }


def norm_to_deg(x, y, display: DisplayGeometry, distance_mm: float):
    """
    正規化座標を、画面中央を 0° とする水平・垂直の視角 [deg] に変換する。
    x, y はスカラーでも numpy 配列でもよい。
    """
    if distance_mm <= 0:
        raise ValueError(f"眼−画面距離が不正です: {distance_mm}")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    theta_x = np.degrees(np.arctan((x - 0.5) * display.width_mm / distance_mm))
    theta_y = np.degrees(np.arctan((y - 0.5) * display.height_mm / distance_mm))
    return theta_x, theta_y


def deg_radius_to_norm(radius_deg: float, display: DisplayGeometry, distance_mm: float):
    """
    画面中央に置いた円の視角半径 [deg] を、正規化座標の半径 (radius_x, radius_y) に変換する。
    r_mm = D * tan(radius_deg)。画面中央以外に置く円では近似になる。
    """
    r_mm = distance_mm * math.tan(math.radians(radius_deg))
    return r_mm / display.width_mm, r_mm / display.height_mm


# ---------------------------------------------------------------------------
# AOI設定の読み込み
# ---------------------------------------------------------------------------

def load_aoi_config(path) -> tuple[list[dict], dict]:
    """
    AOI設定JSONを読み込み、(aois, raw_config) を返す。

    - circle で radius_deg が指定されている場合、JSON の display と
      viewing_distance_mm から radius_x / radius_y を計算して埋める
      （手計算値を JSON に直書きしないため）。radius_x/radius_y を直接書いた
      旧形式もそのまま読める。各AOIに "radius_source" を付けて出所を残す。
    - 返す aois は JSON の記載順（＝重なり時の優先順位）を保つ。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    aois = raw.get("aois")
    if not isinstance(aois, list) or not aois:
        raise ValueError("AOI設定JSONには aois の配列が必要です。")

    names = [aoi.get("name") for aoi in aois]
    if any(n is None for n in names):
        raise ValueError("AOI設定に name が欠けている要素があります。")
    reserved = {OUTSIDE_LABEL, OFFSCREEN_LABEL, INVALID_LABEL}
    bad = sorted(set(names) & reserved)
    if bad:
        raise ValueError(f"AOI名に予約語は使えません: {bad}")
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"AOI設定に重複した name があります: {dup}")

    display = get_display(raw)
    design_distance = raw.get("viewing_distance_mm")

    resolved = []
    for aoi in aois:
        aoi = dict(aoi)
        shape = aoi.get("type", "rectangle")
        aoi["type"] = shape
        if shape == "circle":
            if "radius_deg" in aoi:
                if display is None or design_distance is None:
                    raise ValueError(
                        f"AOI '{aoi['name']}' は radius_deg で定義されていますが、JSON に "
                        "display と viewing_distance_mm がありません。"
                    )
                rx, ry = deg_radius_to_norm(float(aoi["radius_deg"]), display, float(design_distance))
                aoi["radius_x"], aoi["radius_y"] = rx, ry
                aoi["radius_source"] = (
                    f"radius_deg={aoi['radius_deg']} @ viewing_distance_mm={design_distance}"
                )
            elif "radius_x" in aoi and "radius_y" in aoi:
                aoi["radius_source"] = "radius_x/radius_y（正規化半径の直接指定）"
            else:
                raise ValueError(f"circle AOI '{aoi['name']}' に radius_deg または radius_x/radius_y が必要です。")
            for key in ("center_x", "center_y"):
                if key not in aoi:
                    raise ValueError(f"circle AOI '{aoi['name']}' に {key} がありません。")
        elif shape == "rectangle":
            for key in ("x_min", "x_max", "y_min", "y_max"):
                if key not in aoi:
                    raise ValueError(f"rectangle AOI '{aoi['name']}' に {key} がありません。")
            if not (aoi["x_min"] < aoi["x_max"] and aoi["y_min"] < aoi["y_max"]):
                raise ValueError(f"rectangle AOI '{aoi['name']}' の min/max が逆転しています。")
        else:
            raise ValueError(f"未対応のAOI形状です: {shape}（AOI '{aoi['name']}'）")
        resolved.append(aoi)

    warnings = sanity_check_coordinates(resolved, raw.get("coordinate_system"))
    for w in warnings:
        print(f"⚠ 警告: {w}")
    return resolved, raw


def get_display(raw_config: dict) -> "DisplayGeometry | None":
    d = raw_config.get("display")
    return DisplayGeometry.from_dict(d) if d else None


def sanity_check_coordinates(aois: list[dict], coordinate_system) -> list[str]:
    """coordinate_system=normalized なのにピクセル座標などが混入していないかを緩く検査する。"""
    if coordinate_system not in (None, "normalized"):
        return []
    lo, hi = NORMALIZED_SANITY_RANGE
    suspicious = []
    for aoi in aois:
        if aoi["type"] == "rectangle":
            values = [aoi["x_min"], aoi["x_max"], aoi["y_min"], aoi["y_max"]]
        else:
            values = [aoi["center_x"], aoi["center_y"], aoi["radius_x"], aoi["radius_y"]]
        if any(not (lo <= v <= hi) for v in values):
            suspicious.append(aoi["name"])
    if suspicious:
        return [
            f"coordinate_system=normalized ですが、[{lo}, {hi}] を外れる座標値があります"
            f"（ピクセル座標混入の可能性）: {suspicious}"
        ]
    return []


# ---------------------------------------------------------------------------
# AOI判定
# ---------------------------------------------------------------------------

def aoi_mask(x: np.ndarray, y: np.ndarray, aoi: dict) -> np.ndarray:
    """
    各点が AOI 内かどうかの bool 配列を返す。
    境界の扱い：矩形は [min, max)（左上の辺を含み右下の辺を含まない。隣接AOIで
    境界上の点が二重に数えられないようにするため）。円（楕円）は境界を含む（<= 1）。
    NaN は常に False。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    with np.errstate(invalid="ignore"):
        if aoi["type"] == "rectangle":
            return (x >= aoi["x_min"]) & (x < aoi["x_max"]) & (y >= aoi["y_min"]) & (y < aoi["y_max"])
        dx = (x - aoi["center_x"]) / aoi["radius_x"]
        dy = (y - aoi["center_y"]) / aoi["radius_y"]
        return (dx * dx + dy * dy) <= 1.0


def assign_aoi(x, y, aois: list[dict]) -> np.ndarray:
    """
    各点に AOI 名を割り当てる（object 配列）。どの AOI にも入らない点は "outside"。
    重なりがある場合は aois の記載順で先に一致したものを採る（先勝ち）。
    画面外判定・無効判定は呼び出し側で行う。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    result = np.full(x.shape, OUTSIDE_LABEL, dtype=object)
    unassigned = np.ones(x.shape, dtype=bool)
    for aoi in aois:
        m = unassigned & aoi_mask(x, y, aoi)
        result[m] = aoi["name"]
        unassigned &= ~m
    return result


def offscreen_mask(x, y) -> np.ndarray:
    """正規化座標が表示領域 [0,1]×[0,1] の外にあるか。NaN は False。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    with np.errstate(invalid="ignore"):
        return (x < 0) | (x > 1) | (y < 0) | (y > 1)


def offscreen_edge(x, y) -> np.ndarray:
    """
    画面外サンプルが、どの辺の外にあるか（"top"/"bottom"/"left"/"right"）を返す。
    画面内・NaN は ""。角の外側では、はみ出し量が大きい辺を採る。
    上下端のHUD（タイマー・弾数・クレジット等）が「測れていない」可能性の診断に使う。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    over = np.stack([
        np.where(y < 0, -y, 0.0),        # top
        np.where(y > 1, y - 1, 0.0),     # bottom
        np.where(x < 0, -x, 0.0),        # left
        np.where(x > 1, x - 1, 0.0),     # right
    ])
    over = np.nan_to_num(over, nan=0.0)
    labels = np.array(["top", "bottom", "left", "right"], dtype=object)
    result = labels[np.argmax(over, axis=0)]
    result = np.where(over.max(axis=0) > 0, result, "")
    return result.astype(object)


def find_overlapping_aois(aois: list[dict], grid: int = 800) -> list[dict]:
    """
    AOI同士の重なりを格子点で近似検出する。重なり面積（正規化面積）と、
    先勝ちで採用される側を返す。
    """
    xs = (np.arange(grid) + 0.5) / grid
    gx, gy = np.meshgrid(xs, xs)
    masks = [aoi_mask(gx, gy, a) for a in aois]
    out = []
    for i in range(len(aois)):
        for j in range(i + 1, len(aois)):
            n = int(np.count_nonzero(masks[i] & masks[j]))
            if n:
                out.append({
                    "aoi_a": aois[i]["name"],
                    "aoi_b": aois[j]["name"],
                    "overlap_area_normalized": n / (grid * grid),
                    "assigned_to": aois[i]["name"],
                })
    return out


def aoi_extent_deg(aoi: dict, display: DisplayGeometry, distance_mm: float) -> dict:
    """AOIの外接矩形の幅・高さを視角 [deg] で返す（画面上の位置を考慮した atan 差分）。"""
    if aoi["type"] == "rectangle":
        x0, x1, y0, y1 = aoi["x_min"], aoi["x_max"], aoi["y_min"], aoi["y_max"]
    else:
        x0 = aoi["center_x"] - aoi["radius_x"]
        x1 = aoi["center_x"] + aoi["radius_x"]
        y0 = aoi["center_y"] - aoi["radius_y"]
        y1 = aoi["center_y"] + aoi["radius_y"]
    tx0, ty0 = norm_to_deg(x0, y0, display, distance_mm)
    tx1, ty1 = norm_to_deg(x1, y1, display, distance_mm)
    return {
        "width_deg": float(tx1 - tx0),
        "height_deg": float(ty1 - ty0),
        "width_px": (x1 - x0) * display.width_px,
        "height_px": (y1 - y0) * display.height_px,
        "width_mm": (x1 - x0) * display.width_mm,
        "height_mm": (y1 - y0) * display.height_mm,
    }
