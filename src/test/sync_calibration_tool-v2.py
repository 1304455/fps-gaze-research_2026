# -*- coding: utf-8 -*-
"""
sync_calibration_tool.py

Tobii Pro Spark + OBS の「開始同期オフセット」を実測するための、一回限りの
検証専用ツール。

--------------------------------------------------------------------------
【背景・このツールが解決する問題】

tobii_capture_with_sync_flash_v4.py の start_sync では、Tobii最初の視線
サンプル到達時刻を基準に、OBS側の時刻として

    - delta_sec_request_based   : OBSへ録画開始を「要求」した時刻との差分
    - delta_sec_confirmed_based : OBSから録画開始「確認」イベントを受信した
                                   時刻との差分

の2種類を算出できる。しかしこの2つのどちらが実際の映像1フレーム目に近い
のか(あるいはその中間のどこかなのか)は、ログ・メタデータだけからは判定
できない。OBS内部で「要求受理→エンコーダ起動→実際の書き込み開始→
WebSocketイベント配送」がどのタイミングで起きているかはブラックボックス
だからである。

以前は単発の白フラッシュで「映像に映ったかどうか」を目視確認する方式を
検討したが、以下の理由で本ツールでは採用しない:
    1. フラッシュは「映った/映らなかった」の二値情報しか得られず、
       正確に何ms/何フレームずれているかまでは分からない。
    2. 1フレーム(1/60秒)だけ表示されるため、動画のどのフレームで
       目視確認するかのタイミング調整自体が難しい。
    3. セッション終盤でのドリフト(録画時間と実時間のズレ)を検証できない。

そこで本ツールでは、フラッシュの代わりに「常時更新され続けるUTC時刻表示 +
Tobii基準からの経過時間(T+ x.xxx s)」を画面に表示し続ける。録画された
映像を1フレームずつ確認すれば、"どのフレームでも直接その瞬間の時刻を読み
取れる"ため、開始オフセットだけでなくセッション終盤までのドリフトも同一の
検証セッションで評価できる。

--------------------------------------------------------------------------
【重要な前提・制約(必ず一読すること)】

  - この検証はOBSの映像ソースが「ディスプレイキャプチャ」、または本
    ウィンドウ(タイトル: SYNC_CLOCK_OVERLAY)を直接対象とした「ウィンドウ
    キャプチャ」であることを前提とする。「ゲームキャプチャ」ではこの
    オーバーレイウィンドウは録画に含まれないため、検証そのものが成立
    しない。
  - VALORANT本番実験で「ゲームキャプチャ」を使う場合、本ツールで得られる
    補正値は「OBS内部の録画開始シーケンスの遅延はキャプチャソースの種類に
    依存しない」という仮定のもとで転用していることになる。この仮定自体は
    本ツールでは検証できないため、卒論等では明記すべき限界として
    known_limitations に記載している。
  - VALORANT自体の起動は不要。Vanguardの常駐やアンチチート云々は本検証と
    無関係な、Tobii + OBSだけの単体検証である。

--------------------------------------------------------------------------
【使い方】

  1. 検証セッションを記録する:
       python sync_calibration_tool.py record --duration-sec 20

     実行するとEnterキー入力待ちになるので、OBS側の映像ソース設定
     (上記前提を参照)を確認してからEnterを押す。指定秒数のあいだ
     SYNC_CLOCK_OVERLAY ウィンドウにUTC時刻とT+経過時間が表示され続ける
     ので、その間にOBSで録画されていることを確認する。

  2. 出力された動画ファイル(*_meta.jsonのobs_output_path)を動画編集
     ソフト等で1フレームずつ確認し、
       a) オーバーレイが最初に読み取れるフレームの再生位置(秒)と、
          そこに表示されているT+の値
       b) (任意・ドリフト確認用)セッション終盤の別フレームでも同様に
          再生位置とT+の値
     を読み取る。

  3. 分析する:
       python sync_calibration_tool.py analyze \
           --meta ./sync_calibration_sessions/xxx_meta.json \
           --point 0.52=0.095 \
           --point 18.20=17.804

     --point は「動画再生位置(秒)=読み取ったT+の値(秒)」の形式。
     複数指定すると平均・ばらつき・ドリフト(回帰の傾き)も表示する。

必要パッケージ: tobii_capture_with_sync_flash_v4.py および obs_controller_v2.py
と同じディレクトリに配置し、同じ依存パッケージ(tobii-research, pygame,
screeninfo, obsws-python)がインストールされていること。
--------------------------------------------------------------------------
"""

import argparse
import json
import logging
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import pygame
except ImportError:
    pygame = None

try:
    import tobii_research as tr
except ImportError:
    tr = None

# ---- ディレクトリ再編対応: sys.path 設定 ------------------------------------
_THIS_FILE = Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parent.parent if _THIS_FILE.parent.name != "src" else _THIS_FILE.parent
_PROJECT_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 既存の本番用スクリプトから、検証済みのTobii取得ロジック・ディスプレイ
# 選択ロジックを再利用する(同じロジックの重複実装によるズレを避けるため)。
try:
    import tobii_capture_with_sync_flash_v4 as tcap
except ImportError:
    from gaze_estimation import tobii_capture_with_sync_flash_v4 as tcap

try:
    from obs_controller_v2 import ObsConfig, ObsController, compute_start_sync_metrics
except ImportError:
    from gaze_estimation.obs_controller_v2 import ObsConfig, ObsController, compute_start_sync_metrics

SCRIPT_VERSION = "1.0.0-sync-calibration"
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "raw" / "sync_calibration_sessions"


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
# クロックオーバーレイウィンドウ (検証専用)
# ============================================================================

class ClockOverlayWindow:
    """検証用: Tobii最初のサンプル時刻を基準としたT+経過時間と絶対UTC時刻を
    継続的に描画し続けるウィンドウ。

    フラッシュと異なり常時可視であるため、録画された映像の任意のフレーム
    から直接「その瞬間の時刻」を読み取れる。これにより:
      - 開始オフセット: 映像が最初に読み取れるフレームのT+値がそのまま
        「Tobii最初のサンプルから映像開始までの実測ズレ(秒)」になる。
      - ドリフト: セッション終盤のフレームでも同様に読み取り、複数点から
        回帰の傾きを見ることで、録画時間と実時間の間にズレ(ドリフト)が
        ないかを確認できる。
    """

    def __init__(self, monitor, window_w: int = 1000, window_h: int = 300):
        self.monitor = monitor
        self.window_w = window_w
        self.window_h = window_h
        self._stop_event = threading.Event()
        self._draw_log = []
        self._draw_log_lock = threading.Lock()
        self._reference_perf = None  # T+算出の基準(gaze_first_sampleのperf換算値)
        self.surface = None
        self.font_big = None
        self.font_small = None
        self._init_window()

    def _init_window(self):
        import os
        pos_x = self.monitor.x + self.monitor.width // 2 - self.window_w // 2
        pos_y = self.monitor.y + self.monitor.height // 2 - self.window_h // 2
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{pos_x},{pos_y}"

        pygame.display.init()
        pygame.font.init()
        self.surface = pygame.display.set_mode((self.window_w, self.window_h), pygame.NOFRAME)
        pygame.display.set_caption("SYNC_CLOCK_OVERLAY")
        try:
            self.font_big = pygame.font.SysFont("consolas", 88, bold=True)
            self.font_small = pygame.font.SysFont("consolas", 32)
        except Exception:
            # consolasが無い環境向けフォールバック
            self.font_big = pygame.font.Font(None, 100)
            self.font_small = pygame.font.Font(None, 40)
        self._draw(None)

    def set_reference_perf(self, reference_perf: float) -> None:
        """T+算出の基準時刻(time.perf_counter()換算)をセットする。"""
        self._reference_perf = reference_perf

    def _draw(self, t_plus):
        now_utc = datetime.now(timezone.utc)
        self.surface.fill((0, 0, 0))
        utc_text = now_utc.strftime("%H:%M:%S.") + f"{now_utc.microsecond // 1000:03d}"
        utc_surf = self.font_small.render(f"UTC {utc_text}", True, (200, 200, 200))
        self.surface.blit(utc_surf, (20, 15))
        if t_plus is not None:
            tplus_text = f"T+{t_plus:8.3f}s"
        else:
            tplus_text = "T+ -------s"
        tplus_surf = self.font_big.render(tplus_text, True, (255, 220, 0))
        self.surface.blit(tplus_surf, (20, 85))
        pygame.display.flip()
        return now_utc

    def run_loop(self, duration_sec: float) -> None:
        """呼び出し側スレッドをduration_sec秒間占有し、描画し続ける。

        検証用の短時間実行のため、単純なタイトループ(短いsleepのみ)で
        CPU使用率よりも描画間隔の短さを優先する。
        """
        start_perf = time.perf_counter()
        while (time.perf_counter() - start_perf) < duration_sec and not self._stop_event.is_set():
            draw_req_perf = time.perf_counter()
            t_plus = None
            if self._reference_perf is not None:
                t_plus = draw_req_perf - self._reference_perf
            now_utc = self._draw(t_plus)
            draw_present_perf = time.perf_counter()
            with self._draw_log_lock:
                self._draw_log.append({
                    "draw_request_perf": round(draw_req_perf, 6),
                    "draw_present_perf": round(draw_present_perf, 6),
                    "displayed_utc": now_utc.isoformat(timespec="milliseconds"),
                    "displayed_t_plus_sec": round(t_plus, 6) if t_plus is not None else None,
                })
            time.sleep(0.002)

    def stop(self) -> None:
        self._stop_event.set()

    def write_log(self, output_dir: Path, base_name: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / f"{base_name}_clock_overlay.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            with self._draw_log_lock:
                for entry in self._draw_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return log_path

    def close(self) -> None:
        try:
            pygame.display.quit()
        except Exception:
            pass


# ============================================================================
# record: 検証セッションの記録
# ============================================================================

def cmd_record(args) -> None:
    missing = []
    if tr is None:
        missing.append("tobii-research")
    if pygame is None:
        missing.append("pygame")
    if missing:
        print(f"以下のパッケージが見つかりません: pip install {' '.join(missing)}")
        sys.exit(1)

    session_id = uuid.uuid4().hex[:8]
    file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"synccal_{file_stamp}"
    output_dir = Path(args.output_dir)
    csv_path = output_dir / f"{base_name}.csv"
    meta_path = output_dir / f"{base_name}_meta.json"
    log_path = output_dir / f"{base_name}_log.txt"

    setup_logging(log_path)
    logging.info(f"=== 同期検証セッション開始 session_id={session_id} ===")

    # ---- OBS接続 --------------------------------------------------------
    obs_overrides = {
        "obs_host": args.obs_host,
        "obs_port": args.obs_port,
        "obs_password": args.obs_password,
        "obs_timeout_sec": args.obs_timeout_sec,
    }
    obs_config = ObsConfig.load(args.obs_config, obs_overrides)
    obs_ctrl = ObsController(config=obs_config, session_id=session_id)
    if not obs_ctrl.connect_obs():
        logging.error(
            "OBSへの接続に失敗しました。OBSが起動しWebSocketサーバが"
            "有効化されているか確認してください。検証を中止します。"
        )
        sys.exit(1)

    # ---- ディスプレイ選択 -------------------------------------------------
    monitors = tcap.list_displays()
    display_index, selected_monitor = tcap.select_display(monitors)
    refresh_rate = tcap.get_refresh_rate(selected_monitor)

    # ---- アイトラッカー検出 ------------------------------------------------
    found = tr.find_all_eyetrackers()
    if not found:
        logging.error("Tobiiアイトラッカーが見つかりませんでした。")
        obs_ctrl.disconnect_obs()
        sys.exit(1)
    eyetracker = found[0]
    logging.info(f"接続デバイス: {eyetracker.device_name} / モデル: {eyetracker.model}")

    recorder = tcap.GazeRecorder(csv_path=csv_path, eyetracker=eyetracker, target_fps=args.target_fps)
    clock_window = ClockOverlayWindow(monitor=selected_monitor)

    print("\n" + "=" * 70)
    print(" 重要: OBS側の映像ソースを次のいずれかに設定してください。")
    print("   - ディスプレイキャプチャ (推奨)")
    print("   - 本ウィンドウ(タイトル: SYNC_CLOCK_OVERLAY)を対象とした")
    print("     ウィンドウキャプチャ")
    print(" 「ゲームキャプチャ」ではこのウィンドウは録画に含まれず、")
    print(" 検証が成立しません。")
    print("=" * 70)
    print(f" ディスプレイ更新レート: {refresh_rate} Hz / 記録時間: {args.duration_sec}秒")
    input(" 準備ができたらEnterキーを押して計測を開始します...")

    # ---- Tobii購読開始・最初のサンプル待機 --------------------------------
    recorder.start()
    logging.info(f"Tobii購読開始(gaze_subscribe_call) perf={recorder.start_perf:.6f}")

    gaze_first_sample_ok = recorder.first_sample_event.wait(
        timeout=args.gaze_first_sample_max_wait_sec
    )
    if not gaze_first_sample_ok:
        logging.error(
            f"最初の視線サンプルが{args.gaze_first_sample_max_wait_sec}秒以内に"
            "到達しませんでした。検証を中止します。"
        )
        recorder.stop()
        clock_window.close()
        obs_ctrl.disconnect_obs()
        sys.exit(1)

    info = recorder.first_sample_info
    gaze_first_sample_perf = recorder.start_perf + info["pc_time_sec"]
    gaze_first_sample_utc = info["wall_timestamp_utc"]
    logging.info(f"最初の視線サンプル到達: utc={gaze_first_sample_utc}")

    clock_window.set_reference_perf(gaze_first_sample_perf)

    # ---- クロックオーバーレイ描画開始 + OBS録画開始要求 --------------------
    # オーバーレイの描画開始とOBS開始要求のタイミングを極力揃えるため、
    # オーバーレイ用スレッドを起動した直後にOBS開始要求を送る。
    overlay_thread = threading.Thread(
        target=clock_window.run_loop, args=(args.duration_sec,), daemon=True,
    )
    overlay_thread.start()

    obs_start_ok = obs_ctrl.start_obs_recording()
    obs_start_request_utc = obs_ctrl.record.obs_start_request_utc
    if not obs_start_ok:
        logging.error(
            f"OBS録画開始要求に失敗しました: {obs_ctrl.record.error_message}"
        )

    print(f" 計測中... 約{args.duration_sec}秒後に自動終了します。")
    overlay_thread.join()

    # ---- 終了処理 ---------------------------------------------------------
    obs_ctrl.stop_obs_recording()
    recorder.stop()
    clock_window.close()
    obs_ctrl.disconnect_obs()

    clock_log_path = clock_window.write_log(output_dir, base_name)
    obs_sync_log_path = obs_ctrl.write_obs_sync_log(output_dir, base_name)

    start_sync = compute_start_sync_metrics(
        gaze_first_sample_utc=gaze_first_sample_utc,
        obs_start_request_utc=obs_start_request_utc,
        obs_record_started_utc=obs_ctrl.record.obs_record_started_utc,
        refresh_rate_hz=refresh_rate,
    )

    metadata = {
        "tool": "sync_calibration_tool",
        "script_version": SCRIPT_VERSION,
        "session_id": session_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "display": {
            "index": display_index,
            "name": selected_monitor.name,
            "width": selected_monitor.width,
            "height": selected_monitor.height,
            "refresh_rate_hz": refresh_rate,
        },
        "eyetracker": {
            "device_name": eyetracker.device_name,
            "model": eyetracker.model,
        },
        "gaze_first_sample_utc": gaze_first_sample_utc,
        "start_sync": start_sync,
        "obs_sync": obs_ctrl.record.as_dict(),
        "obs_output_path": obs_ctrl.record.obs_output_path,
        "clock_overlay_log_file": str(clock_log_path.name),
        "obs_sync_log_file": str(obs_sync_log_path.name),
        "csv_file": str(csv_path.name),
        "known_limitations": [
            "本検証はOBSの映像ソースが『ディスプレイキャプチャ』または本ウィンドウを"
            "対象とした『ウィンドウキャプチャ』であることを前提とする。VALORANT本番"
            "実験で『ゲームキャプチャ』を使う場合、ここで得られる補正値は『OBS内部の"
            "録画開始遅延がキャプチャソースの種類に依存しない』という未検証の仮定の"
            "もとで転用していることになる。",
            "analyzeサブコマンドの計算は『動画の再生時間は実時間に対して線形に進む"
            "(可変フレームレートやドロップフレームによる歪みがない)』ことを前提と"
            "する。複数の読み取り点から回帰の傾きを確認し、1.0から大きく外れる"
            "場合はこの前提が崩れている可能性がある。",
            "動画のフレームからT+の値を目視で読み取る際の精度は、動画プレイヤーの"
            "フレームシーク精度と人間の読み取り誤差に依存する(通常は数ms〜1フレーム"
            "程度)。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("\n=== 検証セッション終了 ===")
    print(f"メタデータ: {meta_path.resolve()}")
    print(f"録画ファイル: {obs_ctrl.record.obs_output_path}")
    print(f"delta_sec_request_based   = {start_sync.get('delta_sec_request_based')}")
    print(f"delta_sec_confirmed_based = {start_sync.get('delta_sec_confirmed_based')}")
    print("\n次の手順:")
    print("  1. 上記の録画ファイルを1フレームずつ確認し、SYNC_CLOCK_OVERLAYが")
    print("     最初に読み取れるフレームの再生位置(秒)とT+の値を記録する。")
    print("     (任意) セッション終盤のフレームでも同様にもう1点記録する。")
    print("  2. 以下のようにanalyzeを実行する:")
    print(f'     python sync_calibration_tool.py analyze --meta "{meta_path}" '
          '--point <再生秒>=<T+の値> [--point <再生秒>=<T+の値> ...]')


# ============================================================================
# analyze: 目視読み取り結果からの補正値算出
# ============================================================================

def _parse_point(raw: str):
    try:
        video_sec_str, t_plus_str = raw.split("=")
        return float(video_sec_str), float(t_plus_str)
    except Exception as e:
        raise argparse.ArgumentTypeError(
            f"--point の形式が不正です(例: 0.52=0.095): {raw!r} ({e})"
        )

def _append_analysis_log(log_path: Path, lines) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

def cmd_analyze(args) -> None:
    meta_path = Path(args.meta)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    gaze_first_sample_utc = meta.get("gaze_first_sample_utc")
    start_sync = meta.get("start_sync", {}) or {}
    request_based = start_sync.get("delta_sec_request_based")
    confirmed_based = start_sync.get("delta_sec_confirmed_based")

    points = [_parse_point(p) for p in args.point]
    if not points:
        print("--point を少なくとも1つ指定してください(例: --point 0.52=0.095)。")
        sys.exit(1)

    implied_offsets = [t_plus - video_sec for video_sec, t_plus in points]
    mean_offset = statistics.mean(implied_offsets)
    sd_offset = statistics.pstdev(implied_offsets) if len(implied_offsets) > 1 else None

    analysis_lines = []
    analysis_lines.append(f"=== analyze 実行 {datetime.now(timezone.utc).isoformat(timespec='seconds')} ===")
    analysis_lines.append(f"meta_path: {meta_path.resolve()}")
    analysis_lines.append(f"gaze_first_sample_utc: {gaze_first_sample_utc}")
    analysis_lines.append(f"読み取り点 (video_sec, T+):        {points}")
    analysis_lines.append(f"点ごとの推定オフセット(秒):        {[round(x, 4) for x in implied_offsets]}")
    if sd_offset is not None:
        analysis_lines.append(f"平均オフセット: {mean_offset:.4f} 秒 (SD={sd_offset:.4f} 秒, n={len(points)})")
    else:
        analysis_lines.append(f"平均オフセット: {mean_offset:.4f} 秒 (1点のみのためSD計算不可)")

    analysis_lines.append("")
    analysis_lines.append("参考(同一セッションのログベース推定値):")
    analysis_lines.append(f"  delta_sec_request_based   = {request_based}")
    analysis_lines.append(f"  delta_sec_confirmed_based = {confirmed_based}")

    if len(points) >= 2:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        mean_x = statistics.mean(xs)
        mean_y = statistics.mean(ys)
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x > 0:
            cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            slope = cov_xy / var_x
            drift_pct = (slope - 1.0) * 100
            analysis_lines.append("")
            analysis_lines.append(f"回帰による傾き(理想値=1.0): {slope:.6f}")
            analysis_lines.append(
                f"  1.0からのズレ: {drift_pct:+.4f}% "
                + (
                    "→ ドリフトの兆候あり。長時間セッションでは無視できない可能性がある。"
                    if abs(drift_pct) > 0.05
                    else "→ 実用上無視できる範囲。"
                )
            )
        else:
            analysis_lines.append("")
            analysis_lines.append("(全ての読み取り点のvideo_secが同一のため、傾きは計算できません)")

    analysis_lines.append("")
    analysis_lines.append("=== まとめ ===")
    if request_based is not None and confirmed_based is not None:
        dist_to_request = abs(mean_offset - request_based)
        dist_to_confirmed = abs(mean_offset - confirmed_based)
        closer = "delta_sec_request_based" if dist_to_request < dist_to_confirmed else "delta_sec_confirmed_based"
        analysis_lines.append(
            f"実測オフセット({mean_offset:.4f}秒)は "
            f"request_based(差={dist_to_request:.4f}s) と "
            f"confirmed_based(差={dist_to_confirmed:.4f}s) のうち、"
            f"{closer} に近い値でした。"
        )

    analysis_lines.append(
        "本番セッション(このオーバーレイを使わないセッション)の同期精度を"
        "報告する際は、上記いずれの推定値を採用したか、またはこの実測"
        "オフセットを固定の補正定数として適用したのかを明記してください。"
    )
    analysis_lines.append("")

    for line in analysis_lines:
        print(line)

    analyze_log_path = meta_path.with_name(
        meta_path.stem.replace("_meta", "") + "_analyze_log.txt"
    )
    _append_analysis_log(analyze_log_path, analysis_lines)
    print(f"analyze結果を追記しました: {analyze_log_path.resolve()}")


# ============================================================================
# 引数・エントリポイント
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Tobii-OBS 開始同期オフセットの実測用ツール(record/analyze)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="クロックオーバーレイ付きの検証セッションを記録する")
    rec.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    rec.add_argument("--duration-sec", type=float, default=20.0,
                      help="オーバーレイを表示し続ける秒数(既定: 20.0)")
    rec.add_argument("--target-fps", type=int, default=60)
    rec.add_argument("--gaze-first-sample-max-wait-sec", type=float, default=2.0)
    rec.add_argument("--obs-config", default=None)
    rec.add_argument("--obs-host", default=None)
    rec.add_argument("--obs-port", type=int, default=None)
    rec.add_argument("--obs-password", default=None)
    rec.add_argument("--obs-timeout-sec", type=float, default=None)
    rec.set_defaults(func=cmd_record)

    ana = sub.add_parser("analyze", help="録画を目視確認した結果からオフセットを算出する")
    ana.add_argument("--meta", required=True, help="recordで出力されたmetaJSONのパス")
    ana.add_argument(
        "--point", action="append", default=[],
        help="『動画再生位置(秒)=読み取ったT+の値(秒)』。複数指定可(例: --point 0.5=0.095)",
    )
    ana.set_defaults(func=cmd_analyze)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
