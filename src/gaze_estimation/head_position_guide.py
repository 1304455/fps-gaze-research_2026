# -*- coding: utf-8 -*-
"""
頭部位置ガイド（計測前に、眼の位置を目標に合わせるための画面表示）。

calibrate_validate.py から呼ぶ（キャリブレーションの前に自動で表示。--position-only で単独実行）。
試合中には起動しない（全画面の pygame 窓を出すため）。

- 視線データの gaze_origin_in_user_coordinate_system（UCS, mm。原点はアイトラッカー中心）を使い、
    距離  z：眼−トラッカー距離。目標 --target-distance-mm（既定 650）± --tolerance-z-mm
    左右  x：両眼の中点の横ずれ。目標 0（トラッカー＝画面の中央の正面）± --tolerance-x-mm
    上下  y：参考表示。--reference で前回の位置を指定したときだけ目標にする
  を直近 0.5 秒の平均でリアルタイムに表示する
- 確定時（SPACE / Enter）の直近 1 秒の平均を記録し、calib_*.json の head_position に残す
- UCS の x 軸は「ユーザーから見て右が正」として、鏡と同じ向き（右に動くと円も右）に描く。
  向きが逆なら --flip-x を付ける（docs/LAB_CHECKLIST.md で確認する）

注意：z は眼−トラッカー距離で、眼−画面中央距離とは厳密には一致しない（卒論では近似として明記する）。
"""

from __future__ import annotations

import time

import numpy as np

AVERAGE_WINDOW_SEC = 0.5
RECORD_WINDOW_SEC = 1.0


# ---------------------------------------------------------------------------
# 計算（SDK・画面に依存しない部分。テスト対象）
# ---------------------------------------------------------------------------

def summarize_position(samples: list[dict]) -> dict:
    """
    gaze_data 辞書のリストから、左右眼の位置（UCS, mm）の平均と有効率を返す。
    両眼の中点 (x_mid, y_mid, z_mid) は、片眼しか有効でないサンプルではその眼の値を使う。
    """
    out = {"n_samples": len(samples)}
    mids = []
    for side in ("left", "right"):
        pts = [s[f"{side}_gaze_origin_in_user_coordinate_system"] for s in samples
               if s.get(f"{side}_gaze_origin_validity") == 1]
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        out[f"{side}_valid_rate"] = (len(pts) / len(samples)) if samples else 0.0
        out[f"{side}_xyz_mm"] = pts.mean(axis=0).tolist() if len(pts) else None
    for s in samples:
        eyes = [np.asarray(s[f"{e}_gaze_origin_in_user_coordinate_system"], dtype=float)
                for e in ("left", "right") if s.get(f"{e}_gaze_origin_validity") == 1]
        if eyes:
            mids.append(np.mean(eyes, axis=0))
    if mids:
        m = np.mean(mids, axis=0)
        out["x_mid_mm"], out["y_mid_mm"], out["z_mid_mm"] = float(m[0]), float(m[1]), float(m[2])
        out["z_sd_mm"] = float(np.std([v[2] for v in mids])) if len(mids) > 1 else 0.0
    else:
        out["x_mid_mm"] = out["y_mid_mm"] = out["z_mid_mm"] = out["z_sd_mm"] = None
    return out


def judge_position(pos: dict, *, target_z_mm: float, tol_z_mm: float, target_x_mm: float = 0.0,
                   tol_x_mm: float = 30.0, target_y_mm: "float | None" = None, tol_y_mm: float = 30.0,
                   min_valid_rate: float = 0.8, flip_x: bool = False) -> dict:
    """
    位置が目標範囲内かを判定し、画面に出す指示文を返す。
    ok_* は各軸の判定（target_y_mm が None なら y は判定しない）。
    """
    msgs = []
    valid = min(pos.get("left_valid_rate", 0.0), pos.get("right_valid_rate", 0.0))
    ok_valid = valid >= min_valid_rate
    if not ok_valid:
        msgs.append(f"両眼が検出されていません（有効率 {valid:.0%}）。顔をトラッカーの正面に向けてください")
    z, x, y = pos.get("z_mid_mm"), pos.get("x_mid_mm"), pos.get("y_mid_mm")
    if z is None:
        return {"ok": False, "ok_valid": False, "ok_z": False, "ok_x": False, "ok_y": None,
                "messages": msgs or ["眼が検出されていません"]}
    dz, dx = z - target_z_mm, x - target_x_mm
    ok_z = abs(dz) <= tol_z_mm
    ok_x = abs(dx) <= tol_x_mm
    if not ok_z:
        msgs.append(f"距離 {z:.0f} mm：{'近すぎます。後ろへ' if dz < 0 else '遠すぎます。前へ'} 約{abs(dz):.0f} mm")
    if not ok_x:
        # UCS の x はユーザーから見て右が正（flip_x なら逆）。右にずれていれば左へ
        to_left = (dx > 0) != flip_x
        msgs.append(f"左右に {abs(dx):.0f} mm ずれています：{'左' if to_left else '右'}へ動いてください")
    ok_y = None
    if target_y_mm is not None:
        dy = y - target_y_mm
        ok_y = abs(dy) <= tol_y_mm
        if not ok_y:
            msgs.append(f"上下に {abs(dy):.0f} mm ずれています：{'上' if dy < 0 else '下'}へ（椅子の高さ・姿勢）")
    ok = ok_valid and ok_z and ok_x and (ok_y is not False)
    if ok:
        msgs.append("OK：この姿勢のまま SPACE を押してください")
    return {"ok": bool(ok), "ok_valid": bool(ok_valid), "ok_z": bool(ok_z), "ok_x": bool(ok_x),
            "ok_y": ok_y, "messages": msgs}


def reference_from_calib(calib: dict) -> "dict | None":
    """前回の calib_*.json から、確定時の頭部位置（目標に使う x, y, z）を取り出す。"""
    hp = (calib or {}).get("head_position") or {}
    rec = hp.get("recorded") or {}
    if rec.get("x_mid_mm") is None:
        return None
    return {"x_mid_mm": rec["x_mid_mm"], "y_mid_mm": rec["y_mid_mm"], "z_mid_mm": rec["z_mid_mm"]}


# ---------------------------------------------------------------------------
# 画面表示（研究室PCでのみ動く部分）
# ---------------------------------------------------------------------------

def run_position_guide(tr, eyetracker, screen, *, target_z_mm: float, tol_z_mm: float, tol_x_mm: float,
                       reference: "dict | None" = None, tol_y_mm: float = 30.0, flip_x: bool = False) -> dict:
    """
    位置合わせ画面を表示する。SPACE/Enter で確定（範囲外でも確定はできる。その場合 ok=False で記録）、
    ESC で KeyboardInterrupt。screen は calibrate_validate.TargetScreen。
    """
    from calibrate_validate import SampleCollector

    pg = screen.pg
    collector = SampleCollector()
    eyetracker.subscribe_to(tr.EYETRACKER_GAZE_DATA, collector.callback, as_dictionary=True)
    target_x = reference["x_mid_mm"] if reference else 0.0
    target_y = reference["y_mid_mm"] if reference else None
    if reference and target_z_mm is None:
        target_z_mm = reference["z_mid_mm"]
    w, h = screen.w, screen.h
    scale = (h * 0.35) / 150.0          # 正面図：±150 mm を画面高さの 35% に
    cx0, cy0 = w // 2, int(h * 0.5)
    sign = -1.0 if flip_x else 1.0
    try:
        while True:
            for ev in pg.event.get():
                if ev.type == pg.QUIT or (ev.type == pg.KEYDOWN and ev.key == pg.K_ESCAPE):
                    raise KeyboardInterrupt
                if ev.type == pg.KEYDOWN and ev.key in (pg.K_SPACE, pg.K_RETURN):
                    now = time.perf_counter()
                    rec = summarize_position(collector.between(now - RECORD_WINDOW_SEC, now))
                    verdict = judge_position(rec, target_z_mm=target_z_mm, tol_z_mm=tol_z_mm,
                                             target_x_mm=target_x, tol_x_mm=tol_x_mm,
                                             target_y_mm=target_y, tol_y_mm=tol_y_mm, flip_x=flip_x)
                    return {
                        "recorded": rec,
                        "ok": verdict["ok"],
                        "messages_at_confirm": verdict["messages"],
                        "target": {"z_mm": target_z_mm, "tol_z_mm": tol_z_mm, "x_mm": target_x,
                                   "tol_x_mm": tol_x_mm, "y_mm": target_y, "tol_y_mm": tol_y_mm,
                                   "reference_used": reference is not None},
                        "definition": "直近1秒の gaze_origin（UCS, mm）の平均。z は眼−トラッカー距離",
                    }
            now = time.perf_counter()
            pos = summarize_position(collector.between(now - AVERAGE_WINDOW_SEC, now))
            v = judge_position(pos, target_z_mm=target_z_mm, tol_z_mm=tol_z_mm, target_x_mm=target_x,
                               tol_x_mm=tol_x_mm, target_y_mm=target_y, tol_y_mm=tol_y_mm, flip_x=flip_x)

            screen.screen.fill((40, 40, 40))
            # 正面図：目標の枠（左右・上下の許容範囲）
            ty = target_y if target_y is not None else (pos["y_mid_mm"] or 0.0)
            box = pg.Rect(0, 0, int(2 * tol_x_mm * scale), int(2 * tol_y_mm * scale))
            box.center = (cx0, cy0)
            pg.draw.rect(screen.screen, (90, 90, 90), box, 3)
            color = (60, 200, 90) if v["ok"] else ((230, 200, 60) if v["ok_valid"] else (220, 70, 70))
            for side in ("left", "right"):
                xyz = pos.get(f"{side}_xyz_mm")
                if xyz is None:
                    continue
                px = cx0 + int(sign * (xyz[0] - target_x) * scale)
                py = cy0 - int((xyz[1] - ty) * scale)   # UCS の y は上が正、画面は下が正
                r = max(8, int(28 * (target_z_mm / max(xyz[2], 1.0))))  # 近いほど大きく
                pg.draw.circle(screen.screen, color, (px, py), r, 4)
                lab = screen.font.render("L" if side == "left" else "R", True, color)
                screen.screen.blit(lab, (px - lab.get_width() // 2, py - r - lab.get_height()))
            # 両眼の中点（枠はこの点の許容範囲。中点が枠に入れば左右・上下は OK）
            if pos["x_mid_mm"] is not None:
                mx = cx0 + int(sign * (pos["x_mid_mm"] - target_x) * scale)
                my = cy0 - int((pos["y_mid_mm"] - ty) * scale)
                pg.draw.line(screen.screen, color, (mx - 12, my), (mx + 12, my), 4)
                pg.draw.line(screen.screen, color, (mx, my - 12), (mx, my + 12), 4)
            # 距離バー
            bar = pg.Rect(int(w * 0.2), int(h * 0.78), int(w * 0.6), 24)
            pg.draw.rect(screen.screen, (90, 90, 90), bar, 2)
            zmin, zmax = target_z_mm - 150, target_z_mm + 150
            def zx(z):
                return bar.left + int((min(max(z, zmin), zmax) - zmin) / (zmax - zmin) * bar.width)
            ok_zone = pg.Rect(zx(target_z_mm - tol_z_mm), bar.top, zx(target_z_mm + tol_z_mm) - zx(target_z_mm - tol_z_mm), bar.height)
            pg.draw.rect(screen.screen, (50, 110, 60), ok_zone)
            if pos["z_mid_mm"] is not None:
                pg.draw.line(screen.screen, color, (zx(pos["z_mid_mm"]), bar.top - 10), (zx(pos["z_mid_mm"]), bar.bottom + 10), 5)
            for text, x_at in ((f"近い（{zmin:.0f} mm）", bar.left), (f"遠い（{zmax:.0f} mm）", bar.right)):
                surf = screen.small_font.render(text, True, (180, 180, 180))
                screen.screen.blit(surf, (x_at - (surf.get_width() if x_at == bar.right else 0), bar.bottom + 14))
            z_text = "—" if pos["z_mid_mm"] is None else "{:.0f}".format(pos["z_mid_mm"])
            x_text = "—" if pos["x_mid_mm"] is None else "{:+.0f}".format(pos["x_mid_mm"] - target_x)
            lines = [
                "頭の位置合わせ（＋印を枠の中に、距離の線を緑の範囲に）",
                f"距離: {z_text} mm（目標 {target_z_mm:.0f} ± {tol_z_mm:.0f}）　左右: {x_text} mm",
                *v["messages"],
            ]
            y_text = int(h * 0.03)
            for text in lines:
                surf = screen.font.render(text, True, (230, 230, 230))
                screen.screen.blit(surf, (int(w * 0.04), y_text))
                y_text += surf.get_height() + 6
            foot = screen.small_font.render("SPACE / Enter：確定　ESC：中止", True, (180, 180, 180))
            screen.screen.blit(foot, (int(w * 0.04), h - foot.get_height() - int(h * 0.03)))
            pg.display.flip()
            time.sleep(1 / 30)
    finally:
        eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, collector.callback)
