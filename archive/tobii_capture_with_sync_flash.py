# -*- coding: utf-8 -*-
"""
tobii_capture_with_sync_flash.py

Tobii Pro Spark 視線データ取得 + OBS同期用フラッシュマーカー表示プログラム

準拠要件:
  - gemini-code-1783913719133.md (初版要件定義)
  - valorant_eye_tracking_requirements_revised.md (VALORANT追記版・優先仕様)

ベース: tobii_capture_stable_timestamped.py の視線取得ロジックを継承

--------------------------------------------------------------------------
【設計上の重要な前提・実装者からの申し送り】

1. 同期フラッシュは「全画面」ではなく、選択ディスプレイ中央の小さな
   正方形ウィンドウ（既定150px角）として実装した。
   理由: VALORANTはフルスクリーン/フルスクリーンウィンドウで動作する
   ことが多く、全画面オーバーレイは入力・描画パイプラインへの干渉や
   フォーカス喪失によるゲーム側の予期しない挙動（最小化等）のリスクが
   高い。中央の小窓の方が実運用上安全。

2. 【要検証・最重要】OBSのキャプチャ方式が「ゲームキャプチャ」の場合、
   このフラッシュウィンドウは録画に映らない可能性が高い。
   ゲームキャプチャはVALORANTのプロセス/ウィンドウを直接フックするため、
   デスクトップ上の別ウィンドウ（本フラッシュ窓）は合成されず映像に
   含まれないことがある。
   → 本方式は OBS 側が「ディスプレイキャプチャ」（画面全体をキャプチャ）
     であることを前提にしている。ゲームキャプチャを使う場合は、
     別の同期方式（例: オーディオビープ音 + OBSのオーディオ波形同期、
     または実験用に一時的にディスプレイキャプチャへ切り替える等）を
     検討すること。運用前に必ずOBS側の設定を確認してください。

3. キー入力検出には `keyboard` パッケージ（OSレベルのグローバルフック）
   を使用する。理由: フラッシュ用ウィンドウがフォーカスを持っていなくても
   SPACE/E/ESCを検出できるようにするため（VALORANT側にフォーカスが
   あってもトリガー可能）。
   - Windows: 通常は管理者権限不要で動作する（環境により異なる場合あり）。
   - Linux/Mac: 権限昇格が必要な場合がある。未検証。

4. フラッシュウィンドウはWindows専用の非アクティブ化処理
   (WS_EX_NOACTIVATE / SetWindowPos TOPMOST) を ctypes で行っている。
   Windows以外のOSでは、この非アクティブ化・非表示制御はベストエフォート
   となり、フラッシュ窓がフォーカスを奪う可能性がある（要検証・警告ログ出力）。

5. 「1フレーム/2フレーム」の表示時間は software sleep ベースであり、
   OSの描画パイプライン・GPU・ディスプレイの垂直同期に対して
   フレーム完全一致を保証しない。これは要件定義書 5章の記述
   （「完全なフレーム一致ではなく、フレーム単位で評価可能な誤差範囲を
   もって同期精度を判定する」）とも整合する設計判断であり、
   その代わりに要求された全ての時刻（キー受信/描画要求/描画提示/
   視線記録開始/初回サンプル受信）を個別にログする。

必要パッケージ:
    pip install tobii-research pygame screeninfo keyboard
    (Windowsでリフレッシュレートを取得したい場合、任意で)
    pip install pywin32
--------------------------------------------------------------------------
"""

import argparse
import csv
import ctypes
import json
import logging
import platform
import queue
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# ---- 必須外部パッケージの遅延・安全なインポート ----------------------------
_MISSING_PACKAGES = []

try:
    import tobii_research as tr
except ImportError:
    tr = None
    _MISSING_PACKAGES.append("tobii-research")

try:
    import pygame
except ImportError:
    pygame = None
    _MISSING_PACKAGES.append("pygame")

try:
    import screeninfo
except ImportError:
    screeninfo = None
    _MISSING_PACKAGES.append("screeninfo")

try:
    import keyboard
except ImportError:
    keyboard = None
    _MISSING_PACKAGES.append("keyboard")

if _MISSING_PACKAGES:
    print("以下のパッケージが見つかりません。インストールしてください:")
    print(f"    pip install {' '.join(_MISSING_PACKAGES)}")
    sys.exit(1)


IS_WINDOWS = platform.system() == "Windows"

SCRIPT_VERSION = "2.0.0-sync-flash"

# ============================================================================
# 定数・既定値
# ============================================================================

DEFAULT_OUTPUT_DIR = Path("./gaze_sessions")
BUFFER_SIZE = 200
DEFAULT_TARGET_FPS = 60
DEFAULT_FLASH_FRAMES = 1
DEFAULT_FLASH_SIZE_PX = 150
GAP_ANOMALY_MULTIPLIER = 3.0  # サンプル間隔がこの倍数を超えたら欠落として記録

FIELDNAMES = [
    "wall_timestamp_local",
    "wall_timestamp_utc",
    "pc_time_sec",
    "device_time_stamp_us",
    "system_time_stamp_us",
    "left_x",
    "left_y",
    "right_x",
    "right_y",
    "center_x",
    "center_y",
    "left_gaze_point_validity",
    "right_gaze_point_validity",
    "gaze_missing",
]


# ============================================================================
# ロギング
# ============================================================================

def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ============================================================================
# ディスプレイ選択 (F-1)
# ============================================================================

def list_displays():
    monitors = screeninfo.get_monitors()
    if not monitors:
        logging.error("接続されているディスプレイが検出できませんでした。")
        sys.exit(1)
    return monitors


def get_refresh_rate(monitor) -> "int | None":
    """ベストエフォートでリフレッシュレートを取得する。取得できなければNone。"""
    if not IS_WINDOWS:
        return None
    try:
        import win32api  # pywin32 (任意依存)
        device_name = getattr(monitor, "name", None)
        if not device_name:
            return None
        settings = win32api.EnumDisplaySettings(device_name, -1)
        return int(settings.DisplayFrequency)
    except Exception as e:
        logging.warning(f"リフレッシュレート取得に失敗（無視して続行）: {e}")
        return None


def select_display(monitors):
    print("\n=== 接続ディスプレイ一覧 ===")
    for i, m in enumerate(monitors, start=1):
        name = m.name or f"display{i}"
        print(f"  {i}: {name}  ({m.width}x{m.height} @ ({m.x},{m.y}))"
              f"{'  [primary]' if getattr(m, 'is_primary', False) else ''}")

    while True:
        raw = input(f"測定対象ディスプレイの番号を入力してください (1-{len(monitors)}): ").strip()
        if not raw.isdigit():
            print("数値を入力してください。")
            continue
        idx = int(raw)
        if 1 <= idx <= len(monitors):
            selected = monitors[idx - 1]
            logging.info(
                f"ディスプレイ選択: index={idx} name={selected.name} "
                f"resolution={selected.width}x{selected.height} pos=({selected.x},{selected.y})"
            )
            return idx, selected
        print("選択ミス: 範囲外の番号です。もう一度入力してください。")


# ============================================================================
# 同期フラッシュウィンドウ (F-3)
# ============================================================================

class SyncFlashWindow:
    """選択ディスプレイ中央に配置される、同期用の小さな白フラッシュウィンドウ。

    通常時は非表示・非アクティブ化されており、flash() 呼び出し時のみ
    一瞬白色を表示してすぐ非表示に戻る。
    """

    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4

    def __init__(self, monitor, size_px: int, target_fps: int):
        self.monitor = monitor
        self.size_px = size_px
        self.target_fps = target_fps
        self.hwnd = None
        self.surface = None
        self._init_window()

    def _init_window(self):
        import os

        pos_x = self.monitor.x + self.monitor.width // 2 - self.size_px // 2
        pos_y = self.monitor.y + self.monitor.height // 2 - self.size_px // 2
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos_x},{pos_y}"

        pygame.display.init()
        self.surface = pygame.display.set_mode((self.size_px, self.size_px), pygame.NOFRAME)
        pygame.display.set_caption("SYNC_FLASH_MARKER")
        self.surface.fill((0, 0, 0))
        pygame.display.flip()

        self._apply_noactivate_topmost()
        self._hide()

    def _apply_noactivate_topmost(self):
        if not IS_WINDOWS:
            logging.warning(
                "非Windows環境のため、フラッシュウィンドウの非アクティブ化/"
                "最前面固定はスキップされます（フォーカスを奪う可能性があります）。"
            )
            return
        try:
            wm_info = pygame.display.get_wm_info()
            hwnd = wm_info["window"]
            self.hwnd = hwnd
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, self.GWL_EXSTYLE,
                ex_style | self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW,
            )
            user32.SetWindowPos(
                hwnd, self.HWND_TOPMOST, 0, 0, 0, 0,
                self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE,
            )
        except Exception as e:
            logging.warning(f"トップモスト/非アクティブ化の設定に失敗しました: {e}")

    def _show(self):
        if self.hwnd:
            try:
                ctypes.windll.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
            except Exception as e:
                logging.warning(f"ウィンドウ表示に失敗しました: {e}")

    def _hide(self):
        if self.hwnd:
            try:
                ctypes.windll.user32.ShowWindow(self.hwnd, self.SW_HIDE)
            except Exception as e:
                logging.warning(f"ウィンドウ非表示化に失敗しました: {e}")

    def flash(self, frames: int, color=(255, 255, 255)) -> dict:
        """指定フレーム数だけ白色を表示し、各種時刻を記録して返す。"""
        timestamps = {}
        self._show()

        frame_interval = 1.0 / self.target_fps
        for i in range(frames):
            t_req = time.perf_counter()
            self.surface.fill(color)
            pygame.display.flip()
            t_present = time.perf_counter()
            if i == 0:
                timestamps["draw_request_perf"] = t_req
                timestamps["draw_present_perf"] = t_present
            elapsed = time.perf_counter() - t_req
            remaining = frame_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self.surface.fill((0, 0, 0))
        pygame.display.flip()
        self._hide()
        return timestamps

    def close(self):
        try:
            pygame.display.quit()
        except Exception:
            pass


# ============================================================================
# 視線データ取得 (F-4)
# ============================================================================

class GazeRecorder:
    def __init__(self, csv_path: Path, eyetracker, target_fps: int):
        self.csv_path = csv_path
        self.eyetracker = eyetracker
        self.target_fps = target_fps

        self.buffer = deque()
        self.buffer_lock = threading.Lock()
        self.writer_file = None
        self.writer = None

        self.start_perf = None
        self.total_samples = 0
        self.missing_samples = 0

        self._last_pc_time = None
        self.anomalies = queue.Queue()

        self.first_sample_event = threading.Event()
        self.first_sample_info = {}

    def open_writer(self):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.writer_file = open(self.csv_path, "w", newline="", encoding="utf-8-sig")
        self.writer = csv.DictWriter(self.writer_file, fieldnames=FIELDNAMES)
        self.writer.writeheader()
        self.writer_file.flush()

    def close_writer(self):
        self.flush_buffer()
        if self.writer_file is not None:
            self.writer_file.close()
        self.writer_file = None
        self.writer = None

    def flush_buffer(self):
        with self.buffer_lock:
            if not self.buffer or self.writer is None:
                return
            self.writer.writerows(self.buffer)
            self.writer_file.flush()
            self.buffer.clear()

    @staticmethod
    def _safe_xy(value):
        if value is None:
            return None, None
        try:
            return value[0], value[1]
        except Exception:
            return None, None

    @staticmethod
    def _average_xy(left_x, left_y, left_valid, right_x, right_y, right_valid):
        pts = []
        if left_valid and left_x is not None and left_y is not None:
            pts.append((left_x, left_y))
        if right_valid and right_x is not None and right_y is not None:
            pts.append((right_x, right_y))
        if not pts:
            return None, None
        x = sum(p[0] for p in pts) / len(pts)
        y = sum(p[1] for p in pts) / len(pts)
        return x, y

    @staticmethod
    def _fmt(v):
        """欠測値を明示的に 'NaN' 文字列として記録する（空欄との混同を避ける）。"""
        return "NaN" if v is None else v

    def gaze_data_callback(self, gaze_data):
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        pc_time_sec = time.perf_counter() - self.start_perf

        # サンプル間ギャップ検知（異常系: 測定中のデータ欠落の早期発見用）
        if self._last_pc_time is not None:
            interval = 1.0 / self.target_fps
            delta = pc_time_sec - self._last_pc_time
            if delta > interval * GAP_ANOMALY_MULTIPLIER:
                self.anomalies.put({
                    "type": "sample_gap",
                    "pc_time_sec": round(pc_time_sec, 6),
                    "gap_sec": round(delta, 6),
                })
        self._last_pc_time = pc_time_sec

        left_x, left_y = self._safe_xy(gaze_data.get("left_gaze_point_on_display_area"))
        right_x, right_y = self._safe_xy(gaze_data.get("right_gaze_point_on_display_area"))
        left_valid = gaze_data.get("left_gaze_point_validity")
        right_valid = gaze_data.get("right_gaze_point_validity")

        center_x, center_y = self._average_xy(
            left_x, left_y, left_valid, right_x, right_y, right_valid,
        )
        gaze_missing = center_x is None or center_y is None

        row = {
            "wall_timestamp_local": now_local.isoformat(timespec="milliseconds"),
            "wall_timestamp_utc": now_utc.isoformat(timespec="milliseconds"),
            "pc_time_sec": round(pc_time_sec, 6),
            "device_time_stamp_us": gaze_data.get("device_time_stamp"),
            "system_time_stamp_us": gaze_data.get("system_time_stamp"),
            "left_x": self._fmt(left_x),
            "left_y": self._fmt(left_y),
            "right_x": self._fmt(right_x),
            "right_y": self._fmt(right_y),
            "center_x": self._fmt(center_x),
            "center_y": self._fmt(center_y),
            "left_gaze_point_validity": left_valid,
            "right_gaze_point_validity": right_valid,
            "gaze_missing": gaze_missing,
        }

        if not self.first_sample_event.is_set():
            self.first_sample_info = {
                "pc_time_sec": round(pc_time_sec, 6),
                "wall_timestamp_local": row["wall_timestamp_local"],
                "wall_timestamp_utc": row["wall_timestamp_utc"],
            }
            self.first_sample_event.set()

        self.total_samples += 1
        if gaze_missing:
            self.missing_samples += 1

        with self.buffer_lock:
            self.buffer.append(row)
            should_flush = len(self.buffer) >= BUFFER_SIZE
        if should_flush:
            self.flush_buffer()

    def start(self):
        self.open_writer()
        self.start_perf = time.perf_counter()
        self.eyetracker.subscribe_to(
            tr.EYETRACKER_GAZE_DATA,
            self.gaze_data_callback,
            as_dictionary=True,
        )

    def stop(self):
        try:
            self.eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, self.gaze_data_callback)
        except Exception as e:
            logging.warning(f"unsubscribe時に例外が発生しました: {e}")
        self.close_writer()

    def drain_anomalies(self):
        result = []
        while True:
            try:
                result.append(self.anomalies.get_nowait())
            except queue.Empty:
                break
        return result

    def missing_rate(self):
        if self.total_samples == 0:
            return None
        return round(self.missing_samples / self.total_samples, 4)


# ============================================================================
# キャリブレーション
# ============================================================================

def run_calibration(eyetracker) -> str:
    calibration = tr.ScreenBasedCalibration(eyetracker)
    calibration.enter_calibration_mode()
    points_to_calibrate = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.5), (0.1, 0.9), (0.9, 0.9)]
    for x, y in points_to_calibrate:
        print(f"  キャリブレーションポイントを注視してください: {(x, y)}")
        time.sleep(1.0)
        calibration.collect_data(x, y)
    result = calibration.compute_and_apply()
    calibration.leave_calibration_mode()
    logging.info(f"キャリブレーション結果: {result.status}")
    return str(result.status)


# ============================================================================
# 実験条件メタデータ (F-8 相当、任意入力・Enterでスキップ可)
# ============================================================================

def prompt_experimental_conditions() -> dict:
    print("\n=== 実験条件の記録（任意項目。不明な場合はEnterでスキップ） ===")
    fields = [
        ("viewing_distance_cm", "視距離 (cm)"),
        ("seated_posture", "座位姿勢の備考"),
        ("chair_monitor_distance_cm", "椅子とモニタの距離 (cm)"),
        ("head_fixation", "頭部固定の有無 (有/無)"),
        ("monitor_size_inch", "モニタサイズ (インチ)"),
        ("monitor_resolution", "モニタ解像度 (例: 1920x1080)"),
        ("crosshair_setting", "クロスヘア設定"),
        ("sensitivity", "感度設定"),
        ("play_mode", "プレイモード (例: 訓練場/半統制課題/自由対戦)"),
        ("recording_condition", "録画条件の備考"),
    ]
    conditions = {}
    for key, label in fields:
        val = input(f"  {label}: ").strip()
        conditions[key] = val if val else None
    return conditions


def confirm_obs_recording() -> bool:
    while True:
        ans = input("\nOBSの録画は開始されていますか？ (y/n): ").strip().lower()
        if ans in ("y", "yes"):
            logging.info("OBS録画開始を操作者が確認済み。")
            return True
        if ans in ("n", "no"):
            logging.warning("OBS録画が未開始と回答されました。録画を開始してから再実行してください。")
            return False
        print("y または n を入力してください。")


# ============================================================================
# キーボード制御 (F-2, 異常系: スペースキー多重入力)
# ============================================================================

class TriggerController:
    """SPACE/ESC/E のグローバルホットキーを管理する。"""

    def __init__(self):
        self.started = False
        self.trigger_event = threading.Event()
        self.abort_before_start_event = threading.Event()
        self.stop_no_marker_event = threading.Event()
        self.stop_with_marker_event = threading.Event()
        self.anomalies = queue.Queue()
        self._key_event_perf = None

        keyboard.add_hotkey("space", self._on_space)
        keyboard.add_hotkey("esc", self._on_esc)
        keyboard.add_hotkey("e", self._on_e)

    def _on_space(self):
        if self.started:
            self.anomalies.put({"type": "duplicate_space_press", "perf_time": time.perf_counter()})
            logging.warning("測定開始後のスペースキー多重入力を検出しました（無視）。")
            return
        self.started = True
        self._key_event_perf = time.perf_counter()
        self.trigger_event.set()

    def _on_esc(self):
        if not self.started:
            self.abort_before_start_event.set()
        else:
            self.stop_no_marker_event.set()

    def _on_e(self):
        if self.started:
            self.stop_with_marker_event.set()

    def key_event_perf_time(self):
        return self._key_event_perf

    def teardown(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass


# ============================================================================
# 引数
# ============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="Tobii視線データ取得 + OBS同期フラッシュ")
    p.add_argument("--subject", default="NA", help="被験者ID")
    p.add_argument("--trial", default="1", help="試行ID")
    p.add_argument("--condition", default="default", help="条件ID")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="出力先ディレクトリ")
    p.add_argument("--flash-frames", type=int, default=DEFAULT_FLASH_FRAMES, choices=[1, 2],
                    help="フラッシュ表示フレーム数 (1 or 2)")
    p.add_argument("--flash-size", type=int, default=DEFAULT_FLASH_SIZE_PX, help="フラッシュ窓の一辺(px)")
    p.add_argument("--target-fps", type=int, default=DEFAULT_TARGET_FPS, help="想定フレームレート(Hz)")
    p.add_argument("--calibrate", action="store_true", help="キャリブレーションを実施する")
    p.add_argument("--max-duration", type=float, default=None,
                    help="安全装置としての最大記録時間(秒)。指定なしなら手動停止のみ。")
    p.add_argument("--skip-conditions-prompt", action="store_true", help="実験条件の対話入力をスキップする")
    return p.parse_args()


# ============================================================================
# メイン処理
# ============================================================================

def main():
    args = parse_args()

    session_id = uuid.uuid4().hex[:8]
    file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"gaze_{args.subject}_{args.trial}_{args.condition}_{file_stamp}"
    output_dir = Path(args.output_dir)
    csv_path = output_dir / f"{base_name}.csv"
    meta_path = output_dir / f"{base_name}_meta.json"
    log_path = output_dir / f"{base_name}_log.txt"

    setup_logging(log_path)
    logging.info(f"=== セッション開始 session_id={session_id} script_version={SCRIPT_VERSION} ===")
    logging.info(f"実行環境: OS={platform.platform()} Python={platform.python_version()}")

    timing_events = []

    def log_event(name, perf_time=None, wall_local=None, wall_utc=None):
        entry = {
            "event": name,
            "perf_counter_sec": perf_time if perf_time is not None else time.perf_counter(),
            "wall_local": wall_local or datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "wall_utc": wall_utc or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        timing_events.append(entry)
        logging.info(f"[EVENT] {name} perf={entry['perf_counter_sec']:.6f}")

    # ---- OBS録画確認 --------------------------------------------------
    obs_confirmed = confirm_obs_recording()
    if not obs_confirmed:
        logging.error("OBS未確認のため中止します。")
        sys.exit(1)

    # ---- ディスプレイ選択 (F-1) ----------------------------------------
    monitors = list_displays()
    display_index, selected_monitor = select_display(monitors)
    refresh_rate = get_refresh_rate(selected_monitor)
    if refresh_rate is None:
        logging.warning(
            "リフレッシュレートを自動取得できませんでした（未検出 or 非Windows/pywin32未導入）。"
            "メタデータには null として記録されます。手動で確認・記入してください。"
        )

    # ---- アイトラッカー検出 --------------------------------------------
    found = tr.find_all_eyetrackers()
    if not found:
        logging.error("Tobiiアイトラッカーが見つかりませんでした。接続を確認してください。")
        sys.exit(1)
    eyetracker = found[0]
    logging.info(f"接続デバイス: {eyetracker.device_name} / モデル: {eyetracker.model}")

    # ---- キャリブレーション ---------------------------------------------
    calibration_status = None
    if args.calibrate:
        print("\nキャリブレーションを開始します。")
        calibration_status = run_calibration(eyetracker)
    else:
        logging.warning("キャリブレーション未実施で測定を開始します（--calibrate 未指定）。")

    # ---- 実験条件 ---------------------------------------------------
    conditions = {} if args.skip_conditions_prompt else prompt_experimental_conditions()

    # ---- フラッシュウィンドウ準備 (F-3) ---------------------------------
    flash_window = SyncFlashWindow(
        monitor=selected_monitor, size_px=args.flash_size, target_fps=args.target_fps,
    )

    # ---- 記録準備 -------------------------------------------------------
    recorder = GazeRecorder(csv_path=csv_path, eyetracker=eyetracker, target_fps=args.target_fps)

    # ---- トリガー待機 (F-2) ----------------------------------------------
    controller = TriggerController()
    print("\n待機中... スペースキーで測定開始 / ESCで中断")

    try:
        while not controller.trigger_event.is_set() and not controller.abort_before_start_event.is_set():
            time.sleep(0.001)

        if controller.abort_before_start_event.is_set():
            logging.info("測定開始前にESCが押されたため中止します。")
            log_event("aborted_before_start")
            return

        # --- キー受信時刻 ---
        t_key = controller.key_event_perf_time()
        log_event("key_received", perf_time=t_key)

        # --- 開始同期フラッシュ ---
        # flash_ts = flash_window.flash(frames=args.flash_frames)
        flash_ts = flash_window.flash(frames=args.flash_frames, color=(255, 0, 127))
        log_event("flash_draw_request", perf_time=flash_ts.get("draw_request_perf"))
        log_event("flash_draw_present", perf_time=flash_ts.get("draw_present_perf"))

        # --- 視線記録開始 ---
        recorder.start()
        log_event("gaze_subscribe_call", perf_time=recorder.start_perf)
        print("測定中... 'e' で終了マーカー付き終了 / ESC でマーカーなし即時終了")

        # --- 記録ループ ---
        start_wall = time.perf_counter()
        while True:
            if controller.stop_no_marker_event.is_set():
                break
            if controller.stop_with_marker_event.is_set():
                break
            if args.max_duration is not None and (time.perf_counter() - start_wall) >= args.max_duration:
                logging.warning(f"max_duration={args.max_duration}s に到達したため自動停止します。")
                break
            if recorder.first_sample_event.is_set() and "gaze_first_sample" not in [e["event"] for e in timing_events]:
                log_event("gaze_first_sample", perf_time=None)
            time.sleep(0.01)

        # --- 終了マーカー（任意） ---
        if controller.stop_with_marker_event.is_set():
            end_ts = flash_window.flash(frames=args.flash_frames)
            log_event("end_marker_draw_request", perf_time=end_ts.get("draw_request_perf"))
            log_event("end_marker_draw_present", perf_time=end_ts.get("draw_present_perf"))
        else:
            log_event("stop_without_marker")

    except KeyboardInterrupt:
        logging.warning("Ctrl+Cによる中断を検出しました。")
    finally:
        controller.teardown()
        if recorder.start_perf is not None:
            recorder.stop()
        flash_window.close()

    # ---- メタデータ集計・保存 --------------------------------------------
    anomalies = []
    anomalies.extend(recorder.drain_anomalies())
    while True:
        try:
            anomalies.append(controller.anomalies.get_nowait())
        except queue.Empty:
            break

    metadata = {
        "session_id": session_id,
        "script_version": SCRIPT_VERSION,
        "subject_id": args.subject,
        "trial_id": args.trial,
        "condition_id": args.condition,
        "created_at_local": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "software": {
            "python_version": platform.python_version(),
            "os": platform.platform(),
            "pygame_version": pygame.version.ver,
        },
        "display": {
            "index": display_index,
            "name": selected_monitor.name,
            "x": selected_monitor.x,
            "y": selected_monitor.y,
            "width": selected_monitor.width,
            "height": selected_monitor.height,
            "refresh_rate_hz": refresh_rate,
        },
        "eyetracker": {
            "device_name": eyetracker.device_name,
            "model": eyetracker.model,
            "serial_number": getattr(eyetracker, "serial_number", None),
            "address": getattr(eyetracker, "address", None),
        },
        "calibration_performed": args.calibrate,
        "calibration_status": calibration_status,
        "obs_recording_confirmed": obs_confirmed,
        "flash_settings": {
            "frames": args.flash_frames,
            "size_px": args.flash_size,
            "target_fps": args.target_fps,
        },
        "experimental_conditions": conditions,
        "time_reference_notes": {
            "primary_analysis_column": "pc_time_sec",
            "auxiliary_verification_columns": ["device_time_stamp_us", "system_time_stamp_us"],
            "note": "pc_time_sec は視線記録開始(gaze_subscribe_call)からの経過秒。"
                    "OBS同期には flash_draw_present_perf 基準でのオフセット計算を推奨。",
        },
        "timing_events": [
            {**e, "perf_counter_sec": round(e["perf_counter_sec"], 6)} for e in timing_events
        ],
        "anomalies": anomalies,
        "data_quality": {
            "total_samples": recorder.total_samples,
            "missing_samples": recorder.missing_samples,
            "missing_rate": recorder.missing_rate(),
            "first_sample": recorder.first_sample_info or None,
        },
        "csv_file": str(csv_path.name),
        "known_limitations": [
            "フラッシュのフレーム精度はソフトウェアsleepベースであり、垂直同期を保証しない。",
            "OBSがゲームキャプチャの場合、フラッシュウィンドウが録画に映らない可能性がある"
            "（ディスプレイキャプチャ前提の設計）。",
            "サンプリングレート60Hzのため、マイクロサッカードや極短時間の照準補正の高精度復元には非対応。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("\n=== セッション終了 ===")
    print(f"CSV:      {csv_path.resolve()}")
    print(f"メタデータ: {meta_path.resolve()}")
    print(f"ログ:      {log_path.resolve()}")
    print(f"総サンプル数: {recorder.total_samples} / 欠測率: {recorder.missing_rate()}")
    if anomalies:
        print(f"異常検知件数: {len(anomalies)} (詳細はメタデータJSON参照)")

    logging.info("=== セッション終了 ===")


if __name__ == "__main__":
    main()
