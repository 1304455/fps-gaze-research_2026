"""
gaze_visualizer.py
------------------
tobii_capture_stable_timestamped.py が出力する CSV (gaze_data_*.csv) を読み込み、
記録された視点(center_x, center_y)を画面上にリアルタイム再生・描画するビューワー。

前提:
  - CSVの center_x / center_y は、録画時の画面解像度に対する正規化座標 (0.0〜1.0)。
  - 録画時の画面解像度と再生時の画面解像度が異なる場合は、
    --width / --height で録画時の解像度を明示すること。
    (指定しなければ「再生環境の現在の解像度」を録画時解像度とみなして描画する)

操作方法 (ウィンドウ表示中):
  SPACE       : 一時停止 / 再開
  R           : 再生位置を先頭に戻す
  ESC または Q: 終了
  +  / -      : 再生速度を上げる / 下げる
  H           : ヒートマップモードに切替 (分析用。蓄積表示)
  L           : ライブ再生モードに戻る
  C           : ヒートマップをクリア
  →  / ←      : 5秒早送り / 巻き戻し (ライブ・ヒートマップ両モード対応。巻き戻しても二重加算はしない)

使い方:
  pip install pygame
  python gaze_visualizer.py gaze_data_20260101_120000.csv
  python gaze_visualizer.py gaze_data_20260101_120000.csv --speed 2 --trail 1.0
  python gaze_visualizer.py gaze_data_20260101_120000.csv --width 1920 --height 1080
  python gaze_visualizer.py gaze_data_20260101_120000.csv --fullscreen
"""

import argparse
import bisect
import csv
import sys
from pathlib import Path

import pygame


def load_rows(csv_path: Path):
    """CSVを読み込み、再生に必要な情報のみを抽出したリストを返す。"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"pc_time_sec", "center_x", "center_y"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            sys.exit(f"エラー: CSVに必要な列がありません: {missing}")

        for r in reader:
            try:
                t = float(r["pc_time_sec"])
            except (TypeError, ValueError):
                continue

            def to_float(key):
                v = r.get(key)
                if v in (None, "", "None"):
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            rows.append(
                {
                    "t": t,
                    "cx": to_float("center_x"),
                    "cy": to_float("center_y"),
                    "lx": to_float("left_x"),
                    "ly": to_float("left_y"),
                    "rx": to_float("right_x"),
                    "ry": to_float("right_y"),
                    "lvalid": r.get("left_gaze_point_validity") in ("True", "1", "true"),
                    "rvalid": r.get("right_gaze_point_validity") in ("True", "1", "true"),
                }
            )

    if not rows:
        sys.exit("エラー: CSVにデータ行がありませんでした。")

    rows.sort(key=lambda r: r["t"])
    return rows


def build_arg_parser():
    p = argparse.ArgumentParser(description="Tobii gaze CSV のリプレイ描画ビューワー")
    p.add_argument("csv_path", type=Path, help="gaze_data_*.csv のパス")
    p.add_argument("--width", type=int, default=None, help="録画時の画面幅(px)。未指定なら再生環境の解像度を使用")
    p.add_argument("--height", type=int, default=None, help="録画時の画面高さ(px)。未指定なら再生環境の解像度を使用")
    p.add_argument("--speed", type=float, default=1.0, help="再生速度の倍率 (デフォルト 1.0)")
    p.add_argument("--radius", type=int, default=14, help="視点マーカーの半径(px)")
    p.add_argument("--trail", type=float, default=0.4, help="軌跡を残す時間(秒)。0で軌跡なし")
    p.add_argument("--fullscreen", action="store_true", help="フルスクリーンで起動")
    p.add_argument("--show-eyes", action="store_true", help="左右の眼の点も別色で表示する")
    return p


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def main():
    args = build_arg_parser().parse_args()
    rows = load_rows(args.csv_path)
    duration = rows[-1]["t"] - rows[0]["t"]
    t0 = rows[0]["t"]
    times = [r["t"] for r in rows]  # 二分探索用(再生位置 -> 行インデックスの対応付け)

    pygame.init()
    pygame.display.set_caption(f"Gaze Replay - {args.csv_path.name}")

    if args.fullscreen:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        info = pygame.display.Info()
        win_w = args.width or min(info.current_w, 1280)
        win_h = args.height or min(info.current_h, 800)
        screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)

    # 録画時の解像度(正規化座標 -> px 変換の基準)。
    # 未指定の場合は「現在のウィンドウ解像度」を録画時解像度とみなす(暫定値)。
    rec_w = args.width or screen.get_width()
    rec_h = args.height or screen.get_height()

    font = pygame.font.SysFont(None, 22)
    clock = pygame.time.Clock()

    mode = "live"  # "live" or "heatmap"
    paused = False
    speed = args.speed
    play_t = 0.0  # 再生開始からの経過秒(録画タイムライン上)
    idx = 0
    last_heat_idx_accumulated = -1  # ヒートマップに加算済みの最終インデックス(重複加算/フレームレート依存を防ぐ)

    heat_surface = pygame.Surface(screen.get_size(), pygame.SRCALPHA)

    def to_px(nx, ny, surf):
        sw, sh = surf.get_size()
        # 録画時解像度の正規化座標を、現在の再生ウィンドウサイズにスケーリング
        px = clamp(nx, 0.0, 1.0) * sw
        py = clamp(ny, 0.0, 1.0) * sh
        return int(px), int(py)

    running = True
    while running:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.VIDEORESIZE and not args.fullscreen:
                screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
                heat_surface = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key == pygame.K_r:
                    play_t = 0.0
                    idx = 0
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                    speed = round(speed + 0.25, 2)
                elif event.key == pygame.K_MINUS:
                    speed = max(0.1, round(speed - 0.25, 2))
                elif event.key == pygame.K_h:
                    mode = "heatmap"
                elif event.key == pygame.K_l:
                    mode = "live"
                elif event.key == pygame.K_c:
                    heat_surface.fill((0, 0, 0, 0))
                    last_heat_idx_accumulated = -1
                elif event.key == pygame.K_RIGHT:
                    play_t = clamp(play_t + 5.0, 0.0, duration)
                elif event.key == pygame.K_LEFT:
                    play_t = clamp(play_t - 5.0, 0.0, duration)

        if not paused:
            play_t += dt * speed
            if play_t > duration:
                play_t = duration
                paused = True

        # 現在の再生時刻に対応するインデックスを求める(早送り・巻き戻し両対応)
        target_abs_t = t0 + play_t
        idx = bisect.bisect_right(times, target_abs_t) - 1
        idx = int(clamp(idx, 0, len(rows) - 1))
        cur = rows[idx]

        if mode == "live":
            screen.fill((18, 18, 22))

            # トレイル(直近 args.trail 秒分の過去の点を薄く描画)
            if args.trail > 0:
                trail_start_t = target_abs_t - args.trail
                j = idx
                while j >= 0 and rows[j]["t"] >= trail_start_t:
                    r2 = rows[j]
                    if r2["cx"] is not None and r2["cy"] is not None:
                        age = (target_abs_t - r2["t"]) / args.trail
                        alpha = int(clamp(180 * (1.0 - age), 0, 255))
                        if alpha > 0:
                            px, py = to_px(r2["cx"], r2["cy"], screen)
                            s = pygame.Surface((args.radius * 2, args.radius * 2), pygame.SRCALPHA)
                            pygame.draw.circle(
                                s, (80, 180, 255, alpha), (args.radius, args.radius), max(2, args.radius // 2)
                            )
                            screen.blit(s, (px - args.radius, py - args.radius))
                    j -= 1

            if cur["cx"] is not None and cur["cy"] is not None:
                px, py = to_px(cur["cx"], cur["cy"], screen)
                pygame.draw.circle(screen, (255, 60, 60), (px, py), args.radius)
                pygame.draw.circle(screen, (255, 255, 255), (px, py), args.radius, 2)
            else:
                msg = font.render("視線データロスト (瞬き/外れ)", True, (255, 180, 60))
                screen.blit(msg, (20, 50))

            if args.show_eyes:
                if cur["lvalid"] and cur["lx"] is not None and cur["ly"] is not None:
                    px, py = to_px(cur["lx"], cur["ly"], screen)
                    pygame.draw.circle(screen, (80, 255, 120), (px, py), max(4, args.radius // 2))
                if cur["rvalid"] and cur["rx"] is not None and cur["ry"] is not None:
                    px, py = to_px(cur["rx"], cur["ry"], screen)
                    pygame.draw.circle(screen, (255, 220, 80), (px, py), max(4, args.radius // 2))

            hud_lines = [
                f"Time: {play_t:6.2f} / {duration:6.2f} s  Speed: x{speed:.2f}  {'PAUSED' if paused else 'PLAYING'}",
                f"Rec Resolution: {rec_w}x{rec_h}  Row: {idx+1}/{len(rows)}",
                "SPACE: Pause  R: Restart  H: Heatmap  +/-: Speed  ←→: Step 5s  Q/ESC: Quit",
            ]
            for i, line in enumerate(hud_lines):
                txt = font.render(line, True, (210, 210, 210))
                screen.blit(txt, (10, screen.get_height() - 20 * (len(hud_lines) - i) - 10))

        else:  # heatmap mode: 新しく再生が進んだサンプルのみ累積描画(一時停止中・巻き戻し中は加算しない)
            if idx > last_heat_idx_accumulated:
                radius = max(8, args.radius)
                for k in range(last_heat_idx_accumulated + 1, idx + 1):
                    rk = rows[k]
                    if rk["cx"] is None or rk["cy"] is None:
                        continue
                    px, py = to_px(rk["cx"], rk["cy"], heat_surface)
                    s = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
                    for rr, aa in [(radius * 2, 10), (int(radius * 1.3), 18), (radius, 30)]:
                        pygame.draw.circle(s, (255, 80, 0, aa), (radius * 2, radius * 2), rr)
                    heat_surface.blit(s, (px - radius * 2, py - radius * 2), special_flags=pygame.BLEND_RGBA_ADD)
                last_heat_idx_accumulated = idx

            screen.fill((18, 18, 22))
            screen.blit(heat_surface, (0, 0))
            txt = font.render(
                "Heatmap Mode (Cumulative)  L: Return to Live  C: Clear  SPACE: Pause", True, (210, 210, 210)
            )
            screen.blit(txt, (10, screen.get_height() - 30))

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()