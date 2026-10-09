# -*- coding: utf-8 -*-
"""
Tobii Pro SDK によるキャリブレーション＋検証ツール（IMPROVEMENT_PLAN T2）。

目的
----
画面端の HUD（タイマー・弾数・クレジット等）で「注視0件」になったとき、それが
「見ていない」のか「測れていない」のかを区別するため、点ごとの精度を数値で残す。

- 実行タイミング：VALORANT の試合を始める前（試合中には起動しない。全画面の pygame 窓を出すため）
- 9点キャリブレーション（tobii_research.ScreenBasedCalibration）→ 検証
- 検証点：9点グリッド ＋ AOI JSON の各AOIの中心（HUD 上の実際の位置）
- 各点で、提示から --settle-ms 後の --window-ms 間のサンプルで
    accuracy  ：視線ベクトルと目標ベクトルの角度差の平均 [deg]
    precision ：連続サンプル間の角度差の RMS（RMS sample-to-sample）[deg]
  を左右眼それぞれ計算し、両眼の平均も出す。
  角度は user coordinate system（UCS, mm）で、眼の位置（gaze_origin）から
  画面上の注視点（gaze_point_in_user_coordinate_system）と目標点へのベクトルで求める。
  目標点の UCS 座標は eyetracker.get_display_area() の3隅から計算する
  （眼−画面距離の近似を使わない）。
- 結果を data/raw/calib_<subject>_<YYYYMMDD_HHMMSS>.json に保存

合否閾値（--max-mean-accuracy-deg / --max-hud-accuracy-deg）の既定値 1.0° / 1.5° は
計画段階の案で、ユーザーとの合意で確定する。

使い方（PowerShell）
--------------------
# 1) まず Spark で ScreenBasedCalibration が使えるかだけ確認する
python .\\src\\gaze_estimation\\calibrate_validate.py --check-only
# 2) キャリブレーション＋検証
python .\\src\\gaze_estimation\\calibrate_validate.py --subject P01
# 3) キャリブレーションが使えない場合は検証のみ（Eye Tracker Manager で較正した後に）
python .\\src\\gaze_estimation\\calibrate_validate.py --subject P01 --validate-only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parent.parent
_PROJECT_ROOT = _SRC_DIR.parent
for _p in (_SRC_DIR, _SRC_DIR / "aoi_detector"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SCRIPT_VERSION = "1.0.0"
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "raw"
DEFAULT_AOI_JSON = _SRC_DIR / "aoi_detector" / "valorant_hud_aoi_circular.json"
CALIBRATION_POINTS = [(0.5, 0.5), (0.1, 0.1), (0.5, 0.1), (0.9, 0.1), (0.1, 0.5),
                      (0.9, 0.5), (0.1, 0.9), (0.5, 0.9), (0.9, 0.9)]
GRID_VALIDATION_POINTS = [(0.1, 0.1), (0.5, 0.1), (0.9, 0.1), (0.1, 0.5), (0.5, 0.5),
                          (0.9, 0.5), (0.1, 0.9), (0.5, 0.9), (0.9, 0.9)]


# ---------------------------------------------------------------------------
# 計算（SDK・画面に依存しない部分。テスト対象）
# ---------------------------------------------------------------------------

def display_point_to_ucs(x: float, y: float, display_area: dict) -> np.ndarray:
    """
    正規化座標 (x, y) を UCS [mm] に変換する。
    display_area: {"top_left": (x,y,z), "top_right": ..., "bottom_left": ...}（SDK の DisplayArea と同じ値）
    """
    tl = np.asarray(display_area["top_left"], dtype=float)
    tr_ = np.asarray(display_area["top_right"], dtype=float)
    bl = np.asarray(display_area["bottom_left"], dtype=float)
    return tl + x * (tr_ - tl) + y * (bl - tl)


def angle_between_deg(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    """ベクトル同士の角度 [deg]（行ごと）。"""
    v1 = np.atleast_2d(v1)
    v2 = np.atleast_2d(v2)
    cos = np.sum(v1 * v2, axis=1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1))
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def eye_metrics(origins: np.ndarray, gaze_points: np.ndarray, target_ucs: np.ndarray) -> dict:
    """
    片眼分のサンプル（有効なものだけ、時刻順）から accuracy / precision を求める。
    origins, gaze_points: (n, 3) [mm, UCS]
    """
    n = len(origins)
    if n == 0:
        return {"n_samples": 0, "accuracy_deg": None, "precision_rms_s2s_deg": None}
    gaze_vec = gaze_points - origins
    target_vec = target_ucs[None, :] - origins
    acc = angle_between_deg(gaze_vec, target_vec)
    if n >= 2:
        s2s = angle_between_deg(gaze_vec[1:], gaze_vec[:-1])
        prec = float(np.sqrt(np.mean(s2s ** 2)))
    else:
        prec = None
    return {"n_samples": int(n), "accuracy_deg": float(np.mean(acc)), "precision_rms_s2s_deg": prec}


def point_metrics(samples: list[dict], target_xy: tuple[float, float], display_area: dict) -> dict:
    """
    1検証点分のサンプル（SDK の gaze_data 辞書のリスト）から、左右眼と両眼平均の指標を出す。
    正規化座標上の平均ずれ（bias）も出す（画面端で上下どちらにずれるかを見るため）。
    """
    target_ucs = display_point_to_ucs(*target_xy, display_area)
    out = {"target_x": target_xy[0], "target_y": target_xy[1], "n_samples_window": len(samples)}
    accs, precs = [], []
    for side in ("left", "right"):
        o, g, d = [], [], []
        for s in samples:
            if s.get(f"{side}_gaze_point_validity") == 1 and s.get(f"{side}_gaze_origin_validity") == 1:
                o.append(s[f"{side}_gaze_origin_in_user_coordinate_system"])
                g.append(s[f"{side}_gaze_point_in_user_coordinate_system"])
                d.append(s[f"{side}_gaze_point_on_display_area"])
        m = eye_metrics(np.asarray(o, dtype=float).reshape(-1, 3),
                        np.asarray(g, dtype=float).reshape(-1, 3), target_ucs)
        if d:
            d = np.asarray(d, dtype=float)
            m["bias_x_norm"] = float(d[:, 0].mean() - target_xy[0])
            m["bias_y_norm"] = float(d[:, 1].mean() - target_xy[1])
            m["valid_rate"] = len(d) / len(samples) if samples else None
        out[side] = m
        if m["accuracy_deg"] is not None:
            accs.append(m["accuracy_deg"])
        if m["precision_rms_s2s_deg"] is not None:
            precs.append(m["precision_rms_s2s_deg"])
    out["accuracy_deg"] = float(np.mean(accs)) if accs else None
    out["precision_rms_s2s_deg"] = float(np.mean(precs)) if precs else None
    return out


def validation_targets(aois: "list[dict] | None", min_separation: float = 0.03) -> list[dict]:
    """9点グリッド＋各AOIの中心。既存の点と近すぎるAOI中心は重複として省く。"""
    targets = [{"label": f"grid_{x:.1f}_{y:.1f}", "kind": "grid", "x": x, "y": y} for x, y in GRID_VALIDATION_POINTS]
    for aoi in aois or []:
        if aoi["type"] == "circle":
            cx, cy = aoi["center_x"], aoi["center_y"]
        else:
            cx, cy = (aoi["x_min"] + aoi["x_max"]) / 2, (aoi["y_min"] + aoi["y_max"]) / 2
        if any(abs(t["x"] - cx) < min_separation and abs(t["y"] - cy) < min_separation for t in targets):
            continue
        targets.append({"label": f"aoi_{aoi['name']}", "kind": "aoi", "aoi": aoi["name"], "x": cx, "y": cy})
    return targets


def judge(points: list[dict], max_mean_acc: float, max_hud_acc: float) -> dict:
    """全体平均 accuracy と、AOI中心の各点の accuracy で合否を判定する。"""
    accs = [p["accuracy_deg"] for p in points if p["accuracy_deg"] is not None]
    mean_acc = float(np.mean(accs)) if accs else None
    hud = [p for p in points if p.get("kind") == "aoi"]
    hud_fail = [p["label"] for p in hud if p["accuracy_deg"] is None or p["accuracy_deg"] > max_hud_acc]
    missing = [p["label"] for p in points if p["accuracy_deg"] is None]
    passed = mean_acc is not None and mean_acc <= max_mean_acc and not hud_fail
    return {
        "mean_accuracy_deg": mean_acc,
        "mean_precision_rms_s2s_deg": (float(np.mean([p["precision_rms_s2s_deg"] for p in points
                                                      if p["precision_rms_s2s_deg"] is not None]))
                                       if any(p["precision_rms_s2s_deg"] is not None for p in points) else None),
        "threshold_mean_accuracy_deg": max_mean_acc,
        "threshold_hud_accuracy_deg": max_hud_acc,
        "hud_points_failed": hud_fail,
        "points_without_data": missing,
        "passed": bool(passed),
    }


# ---------------------------------------------------------------------------
# 画面表示・SDK（研究室PCでのみ動く部分）
# ---------------------------------------------------------------------------

class TargetScreen:
    """選択ディスプレイ全面に枠なし窓を出し、注視点を描く。"""

    def __init__(self, monitor, pygame_mod):
        self.pg = pygame_mod
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{monitor.x},{monitor.y}"
        self.pg.init()
        self.w, self.h = monitor.width, monitor.height
        self.screen = self.pg.display.set_mode((self.w, self.h), self.pg.NOFRAME)
        self.pg.display.set_caption("calibrate_validate")
        self.font = self.pg.font.SysFont(None, 48)

    def pump_quit(self) -> bool:
        for ev in self.pg.event.get():
            if ev.type == self.pg.QUIT or (ev.type == self.pg.KEYDOWN and ev.key == self.pg.K_ESCAPE):
                return True
        return False

    def message(self, text: str, wait_key: bool = True) -> bool:
        self.screen.fill((128, 128, 128))
        for i, line in enumerate(text.split("\n")):
            surf = self.font.render(line, True, (0, 0, 0))
            self.screen.blit(surf, (self.w // 2 - surf.get_width() // 2, self.h // 2 - 60 + i * 56))
        self.pg.display.flip()
        while wait_key:
            for ev in self.pg.event.get():
                if ev.type == self.pg.KEYDOWN:
                    return ev.key != self.pg.K_ESCAPE
                if ev.type == self.pg.QUIT:
                    return False
            time.sleep(0.01)
        return True

    def target(self, x: float, y: float, shrink_sec: float) -> bool:
        """目標点を表示し、外円を shrink_sec かけて縮める（注視を促す）。ESC で False。"""
        px, py = int(x * self.w), int(y * self.h)
        t0 = time.perf_counter()
        while True:
            if self.pump_quit():
                return False
            frac = min(1.0, (time.perf_counter() - t0) / shrink_sec) if shrink_sec > 0 else 1.0
            self.screen.fill((128, 128, 128))
            r = int(40 - 28 * frac)
            self.pg.draw.circle(self.screen, (255, 255, 255), (px, py), r)
            self.pg.draw.circle(self.screen, (0, 0, 0), (px, py), 5)
            self.pg.display.flip()
            if frac >= 1.0:
                return True
            time.sleep(0.005)

    def hold(self, sec: float) -> bool:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < sec:
            if self.pump_quit():
                return False
            time.sleep(0.005)
        return True

    def close(self):
        self.pg.quit()


class SampleCollector:
    """SDK のコールバックで受けたサンプルを、ホスト時刻付きで溜める。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.samples = []

    def callback(self, gaze_data):
        with self.lock:
            self.samples.append((time.perf_counter(), gaze_data))

    def between(self, t0: float, t1: float) -> list[dict]:
        with self.lock:
            return [d for (t, d) in self.samples if t0 <= t <= t1]


def display_area_dict(eyetracker) -> dict:
    da = eyetracker.get_display_area()
    return {
        "top_left": tuple(da.top_left),
        "top_right": tuple(da.top_right),
        "bottom_left": tuple(da.bottom_left),
        "width_mm": float(da.width),
        "height_mm": float(da.height),
    }


def run_calibration(tr, eyetracker, screen: TargetScreen, shrink_sec: float) -> dict:
    calib = tr.ScreenBasedCalibration(eyetracker)
    calib.enter_calibration_mode()
    point_status = []
    try:
        for x, y in CALIBRATION_POINTS:
            if not screen.target(x, y, shrink_sec):
                raise KeyboardInterrupt
            status = calib.collect_data(x, y)
            if status != tr.CALIBRATION_STATUS_SUCCESS:
                status = calib.collect_data(x, y)  # SDK サンプルどおり1回だけ再試行
            point_status.append({"x": x, "y": y, "collect_status": str(status)})
        result = calib.compute_and_apply()
    finally:
        calib.leave_calibration_mode()
    used = []
    for p in getattr(result, "calibration_points", []) or []:
        used.append({"x": p.position_on_display_area[0], "y": p.position_on_display_area[1]})
    return {"status": str(result.status), "points": point_status, "points_used_by_sdk": used}


def run_validation(tr, eyetracker, screen: TargetScreen, targets: list[dict], display_area: dict,
                   shrink_sec: float, settle_ms: float, window_ms: float) -> list[dict]:
    collector = SampleCollector()
    eyetracker.subscribe_to(tr.EYETRACKER_GAZE_DATA, collector.callback, as_dictionary=True)
    results = []
    try:
        for tgt in targets:
            if not screen.target(tgt["x"], tgt["y"], shrink_sec):
                raise KeyboardInterrupt
            onset = time.perf_counter()
            if not screen.hold((settle_ms + window_ms) / 1000.0 + 0.05):
                raise KeyboardInterrupt
            win = collector.between(onset + settle_ms / 1000.0, onset + (settle_ms + window_ms) / 1000.0)
            m = point_metrics(win, (tgt["x"], tgt["y"]), display_area)
            results.append({**tgt, **m})
            acc = m["accuracy_deg"]
            print(f"  {tgt['label']:<28} accuracy={'—' if acc is None else f'{acc:.2f}°'}  "
                  f"n={m['n_samples_window']}")
    finally:
        eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, collector.callback)
    return results


def check_only(tr, eyetracker) -> int:
    print(f"デバイス: {eyetracker.device_name} / モデル: {eyetracker.model} / シリアル: "
          f"{getattr(eyetracker, 'serial_number', None)}")
    caps = getattr(eyetracker, "device_capabilities", ())
    can = getattr(tr, "CAPABILITY_CAN_DO_SCREEN_BASED_CALIBRATION", None)
    print(f"device_capabilities: {caps}")
    if can is not None:
        print(f"ScreenBasedCalibration 対応（capability）: {can in caps}")
    try:
        calib = tr.ScreenBasedCalibration(eyetracker)
        calib.enter_calibration_mode()
        calib.leave_calibration_mode()
        print("enter/leave_calibration_mode: 成功（このデバイスで SDK キャリブレーションを使えます）")
        return 0
    except Exception as e:
        print(f"enter/leave_calibration_mode: 失敗 {type(e).__name__}: {e}")
        print("→ --validate-only（Eye Tracker Manager で較正した後に検証だけ行う）を使ってください。")
        return 2


def main(argv=None):
    p = argparse.ArgumentParser(description="Tobii Pro SDK によるキャリブレーション＋検証（試合前に実行）")
    p.add_argument("--subject", default="NA", help="被験者ID")
    p.add_argument("--aoi", type=Path, default=DEFAULT_AOI_JSON, help="検証点に使う AOI JSON（各AOIの中心）")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--check-only", action="store_true", help="SDK キャリブレーションが使えるかだけ確認する")
    p.add_argument("--validate-only", action="store_true", help="キャリブレーションをせず検証のみ行う")
    p.add_argument("--shrink-sec", type=float, default=1.0, help="目標点の外円が縮む時間（秒）")
    p.add_argument("--settle-ms", type=float, default=800.0, help="検証：目標提示から集計開始までの待ち（ms）")
    p.add_argument("--window-ms", type=float, default=500.0, help="検証：集計に使う区間の長さ（ms）")
    p.add_argument("--max-mean-accuracy-deg", type=float, default=1.0, help="合格条件：全点平均 accuracy（案。要合意）")
    p.add_argument("--max-hud-accuracy-deg", type=float, default=1.5, help="合格条件：AOI中心の各点 accuracy（案。要合意）")
    args = p.parse_args(argv)

    try:
        import tobii_research as tr
    except ImportError:
        sys.exit("tobii-research が見つかりません。pip install tobii-research")
    found = tr.find_all_eyetrackers()
    if not found:
        sys.exit("Tobiiアイトラッカーが見つかりませんでした。接続を確認してください。")
    eyetracker = found[0]
    if args.check_only:
        sys.exit(check_only(tr, eyetracker))

    import pygame
    import screeninfo
    from aoi_geometry import load_aoi_config

    if args.subject == "NA":
        print("⚠ 警告: --subject が既定値 NA のままです。")
    aois, raw_aoi = load_aoi_config(args.aoi) if args.aoi else (None, {})
    targets = validation_targets(aois)

    monitors = screeninfo.get_monitors()
    print("\n=== 接続ディスプレイ一覧 ===")
    for i, m in enumerate(monitors, start=1):
        print(f"  {i}: {m.name}  ({m.width}x{m.height} @ ({m.x},{m.y}))")
    idx = int(input(f"測定対象ディスプレイの番号 (1-{len(monitors)}): ").strip())
    monitor = monitors[idx - 1]

    display_area = display_area_dict(eyetracker)
    screen = TargetScreen(monitor, pygame)
    calibration = None
    try:
        if not args.validate_only:
            if not screen.message("キャリブレーション：白い円の中心の黒い点を見続けてください\n"
                                  "（何かキーを押すと開始 / ESC で中止）"):
                return
            calibration = run_calibration(tr, eyetracker, screen, args.shrink_sec)
            print(f"キャリブレーション結果: {calibration['status']}")
        if not screen.message("検証：同じように点を見続けてください\n（何かキーを押すと開始 / ESC で中止）"):
            return
        points = run_validation(tr, eyetracker, screen, targets, display_area,
                                args.shrink_sec, args.settle_ms, args.window_ms)
    except KeyboardInterrupt:
        print("中止しました（保存しません）。")
        return
    finally:
        screen.close()

    verdict = judge(points, args.max_mean_accuracy_deg, args.max_hud_accuracy_deg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output_dir / f"calib_{args.subject}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "script": {"name": _THIS_FILE.name, "version": SCRIPT_VERSION},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "subject_id": args.subject,
        "eyetracker": {"device_name": eyetracker.device_name, "model": eyetracker.model,
                       "serial_number": getattr(eyetracker, "serial_number", None)},
        "display": {"name": monitor.name, "width": monitor.width, "height": monitor.height,
                    "x": monitor.x, "y": monitor.y},
        "display_area_ucs_mm": display_area,
        "os": platform.platform(),
        "aoi_config": str(args.aoi) if args.aoi else None,
        "parameters": {"settle_ms": args.settle_ms, "window_ms": args.window_ms, "shrink_sec": args.shrink_sec,
                       "calibration_points": CALIBRATION_POINTS},
        "definitions": {
            "accuracy_deg": "眼位置(gaze_origin)から注視点(gaze_point, UCS)へのベクトルと、目標点へのベクトルの角度差の平均",
            "precision_rms_s2s_deg": "連続サンプルの視線ベクトル間の角度差の二乗平均平方根",
            "binocular": "左右眼の値の平均（片眼のみ有効ならその眼の値）",
            "bias_norm": "正規化座標上の平均ずれ（注視点平均 − 目標）。正の y は下方向",
        },
        "calibration": calibration if calibration else {"status": "not_performed (--validate-only)"},
        "validation_points": points,
        "summary": verdict,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"\n全体平均 accuracy: {verdict['mean_accuracy_deg']}  precision: {verdict['mean_precision_rms_s2s_deg']}")
    print(f"判定: {'合格' if verdict['passed'] else '不合格'}  HUD点の不合格: {verdict['hud_points_failed']}")
    print(f"保存しました: {out}")


if __name__ == "__main__":
    main()
