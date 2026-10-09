# -*- coding: utf-8 -*-
"""合成視線データの生成（実データは手元にない前提でテストする）。"""
import numpy as np
import pandas as pd

from aoi_geometry import DisplayGeometry

DISPLAY = DisplayGeometry(width_mm=596.7, height_mm=335.6, width_px=3840, height_px=2160, name="test")
DT = 1.0 / 60.0

CROSSHAIR = (0.5, 0.5)
MINIMAP = (0.1185, 0.23)
EMPTY = (0.75, 0.5)  # どのAOIにも入らない画面内の点


def make_df(points, dt=DT, t0=0.0):
    """
    points: (x, y) のリスト。None は無効サンプル（gaze_missing=True）。
    視線CSVと同じ列・同じ表現（欠測は NaN、gaze_missing は True/False）で返す。
    """
    rows = []
    for i, p in enumerate(points):
        t = t0 + i * dt
        if p is None:
            rows.append({
                "pc_time_sec": t, "system_time_stamp_us": int(round(t * 1e6)) + 10_000_000,
                "center_x": np.nan, "center_y": np.nan,
                "left_gaze_point_validity": 0, "right_gaze_point_validity": 0, "gaze_missing": True,
            })
        else:
            rows.append({
                "pc_time_sec": t, "system_time_stamp_us": int(round(t * 1e6)) + 10_000_000,
                "center_x": p[0], "center_y": p[1],
                "left_gaze_point_validity": 1, "right_gaze_point_validity": 1, "gaze_missing": False,
            })
    return pd.DataFrame(rows)


def hold(point, n):
    return [point] * n


def saccade(n=3):
    """分散閾値を確実に超える、ばらばらの位置のサンプル列。"""
    xs = [0.3, 0.9, 0.05, 0.6, 0.2]
    ys = [0.8, 0.2, 0.6, 0.1, 0.4]
    return [(xs[i % 5], ys[i % 5]) for i in range(n)]
