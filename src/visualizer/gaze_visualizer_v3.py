"""
gaze_visualizer_v3.py
----------------------
tobii_capture_stable_timestamped.py が出力する CSV (gaze_data_*.csv) と、
計測時に画面を録画した動画(mp4)を入力として、以下を行うツール。

  1. calibrate : 動画とCSVの時刻同期(オフセット)を対話的に決定し、sidecar json に保存する
  2. render    : 同期済みオフセットを使い、Bee Swarm / Scan Path / Heat Map を
                 動画に重ねた mp4 を出力する(モードごとに別ファイル)
  3. live      : (従来互換) CSV のみをリアルタイム再生するデバッグ用ビューワー

前提:
  - CSVの center_x / center_y / left_x / left_y / right_x / right_y は、
    録画時の画面解像度に対する正規化座標 (0.0〜1.0)。
  - 入力する動画は「視線計測対象だった画面」のスクリーン録画であることを前提とする。
    (シーンカメラ等、別の座標系で撮られた動画には対応していない。
     録画解像度と動画解像度が異なる場合のみ --rec-width/--rec-height で調整可能)
  - 動画とCSVは別プロセス・別クロックで記録されるため、両者の開始時刻のズレ(オフセット)は
    自動では分からない。まず `calibrate` を実行してオフセットを決定すること。

使い方:
  pip install pygame opencv-python numpy
  (音声を保持したい場合は ffmpeg が PATH 上にあること)

  # 1. 同期オフセットを決める(1回だけ)
  python gaze_visualizer_v3.py calibrate gaze_data_20260101.csv screen_record.mp4

  # 2. 3種類の可視化mp4を出力する
  python gaze_visualizer_v3.py render gaze_data_20260101.csv screen_record.mp4 \
      --modes beeswarm scanpath heatmap --out-dir ./output

  # 3. (従来互換) CSVのみのリアルタイム再生
  python gaze_visualizer_v3.py live gaze_data_20260101.csv
"""

import argparse
import bisect
import csv
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---- ディレクトリ再編対応: sys.path 設定 ------------------------------------
_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parent.parent if _THIS_FILE.parent.name != "src" else _THIS_FILE.parent
_PROJECT_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import cv2
import numpy as np

# ============================================================
# CSV 読み込み(ver2 と共通)
# ============================================================

def load_rows(csv_path: Path):
    """CSVを読み込み、再生/レンダリングに必要な情報のみを抽出したリストを返す。"""
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


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def to_px(nx, ny, w, h):
    px = clamp(nx, 0.0, 1.0) * w
    py = clamp(ny, 0.0, 1.0) * h
    return int(round(px)), int(round(py))


def gaze_index_at(times, target_t):
    """target_t(生のpc_time_sec)に対応する行インデックスを二分探索で求める。"""
    idx = bisect.bisect_right(times, target_t) - 1
    return idx  # -1 の場合は「まだ最初のサンプルより前」を意味する


# ============================================================
# 同期(calibrate)まわり
# ============================================================

def sync_file_path(video_path: Path) -> Path:
    return video_path.with_name(video_path.stem + "_sync.json")


def save_sync(video_path: Path, csv_path: Path, offset_sec: float):
    data = {
        "video": str(video_path.resolve()),
        "csv": str(csv_path.resolve()),
        "offset_sec": offset_sec,
        "note": (
            "offset_sec: 動画のフレーム0が、視線計測クロック(pc_time_sec)の何秒地点に"
            "対応するか。gaze_time = video_time + offset_sec"
        ),
    }
    path = sync_file_path(video_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_sync(video_path: Path):
    path = sync_file_path(video_path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return float(data.get("offset_sec", 0.0))
    except Exception:
        return None


# ============================================================
# calibrate サブコマンド: pygame での対話的オフセット調整
# ============================================================

def cmd_calibrate(args):
    import pygame  # calibrate/live でのみ必要なので遅延import

    csv_path = args.csv_path
    video_path = args.video_path

    rows = load_rows(csv_path)
    times = [r["t"] for r in rows]

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"エラー: 動画を開けませんでした: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if n_frames <= 0:
        sys.exit("エラー: 動画のフレーム数を取得できませんでした。")

    offset = args.initial_offset if args.initial_offset is not None else (load_sync(video_path) or 0.0)
    frame_idx = 0

    pygame.init()
    pygame.display.set_caption("Sync Calibration - " + video_path.name)
    win_w, win_h = min(vw, 1280), min(vh, 800)
    screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    font = pygame.font.SysFont(None, 24)
    clock = pygame.time.Clock()

    def read_frame_at(idx):
        idx = int(clamp(idx, 0, n_frames - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            return None
        return frame

    playing = False
    running = True
    print("操作方法: ←→=1フレーム移動  Shift+←→=1秒移動  ↑↓=オフセット±0.05s  "
          "Shift+↑↓=オフセット±0.5s  SPACE=再生/停止  ENTER=保存して終了  ESC=保存せず終了")

    while running:
        dt = clock.tick(60) / 1000.0
        mods = pygame.key.get_mods()
        shift = bool(mods & pygame.KMOD_SHIFT)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_RETURN:
                    path = save_sync(video_path, csv_path, offset)
                    print(f"オフセットを保存しました: {path} (offset_sec={offset:.3f})")
                    running = False
                elif event.key == pygame.K_SPACE:
                    playing = not playing
                elif event.key == pygame.K_RIGHT:
                    frame_idx += int(fps) if shift else 1
                elif event.key == pygame.K_LEFT:
                    frame_idx -= int(fps) if shift else 1
                elif event.key == pygame.K_UP:
                    offset += 0.5 if shift else 0.05
                elif event.key == pygame.K_DOWN:
                    offset -= 0.5 if shift else 0.05

        if playing:
            frame_idx += 1
        frame_idx = int(clamp(frame_idx, 0, n_frames - 1))

        frame_bgr = read_frame_at(frame_idx)
        if frame_bgr is None:
            running = False
            continue

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        surf = pygame.surfarray.make_surface(np.transpose(frame_rgb, (1, 0, 2)))
        surf = pygame.transform.smoothscale(surf, screen.get_size())
        screen.blit(surf, (0, 0))

        sw, sh = screen.get_size()
        t_video = frame_idx / fps
        t_gaze = t_video + offset
        idx = gaze_index_at(times, t_gaze)
        if 0 <= idx < len(rows):
            cur = rows[idx]
            if cur["cx"] is not None and cur["cy"] is not None:
                px, py = to_px(cur["cx"], cur["cy"], sw, sh)
                pygame.draw.circle(screen, (255, 60, 60), (px, py), 16)
                pygame.draw.circle(screen, (255, 255, 255), (px, py), 16, 2)
            status = f"gaze row {idx+1}/{len(rows)}  gaze_t={t_gaze:.3f}s"
        else:
            status = "gaze row: 範囲外(この時刻に視線データなし)"

        hud = [
            f"video frame {frame_idx+1}/{n_frames}  video_t={t_video:.3f}s  {'PLAYING' if playing else 'PAUSED'}",
            f"offset_sec = {offset:+.3f}   ({status})",
            "←→:1frame Shift+←→:1s  ↑↓:offset±0.05 Shift+↑↓:±0.5  SPACE:play ENTER:save ESC:cancel",
        ]
        for i, line in enumerate(hud):
            txt = font.render(line, True, (255, 255, 0))
            bg = pygame.Surface((txt.get_width() + 8, txt.get_height() + 4))
            bg.fill((0, 0, 0))
            bg.set_alpha(160)
            screen.blit(bg, (6, 6 + i * 26))
            screen.blit(txt, (10, 8 + i * 26))

        pygame.display.flip()

    cap.release()
    pygame.quit()


# ============================================================
# render サブコマンド: 3種の可視化を動画に焼き込む
# ============================================================

def make_gaussian_kernel(radius: int) -> np.ndarray:
    size = radius * 4 + 1
    ax = np.arange(size) - size // 2
    xx, yy = np.meshgrid(ax, ax)
    sigma = max(1.0, radius)
    kernel = np.exp(-(xx ** 2 + yy ** 2) / (2.0 * sigma ** 2))
    return kernel.astype(np.float32)


def add_splat(heat: np.ndarray, px: int, py: int, kernel: np.ndarray, weight: float = 1.0):
    kh, kw = kernel.shape
    r = kh // 2
    y0, y1 = py - r, py - r + kh
    x0, x1 = px - r, px - r + kw
    H, W = heat.shape
    sy0, sy1 = max(0, y0), min(H, y1)
    sx0, sx1 = max(0, x0), min(W, x1)
    if sy0 >= sy1 or sx0 >= sx1:
        return
    ky0, ky1 = sy0 - y0, sy1 - y0
    kx0, kx1 = sx0 - x0, sx1 - x0
    heat[sy0:sy1, sx0:sx1] += kernel[ky0:ky1, kx0:kx1] * weight


class HeatmapState:
    def __init__(self, w, h, radius, window_sec):
        self.heat = np.zeros((h, w), dtype=np.float32)
        self.kernel = make_gaussian_kernel(radius)
        self.window_sec = window_sec
        self.last_idx = -1

    def step(self, frame_bgr, rows, idx, fps, w, h):
        dt_frame = 1.0 / fps
        if self.window_sec > 0:
            decay = math.exp(-dt_frame / self.window_sec)
            self.heat *= decay
        if idx > self.last_idx:
            for k in range(max(0, self.last_idx + 1), idx + 1):
                rk = rows[k]
                if rk["cx"] is None or rk["cy"] is None:
                    continue
                px, py = to_px(rk["cx"], rk["cy"], w, h)
                add_splat(self.heat, px, py, self.kernel, weight=1.0)
            self.last_idx = idx

        peak = self.heat.max()
        ref = max(peak, 0.6)  # 立ち上がり初期のチラつき防止のための下限
        norm = np.clip(self.heat / ref, 0.0, 1.0)
        heat_u8 = (norm * 255).astype(np.uint8)
        colored = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)
        alpha = (norm * 0.55)[:, :, None]
        out = (frame_bgr.astype(np.float32) * (1 - alpha) + colored.astype(np.float32) * alpha)
        return out.astype(np.uint8)


class ScanpathState:
    def __init__(self, window_sec, max_points=200):
        self.window_sec = window_sec
        self.max_points = max_points
        self.points = []  # (t, px, py)
        self.last_idx = -1

    def step(self, frame_bgr, rows, idx, w, h, t_gaze):
        if idx > self.last_idx:
            for k in range(max(0, self.last_idx + 1), idx + 1):
                rk = rows[k]
                if rk["cx"] is None or rk["cy"] is None:
                    continue
                px, py = to_px(rk["cx"], rk["cy"], w, h)
                self.points.append((rk["t"], px, py))
            self.last_idx = idx

        if self.window_sec > 0:
            cutoff = t_gaze - self.window_sec
            self.points = [p for p in self.points if p[0] >= cutoff]

        pts = self.points
        if len(pts) > self.max_points:
            step = len(pts) // self.max_points
            pts = pts[::max(1, step)]

        overlay = frame_bgr.copy()
        n = len(pts)
        for i in range(1, n):
            t0, x0, y0 = pts[i - 1]
            t1, x1, y1 = pts[i]
            age = 1.0 - (i / max(1, n - 1))  # 0=最新, 1=最古
            color = (
                int(60 + 140 * age),      # B
                int(60),                  # G
                int(255 - 140 * age),     # R
            )
            cv2.line(overlay, (x0, y0), (x1, y1), color, 2, lineType=cv2.LINE_AA)
        for i, (t, x, y) in enumerate(pts):
            age = 1.0 - (i / max(1, n - 1)) if n > 1 else 0.0
            r = 3 + int(4 * (1.0 - age))
            color = (int(60 + 140 * age), 60, int(255 - 140 * age))
            cv2.circle(overlay, (x, y), r, color, -1, lineType=cv2.LINE_AA)
        if n > 0:
            cx, cy = pts[-1][1], pts[-1][2]
            cv2.circle(overlay, (cx, cy), 10, (255, 255, 255), 2, lineType=cv2.LINE_AA)

        out = cv2.addWeighted(overlay, 0.75, frame_bgr, 0.25, 0)
        return out


class BeeSwarmState:
    """左目・右目・中心点を個別に、短いトレイル付きで表示する。"""

    def __init__(self, trail_sec=0.3):
        self.trail_sec = trail_sec
        self.last_idx = -1
        self.history = []  # (t, cx,cy, lx,ly,lvalid, rx,ry,rvalid)

    def step(self, frame_bgr, rows, idx, w, h, t_gaze):
        if idx > self.last_idx:
            for k in range(max(0, self.last_idx + 1), idx + 1):
                rk = rows[k]
                self.history.append(rk)
            self.last_idx = idx

        cutoff = t_gaze - self.trail_sec
        self.history = [r for r in self.history if r["t"] >= cutoff]

        overlay = frame_bgr.copy()

        def draw_channel(key_x, key_y, valid_key, color):
            n = len(self.history)
            for i, r in enumerate(self.history):
                if valid_key is not None and not r.get(valid_key, True):
                    continue
                x, y = r.get(key_x), r.get(key_y)
                if x is None or y is None:
                    continue
                age = 1.0 - (i / max(1, n - 1)) if n > 1 else 0.0
                px, py = to_px(x, y, w, h)
                radius = 3 + int(5 * (1.0 - age))
                alpha_scale = 1.0 - 0.7 * age
                c = tuple(int(v * alpha_scale) for v in color)
                cv2.circle(overlay, (px, py), radius, c, -1, lineType=cv2.LINE_AA)

        draw_channel("lx", "ly", "lvalid", (90, 220, 90))   # 左目: 緑 (BGR)
        draw_channel("rx", "ry", "rvalid", (60, 210, 240))  # 右目: 黄 (BGR)
        draw_channel("cx", "cy", None, (60, 60, 255))       # 中心: 赤 (BGR)

        out = cv2.addWeighted(overlay, 0.85, frame_bgr, 0.15, 0)
        return out


def ffmpeg_remux_audio(silent_video: Path, source_video: Path, final_out: Path) -> bool:
    """cv2で書き出した無音mp4に、元動画の音声を再合成する。ffmpeg が無ければFalseを返す。"""
    if shutil.which("ffmpeg") is None:
        return False
    cmd = [
        "ffmpeg", "-y",
        "-i", str(silent_video),
        "-i", str(source_video),
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-shortest",
        str(final_out),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0 and final_out.exists()


def cmd_render(args):
    csv_path = args.csv_path
    video_path = args.video_path
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(csv_path)
    times = [r["t"] for r in rows]

    if args.offset is not None:
        offset = args.offset
    else:
        offset = load_sync(video_path)
        if offset is None:
            sys.exit(
                "エラー: オフセットが未指定で、sync jsonも見つかりません。\n"
                "先に `calibrate` サブコマンドを実行するか、--offset を指定してください。"
            )
    print(f"[render] 使用するオフセット: {offset:+.3f} s")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"エラー: 動画を開けませんでした: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if args.rec_width:
        w_map, h_map = args.rec_width, args.rec_height or h
    else:
        w_map, h_map = w, h

    modes = args.modes
    states = {}
    writers = {}
    tmp_paths = {}
    final_paths = {}

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    stem = video_path.stem
    for mode in modes:
        if mode == "heatmap":
            states[mode] = HeatmapState(w_map, h_map, args.radius, args.heatmap_window)
        elif mode == "scanpath":
            states[mode] = ScanpathState(args.scanpath_window)
        elif mode == "beeswarm":
            states[mode] = BeeSwarmState(args.beeswarm_trail)
        tmp_path = out_dir / f"_tmp_{stem}_{mode}.mp4"
        final_path = out_dir / f"{stem}_{mode}.mp4"
        tmp_paths[mode] = tmp_path
        final_paths[mode] = final_path
        writers[mode] = cv2.VideoWriter(str(tmp_path), fourcc, fps, (w, h))

    frame_idx = 0
    t_start = time.time()
    print(f"[render] 動画: {w}x{h} @ {fps:.2f}fps, {n_frames} frames / モード: {modes}")

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_video = frame_idx / fps
        t_gaze = t_video + offset
        idx = gaze_index_at(times, t_gaze)
        idx_clamped = int(clamp(idx, 0, len(rows) - 1))

        for mode in modes:
            st = states[mode]
            if idx < 0:
                # まだ視線データ開始前 -> 素の映像フレームのみ書き出す
                out_frame = frame
            elif mode == "heatmap":
                out_frame = st.step(frame, rows, idx_clamped, fps, w_map, h_map)
            elif mode == "scanpath":
                out_frame = st.step(frame, rows, idx_clamped, w_map, h_map, t_gaze)
            elif mode == "beeswarm":
                out_frame = st.step(frame, rows, idx_clamped, w_map, h_map, t_gaze)
            else:
                out_frame = frame
            if (w_map, h_map) != (w, h):
                out_frame = cv2.resize(out_frame, (w, h))
            writers[mode].write(out_frame)

        frame_idx += 1
        if frame_idx % 100 == 0 or frame_idx == n_frames:
            pct = 100.0 * frame_idx / max(1, n_frames)
            elapsed = time.time() - t_start
            print(f"\r[render] {frame_idx}/{n_frames} ({pct:5.1f}%)  経過 {elapsed:5.1f}s", end="", flush=True)

    print()
    cap.release()
    for mode in modes:
        writers[mode].release()

    for mode in modes:
        tmp_path = tmp_paths[mode]
        final_path = final_paths[mode]
        if args.no_audio:
            tmp_path.replace(final_path)
            print(f"[render] 出力(無音): {final_path}")
            continue
        ok = ffmpeg_remux_audio(tmp_path, video_path, final_path)
        if ok:
            tmp_path.unlink(missing_ok=True)
            print(f"[render] 出力(音声付き, H.264): {final_path}")
        else:
            tmp_path.replace(final_path)
            print(f"[render] 警告: ffmpegでの音声合成に失敗/未検出のため無音のまま出力: {final_path}")


# ============================================================
# live サブコマンド: (従来互換) CSVのみのリアルタイム再生
# ============================================================

def cmd_live(args):
    import pygame

    rows = load_rows(args.csv_path)
    duration = rows[-1]["t"] - rows[0]["t"]
    t0 = rows[0]["t"]
    times = [r["t"] for r in rows]

    pygame.init()
    pygame.display.set_caption(f"Gaze Replay - {args.csv_path.name}")
    info = pygame.display.Info()
    win_w = args.width or min(info.current_w, 1280)
    win_h = args.height or min(info.current_h, 800)
    screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    font = pygame.font.SysFont(None, 22)
    clock = pygame.time.Clock()

    paused = False
    speed = 1.0
    play_t = 0.0

    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key == pygame.K_r:
                    play_t = 0.0

        if not paused:
            play_t += dt * speed
            if play_t > duration:
                play_t = duration
                paused = True

        target_abs_t = t0 + play_t
        idx = gaze_index_at(times, target_abs_t)
        idx = int(clamp(idx, 0, len(rows) - 1))
        cur = rows[idx]

        screen.fill((18, 18, 22))
        sw, sh = screen.get_size()
        if cur["cx"] is not None and cur["cy"] is not None:
            px, py = to_px(cur["cx"], cur["cy"], sw, sh)
            pygame.draw.circle(screen, (255, 60, 60), (px, py), 14)
            pygame.draw.circle(screen, (255, 255, 255), (px, py), 14, 2)

        txt = font.render(
            f"Time: {play_t:6.2f}/{duration:6.2f}s  {'PAUSED' if paused else 'PLAYING'}  "
            "SPACE:pause R:restart Q:quit", True, (210, 210, 210))
        screen.blit(txt, (10, sh - 26))
        pygame.display.flip()

    pygame.quit()


# ============================================================
# argparse
# ============================================================

def build_arg_parser():
    p = argparse.ArgumentParser(description="Tobii gaze CSV + 動画 合成ツール")
    sub = p.add_subparsers(dest="command", required=True)

    p_cal = sub.add_parser("calibrate", help="動画とCSVの時刻オフセットを対話的に決定する")
    p_cal.add_argument("csv_path", type=Path)
    p_cal.add_argument("video_path", type=Path)
    p_cal.add_argument("--initial-offset", type=float, default=None, help="初期オフセット(秒)")
    p_cal.set_defaults(func=cmd_calibrate)

    p_ren = sub.add_parser("render", help="Bee Swarm/Scan Path/Heat Map を動画に焼き込む")
    p_ren.add_argument("csv_path", type=Path)
    p_ren.add_argument("video_path", type=Path)
    p_ren.add_argument("--modes", nargs="+", choices=["beeswarm", "scanpath", "heatmap"],
                        default=["beeswarm", "scanpath", "heatmap"], help="出力するモード(各々別ファイル)")
    p_ren.add_argument("--offset", type=float, default=None, help="手動オフセット(秒)。未指定ならsync jsonを使用")
    p_ren.add_argument("--out-dir", type=Path, default=_PROJECT_ROOT / "data" / "processed", help="出力先ディレクトリ (既定: data/processed)")
    p_ren.add_argument("--radius", type=int, default=14, help="ヒートマップ用ガウシアン半径(px)")
    p_ren.add_argument("--heatmap-window", type=float, default=3.0, help="ヒートマップの移動ウィンドウ秒数(0で全累積)")
    p_ren.add_argument("--scanpath-window", type=float, default=5.0, help="スキャンパスの移動ウィンドウ秒数(0で全累積)")
    p_ren.add_argument("--beeswarm-trail", type=float, default=0.3, help="Bee Swarmの軌跡表示秒数")
    p_ren.add_argument("--rec-width", type=int, default=None, help="録画時解像度の幅(動画解像度と異なる場合)")
    p_ren.add_argument("--rec-height", type=int, default=None, help="録画時解像度の高さ")
    p_ren.add_argument("--no-audio", action="store_true", help="音声合成をスキップする(ffmpeg不要)")
    p_ren.set_defaults(func=cmd_render)

    p_live = sub.add_parser("live", help="(従来互換) CSVのみのリアルタイム再生")
    p_live.add_argument("csv_path", type=Path)
    p_live.add_argument("--width", type=int, default=None)
    p_live.add_argument("--height", type=int, default=None)
    p_live.set_defaults(func=cmd_live)

    return p


def main():
    args = build_arg_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
