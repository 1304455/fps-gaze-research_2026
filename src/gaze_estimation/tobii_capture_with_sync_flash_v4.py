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

3. 【v3.0で変更】計測開始/終了の制御は、OSレベルのグローバルキーボード
   フック（`keyboard`パッケージ等）を使わず、同一LAN上の別端末
   （スマートフォン等）のブラウザからのHTTPリクエストで行う方式に変更した。
   理由:
     a) 旧方式ではSPACE/E/ESCをゲーム用ホットキーとして奪っており、
        VALORANT側の同キー（移動/アビリティ等）と衝突し、意図しない
        タイミングで計測が終了する問題が実際に発生していた
        （例: 'E'キーはアビリティ発動に頻用されるため、プレイ中に
        押すたびに終了マーカー付き終了がトリガーされていた）。
     b) より本質的な問題として、VALORANTは Vanguard
        （カーネルモードのアンチチート）を常駐させており、OSレベルの
        グローバル入力フックを行うプロセスは「入力操作/マクロ/リマップ
        ツール」の挙動パターンと類似するため、Vanguardのヒューリスティクス
        に誤検知される実運用リスクがある（キー単純リマップだけの
        ツールでもハードウェアBANに至った実例が報告されている）。
        研究用ツールとはいえ、被験者アカウントを危険に晒すべきではない
        ため、ゲームPC上では一切のキーボード/マウスフックを行わない
        設計に変更した。
   新方式では、実験者(操作者)が別端末のブラウザで簡易リモコン画面を
   開き、そこから開始/終了ボタンを押すことでHTTP経由のみでトリガーする。
   ゲームPC側で追加インストールする常駐フック系パッケージは無い。

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

6. 【v4.0で追加】obs_reqclient_requirements.md に基づき、`obsws-python` の
   `ReqClient`/`EventClient` を用いたOBS録画の自動開始・停止制御を追加した。
   実装は `obs_controller.py`（本ファイルと同ディレクトリに配置）に分離して
   おり(保守性要件 N-2)、本ファイルからは `ObsController` として呼び出す。
   設計上の要点:
     a) 要件定義書 F-4 の指定順序に従い、セッション開始シーケンスは
        「トリガ受理 → Tobii購読開始 → OBS録画開始要求」の順で実行する。
        これは「命令レベルでの同時開始」を狙ったものではなく、各段階の
        UTC時刻を個別に記録し、後処理で差分を比較評価できるようにする
        ための順序である（同期精度そのものは、既存の SyncFlashWindow に
        よる視覚フラッシュマーカーが引き続き主たる手段である）。
     b) 既定では「OBS録画開始に失敗した場合、セッション開始自体を失敗として
        扱う」(obs_require_success=True)。これは同期取得の確実性を優先する
        設計判断であり、被験者の視線データだけを取得し続けてしまい、
        後から映像と対応付けられない事態を避けるため。視線データのみで
        継続したい場合は `--obs-allow-gaze-only` で挙動を変更できる。
     c) OBS連携を無効化した場合(`--no-obs`)、または接続に失敗し
        `obs_require_success=False` の場合は、旧来通り操作者への
        手動確認プロンプト(y/n)にフォールバックする。
     d) OBS側GUIからの手動録画操作は、EventClientの状態変化イベントを
        自プログラムの要求と突き合わせることで検知し、異常系として記録する
        (F-9)。ただしOBS配信制御・シーン切替は対象外。

7. 【v4.1で変更: start_sync】実測の結果、「OBS録画開始」が「Tobiiデータ取得
   開始」より約185〜223ms早く、「録画終了」は「データ取得終了」より
   7〜17ms遅いという系統的なズレが安定して確認された。この経験的事実を
   踏まえ、開始同期の基準を「Tobii購読開始(gaze_subscribe_call)」ではなく
   「Tobiiの最初の視線サンプルが実際に到達した時刻
   (recorder.first_sample_event)」に変更した。
     a) セッション開始シーケンスは
        「トリガ受理 → Tobii購読開始 → 最初の視線サンプル到達を待機
          (first_sample_event.wait) → OBS録画開始要求」
        の順に変更した(旧: 購読開始直後にOBS開始要求を送っていた)。
     b) 最大待機秒数(--gaze-first-sample-max-wait-sec)内にサンプルが
        到達しない場合、--on-first-sample-timeout で
        "abort"(セッション自体を中止。既定値。同期基準点が取れない
        セッションを研究データに混入させないための安全側デフォルト)か
        "fallback"(待たずに即OBS開始要求を送る旧来動作へフォールバック)
        かを選べる。
     c) 同期精度は、Tobii最初のサンプル時刻とOBS側時刻(要求時刻/確認時刻の
        双方)の差分をUTCログのみから算出し、ディスプレイのリフレッシュ
        レートに対するフレーム数に変換してmetadata JSONの"start_sync"に
        記録する。算出ロジックは obs_controller.compute_start_sync_metrics()
        に一元化した(詳細は同関数のdocstring参照)。
     d) VALORANT+Vanguard環境でのアンチチート配慮により、既存の
        SyncFlashWindowによる視覚フラッシュは「本番実験での同期の主手段」
        から「検証用の補助手段」に位置づけを変更した。--flash-mode で
        onscreen(旧来動作)/offscreen(ウィンドウは生成するが画面外に配置、
        既定値)/disabled(フラッシュ機構自体を使わない)を選べる。

必要パッケージ:
    pip install tobii-research pygame screeninfo obsws-python
    (Windowsでリフレッシュレートを取得したい場合、任意で)
    pip install pywin32
    ※ v3.0でキーボードグローバルフック(`keyboard`パッケージ)への依存を廃止した。
      制御は標準ライブラリのみのHTTPサーバ(別端末のブラウザ)経由で行う。
    ※ v4.0で `obsws-python` に依存する OBS録画自動制御を追加した
      (`obs_controller.py`)。OBS側は Tools > WebSocket Server Settings で
      WebSocketサーバを有効化しておくこと(OBS Studio 28以降は標準搭載)。
--------------------------------------------------------------------------
"""

import argparse
import csv
import ctypes
import json
import logging
import platform
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---- ディレクトリ再編対応: sys.path 設定 ------------------------------------
_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parent.parent if _THIS_FILE.parent.name != "src" else _THIS_FILE.parent
_PROJECT_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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

if _MISSING_PACKAGES:
    print("以下のパッケージが見つかりません。インストールしてください:")
    print(f"    pip install {' '.join(_MISSING_PACKAGES)}")
    sys.exit(1)

# obs_controller (obsws-python本体) は必須パッケージ扱いにはしない。
# --no-obs 指定時や obsws-python 未インストール環境でも、視線計測自体は
# 継続できるようにするため、インポート失敗はobs_controller内部で処理し、
# 実際に接続を試みる際(connect_obs)に初めてエラーとして扱う。
try:
    import obs_controller_v2
    from obs_controller_v2 import ObsConfig, ObsController, compute_start_sync_metrics
except ImportError:
    from gaze_estimation import obs_controller_v2
    from gaze_estimation.obs_controller_v2 import ObsConfig, ObsController, compute_start_sync_metrics

IS_WINDOWS = platform.system() == "Windows"

SCRIPT_VERSION = "4.1.0-start-sync"

# ============================================================================
# 定数・既定値
# ============================================================================

DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "raw"
BUFFER_SIZE = 200
DEFAULT_TARGET_FPS = 60
DEFAULT_FLASH_FRAMES = 1
DEFAULT_FLASH_SIZE_PX = 150
GAP_ANOMALY_MULTIPLIER = 3.0  # サンプル間隔がこの倍数を超えたら欠落として記録
DEFAULT_CONTROL_HOST = "0.0.0.0"
DEFAULT_CONTROL_PORT = 8765

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
    """同期検証用の小さなフラッシュウィンドウ(既定は選択ディスプレイ中央)。

    通常時は非表示・非アクティブ化されており、flash() 呼び出し時のみ
    一瞬白色を表示してすぐ非表示に戻る。

    【v4.1での位置づけ変更】本番実験(VALORANT+Vanguard)での開始/終了同期は
    本ウィンドウではなく、Tobiiの最初の視線サンプル時刻とOBS側時刻の差分
    (start_sync, obs_controller.compute_start_sync_metrics)を主手段とする。
    本クラスは検証・デバッグ用、または将来ディスプレイキャプチャ運用時の
    補助手段として維持している。offscreen=True 指定時は仮想デスクトップ
    外に配置され、映像には一切映り込まない。
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

    def __init__(self, monitor, size_px: int, target_fps: int, offscreen: bool = False):
        self.monitor = monitor
        self.size_px = size_px
        self.target_fps = target_fps
        # offscreen=True の場合、ウィンドウ自体は生成する(描画/提示時刻の
        # 取得は検証用に残す)が、選択ディスプレイの外側(仮想デスクトップ
        # 座標系上のはるか右下)に配置し、物理ディスプレイ上には表示されず
        # ディスプレイキャプチャにも合成されないようにする。
        # VALORANT+Vanguard環境での本番実験では、開始同期はこのウィンドウ
        # ではなく start_sync (Tobii最初のサンプル vs OBS時刻) を用いるため、
        # このウィンドウが映像に一切載らないことを優先する。
        self.offscreen = offscreen
        self.hwnd = None
        self.surface = None
        self._init_window()

    def _init_window(self):
        import os

        if self.offscreen:
            pos_x = self.monitor.x + self.monitor.width + 5000
            pos_y = self.monitor.y + self.monitor.height + 5000
        else:
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
# リモート制御 (F-2 改訂版, 異常系: 多重start要求)
#
# 【設計方針】
# VALORANT実行中はVanguard(カーネルモードのアンチチート)が常駐しているため、
# ゲームPC上ではキーボード/マウスのグローバルフックを一切行わない。
# 代わりに、同一LAN上の別端末(実験者のスマートフォン/ノートPC等)の
# ブラウザから、標準ライブラリのみで実装した簡易HTTPサーバに対して
# start/stop/abortのリクエストを送ることで制御する。
#   - ゲームのキー入力(移動・アビリティ等)と物理的に別デバイスなので、
#     衝突・干渉が原理的に発生しない。
#   - ゲームPC側に追加の常駐フック型ソフトウェアが存在しないため、
#     Vanguardの入力操作系ヒューリスティクスに抵触するリスクを回避する。
# ============================================================================

def get_lan_ip() -> str:
    """このPCがLAN上で持つIPアドレスをベストエフォートで取得する。
    実際に外部へ通信するわけではなく、経路解決のためにUDPソケットを
    connect()するだけ(パケットは送出されない)。取得失敗時はlocalhost。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class _ControlRequestHandler(BaseHTTPRequestHandler):
    """NetworkTriggerControllerが起動するHTTPサーバのリクエストハンドラ。
    self.server 経由で controller と token にアクセスする
    (ThreadingHTTPServer 側にセットしておく)。
    """

    def log_message(self, format, *args):  # noqa: A002 (標準APIのシグネチャに合わせる)
        logging.info("[ControlHTTP] %s - " + format, self.client_address[0], *args)

    def _check_token(self, qs: dict) -> bool:
        required = self.server.token
        if not required:
            return True
        supplied = qs.get("token", [None])[0]
        return supplied == required

    def _send_text(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, body: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        controller: "NetworkTriggerController" = self.server.trigger_controller
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        client_ip = self.client_address[0]

        if parsed.path == "/":
            self._send_html(controller.render_panel_html())
            return

        if not self._check_token(qs):
            self._send_text(403, "invalid token")
            return

        if parsed.path == "/start":
            ok = controller.remote_start(client_ip)
            self._send_text(200, "started" if ok else "ignored (already started)")
        elif parsed.path == "/mark_stop":
            ok = controller.remote_stop(with_marker=True, client_ip=client_ip)
            self._send_text(200, "stopping-with-marker" if ok else "ignored (not started)")
        elif parsed.path == "/stop":
            ok = controller.remote_stop(with_marker=False, client_ip=client_ip)
            self._send_text(200, "stopping-no-marker" if ok else "ignored (not started)")
        elif parsed.path == "/abort":
            ok = controller.remote_abort(client_ip)
            self._send_text(200, "aborted" if ok else "ignored (already started)")
        elif parsed.path == "/status":
            self._send_text(200, controller.status_text())
        else:
            self._send_text(404, "not found")


class NetworkTriggerController:
    """LAN経由のHTTPリクエストでSTART/STOP/ABORTを制御する。

    公開インターフェースは旧TriggerControllerと互換
    (trigger_event / abort_before_start_event / stop_no_marker_event /
     stop_with_marker_event / anomalies / key_event_perf_time() / teardown())
    としているため、main()側の呼び出し箇所はほぼ変更不要。
    """

    def __init__(self, host: str = DEFAULT_CONTROL_HOST, port: int = DEFAULT_CONTROL_PORT,
                 token: "str | None" = None):
        self.started = False
        self.trigger_event = threading.Event()
        self.abort_before_start_event = threading.Event()
        self.stop_no_marker_event = threading.Event()
        self.stop_with_marker_event = threading.Event()
        self.anomalies = queue.Queue()
        self._key_event_perf = None
        self._lock = threading.Lock()

        self.token = token or secrets.token_urlsafe(8)
        self.host = host
        self.port = port

        self.httpd = ThreadingHTTPServer((host, port), _ControlRequestHandler)
        self.httpd.trigger_controller = self
        self.httpd.token = self.token
        self._server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._server_thread.start()

        self.lan_ip = get_lan_ip()
        self.control_url = f"http://{self.lan_ip}:{self.port}/?token={self.token}"
        logging.info(f"制御用HTTPサーバを起動しました: {self.control_url}")
        logging.info(
            "上記URLを実験者の別端末(スマートフォン等、同一LAN/Wi-Fi上)の"
            "ブラウザで開いてください。ゲームPC上でのキー操作は不要です。"
        )

    # ---- リモートからの操作 -------------------------------------------
    def remote_start(self, client_ip: str) -> bool:
        with self._lock:
            if self.started:
                self.anomalies.put({
                    "type": "duplicate_start_request",
                    "perf_time": time.perf_counter(),
                    "client_ip": client_ip,
                })
                logging.warning(f"測定開始後の重複start要求を検出しました（無視）。client={client_ip}")
                return False
            self.started = True
            self._key_event_perf = time.perf_counter()
            self.trigger_event.set()
            logging.info(f"[REMOTE] start要求を受理しました。client={client_ip}")
            return True

    def remote_stop(self, with_marker: bool, client_ip: str) -> bool:
        if not self.started:
            logging.warning(f"[REMOTE] 未開始状態でのstop要求を無視しました。client={client_ip}")
            return False
        if with_marker:
            self.stop_with_marker_event.set()
        else:
            self.stop_no_marker_event.set()
        logging.info(f"[REMOTE] stop要求(with_marker={with_marker})を受理しました。client={client_ip}")
        return True

    def remote_abort(self, client_ip: str) -> bool:
        if self.started:
            logging.warning(f"[REMOTE] 開始後のabort要求を無視しました。client={client_ip}")
            return False
        self.abort_before_start_event.set()
        logging.info(f"[REMOTE] abort要求を受理しました。client={client_ip}")
        return True

    def status_text(self) -> str:
        if self.stop_with_marker_event.is_set() or self.stop_no_marker_event.is_set():
            return "stopped"
        if self.started:
            return "recording"
        return "waiting"

    # ---- main()互換インターフェース -------------------------------------
    def key_event_perf_time(self):
        return self._key_event_perf

    def teardown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    # ---- リモコン用HTML --------------------------------------------------
    def render_panel_html(self) -> str:
        return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gaze Capture Remote</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background:#111; color:#eee;
          text-align:center; padding-top:32px; margin:0; }}
  h2 {{ font-size:1.1em; font-weight:normal; color:#aaa; }}
  button {{ font-size:1.3em; padding:22px 10px; margin:10px auto; border-radius:14px;
            border:none; width:82%; max-width:360px; display:block; color:white; }}
  #start {{ background:#2e7d32; }}
  #mark  {{ background:#1565c0; }}
  #stop  {{ background:#ef6c00; }}
  #abort {{ background:#616161; }}
  #status {{ font-size:1.1em; margin-top:18px; color:#9ad; min-height:1.4em; }}
</style></head>
<body>
<h2>視線計測 リモート制御</h2>
<div id="status">status: -</div>
<button id="start">&#9654; 計測開始</button>
<button id="mark">&#9632; 終了（マーカーあり）</button>
<button id="stop">&#9632; 終了（マーカーなし/緊急）</button>
<button id="abort">&#10005; 開始前キャンセル</button>
<script>
const token = "{self.token}";
async function call(path) {{
  try {{
    const res = await fetch(path + "?token=" + token);
    document.getElementById('status').innerText = path + " -> " + (await res.text());
  }} catch (e) {{
    document.getElementById('status').innerText = "通信エラー: " + e;
  }}
}}
document.getElementById('start').onclick = () => call('/start');
document.getElementById('mark').onclick  = () => call('/mark_stop');
document.getElementById('stop').onclick  = () => call('/stop');
document.getElementById('abort').onclick = () => call('/abort');
async function poll() {{
  try {{
    const res = await fetch('/status?token=' + token);
    document.getElementById('status').innerText = "status: " + (await res.text());
  }} catch (e) {{}}
}}
setInterval(poll, 1000);
poll();
</script>
</body></html>"""


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
    p.add_argument(
        "--flash-mode", choices=["onscreen", "offscreen", "disabled"], default="offscreen",
        help="同期検証用フラッシュウィンドウの表示方法(既定: offscreen)。"
             "'onscreen'=モニタ中央に表示(旧来動作。VALORANT等アンチチート環境の"
             "本番実験では非推奨), 'offscreen'=ウィンドウは生成するが仮想デスクトップ外に"
             "配置し画面・録画には映らない(既定), 'disabled'=フラッシュウィンドウ自体を"
             "生成・使用しない。いずれの場合も開始/終了同期の主手段はstart_syncであり、"
             "本設定は検証・デバッグ用の補助機能の扱いを変えるのみ。",
    )
    p.add_argument("--target-fps", type=int, default=DEFAULT_TARGET_FPS, help="想定フレームレート(Hz)")
    p.add_argument("--calibrate", action="store_true", help="キャリブレーションを実施する")
    p.add_argument("--max-duration", type=float, default=None,
                    help="安全装置としての最大記録時間(秒)。指定なしなら手動停止のみ。")
    p.add_argument("--skip-conditions-prompt", action="store_true", help="実験条件の対話入力をスキップする")
    p.add_argument(
        "--gaze-first-sample-max-wait-sec", type=float, default=2.0,
        help="Tobii購読登録(recorder.start())後、最初の視線サンプルが到達するのを"
             "待つ最大秒数(既定: 2.0)。この待機は開始同期の基準点"
             "(gaze_first_sample_utc)を確定させるためのものであり、"
             "通常は数十ms以内にサンプルが到達する。",
    )
    p.add_argument(
        "--on-first-sample-timeout", choices=["abort", "fallback"], default="abort",
        help="--gaze-first-sample-max-wait-sec 以内に最初の視線サンプルが到達しなかった"
             "場合の挙動(既定: abort)。'abort'=同期基準点が得られないセッションを"
             "研究データに混入させないため、このセッションを中止する。"
             "'fallback'=待機を打ち切り、即座にOBS録画開始要求を送る旧来動作へ"
             "フォールバックして継続する(start_sync.methodに記録される)。",
    )
    p.add_argument("--control-host", default=DEFAULT_CONTROL_HOST,
                    help="リモート制御用HTTPサーバのbindアドレス (既定: 全インターフェース)")
    p.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT,
                    help="リモート制御用HTTPサーバのポート番号")
    p.add_argument("--control-token", default=None,
                    help="リモート制御用トークン(未指定なら起動毎に自動生成してログ/画面に表示)")

    # ---- OBS WebSocket連携 (obs_reqclient_requirements.md 対応) -----------
    p.add_argument("--obs-config", default=None,
                    help="OBS連携設定(JSON)ファイルパス。obs_enabled/obs_host/obs_port/"
                         "obs_password/obs_require_success/obs_wait_for_started_event/"
                         "obs_timeout_sec のキーを指定できる。")
    obs_enable_group = p.add_mutually_exclusive_group()
    obs_enable_group.add_argument(
        "--obs-enable", dest="obs_enabled", action="store_true", default=None,
        help="OBS連携(自動録画開始/停止)を有効化する(既定で有効。設定ファイルで"
             "無効化されている場合の上書き用)。",
    )
    obs_enable_group.add_argument(
        "--no-obs", dest="obs_enabled", action="store_false",
        help="OBS連携を無効化し、従来の手動確認方式(y/n入力)にフォールバックする。",
    )
    p.add_argument("--obs-host", default=None,
                    help=f"OBS接続先ホスト(既定: {obs_controller_v2.DEFAULT_OBS_HOST})")
    p.add_argument("--obs-port", type=int, default=None,
                    help=f"OBS接続先ポート(既定: {obs_controller_v2.DEFAULT_OBS_PORT})")
    p.add_argument("--obs-password", default=None, help="OBS WebSocket パスワード")
    obs_require_group = p.add_mutually_exclusive_group()
    obs_require_group.add_argument(
        "--obs-require-success", dest="obs_require_success", action="store_true", default=None,
        help="OBS録画開始に失敗した場合、セッション開始自体を失敗として扱う"
             "(既定で有効。F-7)。",
    )
    obs_require_group.add_argument(
        "--obs-allow-gaze-only", dest="obs_require_success", action="store_false",
        help="OBS録画開始に失敗しても、視線データのみで測定を継続する。",
    )
    obs_wait_group = p.add_mutually_exclusive_group()
    obs_wait_group.add_argument(
        "--obs-wait-event", dest="obs_wait_for_started_event", action="store_true", default=None,
        help="OBS録画開始/停止の確認イベント受信を待つ(既定で有効)。",
    )
    obs_wait_group.add_argument(
        "--obs-no-wait-event", dest="obs_wait_for_started_event", action="store_false",
        help="確認イベントの受信を待たず、要求送信のみで処理を先に進める。",
    )
    p.add_argument("--obs-timeout-sec", type=float, default=None,
                    help=f"OBS応答/確認イベント待ちタイムアウト秒"
                         f"(既定: {obs_controller_v2.DEFAULT_OBS_TIMEOUT_SEC})")

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

    # ---- OBS接続・録画制御セットアップ (F-1〜F-9) --------------------------
    obs_overrides = {
        "obs_enabled": args.obs_enabled,
        "obs_host": args.obs_host,
        "obs_port": args.obs_port,
        "obs_password": args.obs_password,
        "obs_require_success": args.obs_require_success,
        "obs_wait_for_started_event": args.obs_wait_for_started_event,
        "obs_timeout_sec": args.obs_timeout_sec,
    }
    try:
        obs_config = ObsConfig.load(args.obs_config, obs_overrides)
    except FileNotFoundError as e:
        logging.error(str(e))
        sys.exit(1)

    obs_ctrl = None          # ObsController インスタンス(OBS連携有効時のみ)
    obs_precheck_ok = False  # このセッションを開始してよいかどうかの事前確認結果
    obs_integration_mode = "disabled"  # "automatic" / "manual_fallback" / "disabled"

    if obs_config.obs_enabled:
        obs_ctrl = ObsController(config=obs_config, session_id=session_id)
        if obs_ctrl.connect_obs():
            obs_precheck_ok = True
            obs_integration_mode = "automatic"
            logging.info(
                "OBS連携が有効です。録画の開始・停止は本プログラムが自動制御します。"
            )
        elif obs_config.obs_require_success:
            logging.error(
                "OBS接続に失敗し、obs_require_success=True のためセッションを"
                "開始できません。OBSの起動状態やWebSocketサーバ設定(ポート/"
                "パスワード)を確認するか、--obs-allow-gaze-only または --no-obs を"
                "指定して再実行してください。"
            )
            sys.exit(1)
        else:
            logging.warning(
                "OBS接続に失敗しましたが obs_require_success=False のため、"
                "従来の手動確認方式(y/n)にフォールバックします。"
            )
            obs_precheck_ok = confirm_obs_recording()
            obs_integration_mode = "manual_fallback"
    else:
        logging.info("OBS連携は無効化されています(--no-obs)。従来の手動確認方式を使用します。")
        obs_precheck_ok = confirm_obs_recording()
        obs_integration_mode = "disabled"

    if not obs_precheck_ok:
        logging.error("OBS録画状態が確認できないため中止します。")
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

    # ---- フラッシュウィンドウ準備 (F-3、検証用の補助手段) -----------------
    # 本番実験(VALORANT+Vanguard)での開始/終了同期はstart_syncが主手段。
    # --flash-mode disabled の場合はウィンドウ自体を生成しない。
    if args.flash_mode == "disabled":
        flash_window = None
        logging.info("flash_mode=disabled のため、フラッシュウィンドウは使用しません。")
    else:
        flash_window = SyncFlashWindow(
            monitor=selected_monitor, size_px=args.flash_size, target_fps=args.target_fps,
            offscreen=(args.flash_mode == "offscreen"),
        )
        if args.flash_mode == "onscreen":
            logging.warning(
                "flash_mode=onscreen が指定されました。VALORANT等アンチチート環境の"
                "本番実験ではオンスクリーン描画マーカーの映り込みリスクがあるため、"
                "offscreen または disabled を推奨します。"
            )

    # ---- 記録準備 -------------------------------------------------------
    recorder = GazeRecorder(csv_path=csv_path, eyetracker=eyetracker, target_fps=args.target_fps)

    # ---- トリガー待機 (F-2 改訂版: LAN経由リモート制御) --------------------
    controller = NetworkTriggerController(
        host=args.control_host, port=args.control_port, token=args.control_token,
    )
    print(f"\n待機中... 別端末(スマートフォン等)のブラウザで下記URLを開いてください:")
    print(f"    {controller.control_url}")
    print("  (ゲームPCと同一LAN/Wi-Fiに接続されている必要があります)")
    print("  ※ 初回起動時にWindowsファイアウォールの許可を求められた場合は「許可」してください。")

    session_aborted_due_to_obs = False
    session_aborted_due_to_gaze_timeout = False
    start_sync_method = "not_configured"

    try:
        while not controller.trigger_event.is_set() and not controller.abort_before_start_event.is_set():
            time.sleep(0.001)

        if controller.abort_before_start_event.is_set():
            logging.info("測定開始前にESCが押されたため中止します。")
            log_event("aborted_before_start")
            if obs_ctrl is not None:
                obs_ctrl.disconnect_obs()
            return

        # --- キー受信時刻 (= セッション開始トリガ受理, F-4 手順1-2) ---
        t_key = controller.key_event_perf_time()
        log_event("key_received", perf_time=t_key)
        if obs_ctrl is not None:
            obs_ctrl.record.session_trigger_utc = timing_events[-1]["wall_utc"]

        # --- 開始同期フラッシュ (検証用の補助手段。本番はoffscreen/disabled推奨) ---
        if flash_window is not None:
            flash_ts = flash_window.flash(frames=args.flash_frames, color=(255, 0, 127))
            log_event("flash_draw_request", perf_time=flash_ts.get("draw_request_perf"))
            log_event("flash_draw_present", perf_time=flash_ts.get("draw_present_perf"))

        # --- 視線記録開始 (Tobii購読登録) ---
        recorder.start()
        log_event("gaze_subscribe_call", perf_time=recorder.start_perf)
        if obs_ctrl is not None:
            obs_ctrl.record.tobii_subscribe_utc = timing_events[-1]["wall_utc"]

        # --- 最初の視線サンプル到達を待機 (start_syncの基準点を確定させる) ---
        # 実測で「OBS録画開始」が「Tobiiデータ取得開始」より系統的に約0.2秒
        # 早いことが確認されたため、購読開始直後ではなく、実際にサンプルが
        # 届いた時刻を基準としてOBS開始要求を送る。
        gaze_first_sample_ok = recorder.first_sample_event.wait(
            timeout=args.gaze_first_sample_max_wait_sec
        )
        if gaze_first_sample_ok:
            info = recorder.first_sample_info
            first_sample_perf = recorder.start_perf + info["pc_time_sec"]
            log_event(
                "gaze_first_sample",
                perf_time=first_sample_perf,
                wall_local=info["wall_timestamp_local"],
                wall_utc=info["wall_timestamp_utc"],
            )
            start_sync_method = "wait_for_first_sample"
        else:
            timeout_msg = (
                f"最初の視線サンプルが{args.gaze_first_sample_max_wait_sec}秒以内に"
                "到達しませんでした。"
            )
            controller.anomalies.put({
                "type": "gaze_first_sample_timeout",
                "perf_time": time.perf_counter(),
                "max_wait_sec": args.gaze_first_sample_max_wait_sec,
            })
            log_event("gaze_first_sample_timeout")
            if args.on_first_sample_timeout == "abort":
                logging.error(
                    timeout_msg + " --on-first-sample-timeout=abort のため、"
                    "同期基準点が得られないセッションとして中止します。"
                )
                session_aborted_due_to_gaze_timeout = True
                start_sync_method = "aborted_on_timeout"
            else:
                logging.warning(
                    timeout_msg + " --on-first-sample-timeout=fallback のため、"
                    "待機を打ち切りOBS開始要求を即時送信して継続します"
                    "(start_syncは事後、実サンプル到達後に評価されます)。"
                )
                start_sync_method = "fallback_immediate_after_timeout"

        # --- OBS録画開始要求 (F-2, F-7) ---
        # start_sync方式では、Tobiiの最初のサンプル到達直後にOBS開始要求を送る。
        # これにより「購読開始からサンプル到達までの遅延」を同期誤差に含めない。
        if session_aborted_due_to_gaze_timeout:
            logging.error(
                "最初の視線サンプル待機のタイムアウトにより、OBS録画開始要求を"
                "送信せずセッションを中止します。"
            )
        elif obs_ctrl is not None:
            obs_start_ok = obs_ctrl.start_obs_recording()
            log_event("obs_start_request", wall_utc=obs_ctrl.record.obs_start_request_utc)
            if not obs_start_ok:
                if obs_config.obs_require_success:
                    logging.error(
                        "OBS録画開始要求が失敗しました。obs_require_success=True の"
                        "ため、セッション開始失敗として中止します(F-7)。"
                        "Tobiiの取得済みデータは通常どおり保存処理を行います。"
                    )
                    session_aborted_due_to_obs = True
                else:
                    logging.warning(
                        "OBS録画開始要求が失敗しましたが、obs_require_success=False の"
                        "ため視線データのみで測定を継続します(F-7)。"
                    )
                    controller.anomalies.put({
                        "type": "obs_start_failed_continuing_gaze_only",
                        "perf_time": time.perf_counter(),
                        "error": obs_ctrl.record.error_message,
                    })

        if session_aborted_due_to_obs or session_aborted_due_to_gaze_timeout:
            log_event("aborted_after_start_due_to_obs_or_sync_failure")
        else:
            print(f"測定中... 同じ画面（{controller.control_url}）で終了操作を行ってください。")

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

            # --- 終了マーカー（任意、検証用） ---
            if controller.stop_with_marker_event.is_set() and flash_window is not None:
                end_ts = flash_window.flash(frames=args.flash_frames)
                log_event("end_marker_draw_request", perf_time=end_ts.get("draw_request_perf"))
                log_event("end_marker_draw_present", perf_time=end_ts.get("draw_present_perf"))
            else:
                log_event("stop_without_marker")

    except KeyboardInterrupt:
        logging.warning("Ctrl+Cによる中断を検出しました。")
    finally:
        controller.teardown()
        # --- OBS録画停止 (F-3, F-8: 停止に失敗してもTobii側のwriter close/flushは
        #     必ず実行する) ---
        if obs_ctrl is not None:
            try:
                obs_ctrl.stop_obs_recording()
            except Exception as e:
                logging.error(f"OBS録画停止処理で未捕捉の例外が発生しました（無視して続行）: {e}")
        if recorder.start_perf is not None:
            recorder.stop()
        if flash_window is not None:
            flash_window.close()
        if obs_ctrl is not None:
            obs_ctrl.disconnect_obs()

    # ---- メタデータ集計・保存 --------------------------------------------
    anomalies = []
    anomalies.extend(recorder.drain_anomalies())
    while True:
        try:
            anomalies.append(controller.anomalies.get_nowait())
        except queue.Empty:
            break
    if obs_ctrl is not None:
        anomalies.extend(obs_ctrl.anomalies)

    # ---- 開始同期(start_sync)メトリクスの算出 ----------------------------
    # フラッシュマーカーに依存せず、Tobii最初のサンプル時刻とOBS側時刻の
    # UTCログのみから算出する。recorder.first_sample_info は、待機がタイム
    # アウトした場合でも、その後実際にサンプルが到達していれば埋まっている
    # (バックグラウンドのcallbackは待機とは独立して動作し続けるため)。
    start_sync = None
    if obs_ctrl is not None and recorder.first_sample_info:
        start_sync = compute_start_sync_metrics(
            gaze_first_sample_utc=recorder.first_sample_info.get("wall_timestamp_utc"),
            obs_start_request_utc=obs_ctrl.record.obs_start_request_utc,
            obs_record_started_utc=obs_ctrl.record.obs_record_started_utc,
            refresh_rate_hz=refresh_rate,
        )
        start_sync["method"] = start_sync_method
        obs_ctrl.record.start_sync_method = start_sync_method
        obs_ctrl.record.gaze_first_sample_utc = start_sync["gaze_first_sample_utc"]
        obs_ctrl.record.start_sync_delta_sec = start_sync["delta_sec_request_based"]
        obs_ctrl.record.start_sync_delta_frames = start_sync["delta_frames_request_based"]
        obs_ctrl.record.start_sync_delta_sec_confirmed = start_sync["delta_sec_confirmed_based"]
        obs_ctrl.record.start_sync_delta_frames_confirmed = start_sync["delta_frames_confirmed_based"]
        obs_ctrl.record.start_sync_within_one_frame = start_sync["within_one_frame_request_based"]
    elif obs_ctrl is not None:
        # OBS連携は有効だが、視線サンプルが一度も届かなかったケース
        # (デバイス未接続・タイムアウト後もサンプルなし等)。
        obs_ctrl.record.start_sync_method = start_sync_method

    obs_sync_log_path = None
    if obs_ctrl is not None:
        obs_sync_log_path = obs_ctrl.write_obs_sync_log(output_dir, base_name)

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
        "obs_integration": {
            "mode": obs_integration_mode,
            "enabled": obs_config.obs_enabled,
            "host": obs_config.obs_host if obs_config.obs_enabled else None,
            "port": obs_config.obs_port if obs_config.obs_enabled else None,
            "require_success": obs_config.obs_require_success,
            "wait_for_started_event": obs_config.obs_wait_for_started_event,
            "timeout_sec": obs_config.obs_timeout_sec,
            "precheck_ok": obs_precheck_ok,
            "sync_log_file": str(obs_sync_log_path.name) if obs_sync_log_path else None,
            "note": "mode='automatic'は本プログラムがReqClientで録画開始/停止を自動制御した"
                    "ことを示す。'manual_fallback'はOBS接続に失敗し従来のy/n確認に切り替えた"
                    "ことを、'disabled'は--no-obs等でOBS連携自体を使用しなかったことを示す。",
        },
        "obs_sync": obs_ctrl.record.as_dict() if obs_ctrl is not None else None,
        "start_sync": start_sync,
        "session_aborted_due_to_obs_failure": session_aborted_due_to_obs,
        "session_aborted_due_to_gaze_first_sample_timeout": session_aborted_due_to_gaze_timeout,
        "flash_settings": {
            "mode": args.flash_mode,
            "frames": args.flash_frames,
            "size_px": args.flash_size,
            "target_fps": args.target_fps,
            "note": "v4.1以降、開始/終了同期の主手段ではない(start_syncを参照)。"
                    "mode='onscreen'の場合のみ、選択ディスプレイ中央に実際に描画される。",
        },
        "control_protocol": {
            "type": "lan_http_remote",
            "host": args.control_host,
            "port": args.control_port,
            "lan_ip_at_session_start": controller.lan_ip,
            "note": "VALORANT実行中はVanguard(カーネルモードのアンチチート)が常駐するため、"
                    "ゲームPC上でのキーボード/マウスのグローバルフックは使用していない。"
                    "計測開始/終了は別端末のブラウザからのHTTPリクエストのみで行った。",
        },
        "experimental_conditions": conditions,
        "time_reference_notes": {
            "primary_analysis_column": "pc_time_sec",
            "auxiliary_verification_columns": ["device_time_stamp_us", "system_time_stamp_us"],
            "note": "pc_time_sec は視線記録開始(gaze_subscribe_call)からの経過秒。"
                    "OBS同期(開始)には start_sync フィールド"
                    "(gaze_first_sample_utc と obs_start_request_utc/"
                    "obs_record_started_utc のUTC差分)の使用を推奨する。"
                    "flash_draw_present_perf は検証用途(flash_mode!=disabled時のみ)。",
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
            "start_sync(開始同期)は、Tobiiの『最初の視線サンプルがコールバックとして"
            "アプリケーションに届いた時刻』を基準としており、アイトラッカー内部での"
            "サンプリング〜USB/Bluetooth転送〜OS/ドライバのスケジューリングに由来する"
            "遅延そのものは測定・補正できない。あくまで『このプログラムが観測可能な"
            "範囲での』基準点であることに留意する。",
            "delta_*_request_based はOBSへの録画開始要求送信時刻との差分であり、"
            "OBS内部でのエンコーダ起動遅延を含まないため実際のズレを過小評価しうる。"
            "delta_*_confirmed_based はRecordStateChanged(STARTED)受信時刻との差分で、"
            "WebSocketイベント配送のオーバーヘッドを含むため過大評価しうる。"
            "両者の間に真の値があるという前提で、挟み込みとして報告することを推奨する。",
            "--on-first-sample-timeout=fallback を選んだセッションでは、start_sync.method"
            "が'fallback_immediate_after_timeout'となり、OBS開始要求が最初のサンプル到達"
            "『前』に送信されている(=従来方式と同等)。このようなセッションは"
            "wait_for_first_sampleのセッションと同一の同期精度を主張すべきではないため、"
            "卒論等の集計時にはstart_sync.methodで層別して報告することを推奨する。",
            "フラッシュウィンドウ(flash_mode)の描画フレーム精度はソフトウェアsleepベース"
            "であり、垂直同期を保証しない。またVALORANT等アンチチート環境での本番実験"
            "では、映像への映り込みを避けるためoffscreen/disabledを既定としており、"
            "本番実験ではそもそも同期の主手段として使用しない。",
            "サンプリングレート60Hzのため、マイクロサッカードや極短時間の照準補正の高精度復元には非対応。",
            "リモート制御はLAN内のHTTP(非TLS)であり、簡易トークンによる保護のみ。"
            "研究用ローカルネットワーク以外（公衆Wi-Fi等）での運用は避けること。",
            "start要求受理からtrigger_event検知までにOS/ネットワークスタック由来の"
            "数ms〜数十ms程度の遅延が理論上乗る可能性があるが、start_syncはこの経路より"
            "後(Tobii購読開始後)を基準とするため、開始トリガー自体の遅延はstart_syncの"
            "評価値には影響しない。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("\n=== セッション終了 ===")
    print(f"CSV:      {csv_path.resolve()}")
    print(f"メタデータ: {meta_path.resolve()}")
    print(f"ログ:      {log_path.resolve()}")
    if obs_sync_log_path is not None:
        print(f"OBS同期ログ: {obs_sync_log_path.resolve()}")
    if start_sync is not None:
        frames = start_sync.get("delta_frames_request_based")
        frames_c = start_sync.get("delta_frames_confirmed_based")
        print(
            f"開始同期(start_sync): method={start_sync.get('method')} "
            f"delta_frames(request/confirmed)="
            f"{frames if frames is not None else 'N/A'}/"
            f"{frames_c if frames_c is not None else 'N/A'}"
        )
    print(f"総サンプル数: {recorder.total_samples} / 欠測率: {recorder.missing_rate()}")
    if anomalies:
        print(f"異常検知件数: {len(anomalies)} (詳細はメタデータJSON参照)")

    logging.info("=== セッション終了 ===")


if __name__ == "__main__":
    main()
