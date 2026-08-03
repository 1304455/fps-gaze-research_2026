# -*- coding: utf-8 -*-
"""
obs_controller.py

Tobii Pro Spark 視線計測プログラム向け OBS WebSocket v5 (obsws-python) 制御モジュール。

準拠要件:
    obs_reqclient_requirements.md
    (「Tobii Pro Spark 測定プログラムへの obsws-python ReqClient 組み込み要件定義書」)

本モジュールの役割 (要件定義書の対象範囲に対応):
    F-1  OBS接続設定 (host/port/password, 既定値, 認証失敗時のログ)
    F-2  OBS録画開始 (ReqClient, obs_start_request_utc, 二重開始スキップ)
    F-3  OBS録画停止 (ReqClient, obs_stop_request_utc, 未録画時の非エラー扱い)
    F-4  Tobii計測開始との一元制御 (呼び出し順序は main側で保証。本モジュールは
         各時刻の記録責務のみを負う)
    F-5  OBS録画状態確認 (EventClient, callback.register(...) 方式)
    F-6  ログ保存 (JSON Lines形式でのOBS同期ログ)
    F-7  開始失敗時の扱い (obs_require_success, 既定=True)
    F-8  停止失敗時の扱い (例外を外に伝播させず bool を返す設計とし、
         呼び出し側でのTobii writer close/flushを妨げない)
    F-9  手動録画との排他 (EventClient経由での想定外の状態変化検知)

対象外 (要件定義書「対象範囲」節を参照。本モジュールでは一切扱わない):
    - OBSの配信(ストリーミング)開始・停止制御
    - OBSシーン切り替え・ソース表示切り替え
    - Tobii SDK自体のデータ取得ロジック
    - 映像フレーム単位の厳密ハードウェア同期保証
      (本モジュールが目的とするのはUTCログ差分による後処理同期評価であり、
       フレーム完全一致の保証ではない。既存の SyncFlashWindow による
       視覚フラッシュマーカーが主たる同期手段であることに変わりはない。)

必要パッケージ:
    pip install obsws-python

前提: OBS Studio 28以降 (WebSocket v5標準搭載)。Tools > WebSocket Server Settings
で「Enable WebSocket server」を有効化し、ポート/パスワードを確認しておくこと。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---- obsws-python の安全なインポート ---------------------------------------
# 未インストールでもモジュール自体はimport可能にしておき、
# 実際の接続試行時(connect_obs)にわかりやすいエラーとして扱う。
try:
    import obsws_python as obsws
    from obsws_python.error import OBSSDKError, OBSSDKTimeoutError, OBSSDKRequestError
    OBSWS_AVAILABLE = True
except ImportError:
    obsws = None

    class OBSSDKError(Exception):
        pass

    class OBSSDKTimeoutError(OBSSDKError):
        pass

    class OBSSDKRequestError(OBSSDKError):
        req_name = None
        code = None

    OBSWS_AVAILABLE = False


DEFAULT_OBS_HOST = "localhost"
DEFAULT_OBS_PORT = 4455
DEFAULT_OBS_TIMEOUT_SEC = 3.0

# OBS WebSocket v5 RecordStateChanged の outputState 文字列定数
_STATE_STARTED = "OBS_WEBSOCKET_OUTPUT_STARTED"
_STATE_STOPPED = "OBS_WEBSOCKET_OUTPUT_STOPPED"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ============================================================================
# 設定 (F-1 / インターフェース要件「起動設定」)
# ============================================================================

@dataclass
class ObsConfig:
    obs_enabled: bool = True
    obs_host: str = DEFAULT_OBS_HOST
    obs_port: int = DEFAULT_OBS_PORT
    obs_password: Optional[str] = None
    obs_require_success: bool = True          # F-7 既定: 開始失敗=セッション開始失敗
    obs_wait_for_started_event: bool = True   # F-5: 開始/停止確認イベントを待つか
    obs_timeout_sec: float = DEFAULT_OBS_TIMEOUT_SEC

    @classmethod
    def load(cls, config_path: "Optional[str]", overrides: dict) -> "ObsConfig":
        """設定ファイル(JSON)を読み込み、overridesで上書きしてインスタンス化する。

        overrides の値が None のキーは「未指定」として扱い、ファイル値/既定値を
        優先する(CLI引数側でstore_true/store_falseのdefaultをNoneにしておくことで、
        「明示的に指定されたときだけ上書きする」挙動を実現する)。
        """
        data = {}
        if config_path:
            p = Path(config_path)
            if not p.exists():
                raise FileNotFoundError(f"OBS設定ファイルが見つかりません: {config_path}")
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)

        for key, val in overrides.items():
            if val is not None:
                data[key] = val

        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(data.keys()) - valid_keys
        if unknown:
            logging.warning(f"OBS設定に未知のキーがあります（無視します）: {sorted(unknown)}")
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# ============================================================================
# 同期記録 (F-6 ログ保存項目)
# ============================================================================

@dataclass
class ObsSyncRecord:
    session_id: str
    session_trigger_utc: Optional[str] = None
    tobii_subscribe_utc: Optional[str] = None
    obs_start_request_utc: Optional[str] = None
    obs_record_started_utc: Optional[str] = None
    obs_stop_request_utc: Optional[str] = None
    obs_record_stopped_utc: Optional[str] = None
    obs_output_path: Optional[str] = None
    # 要件定義書 F-6 の表に合わせて単一カラムとしているが、接続・開始・停止の
    # 3フェーズはそれぞれ独立に成功/失敗しうる(例: 開始は成功したが停止だけ失敗、
    # というケースは通常運用でも普通に起こる)。単純に最後に発生したイベントで
    # 上書きすると、開始が成功していた事実が失われてしまう。そのため実体は
    # ObsController側で "connect:xxx;start:xxx;stop:xxx" のような
    # フェーズ別の合成文字列として組み立てる(_recompute_control_result参照)。
    # error_messageも同様に、フェーズ毎のメッセージを " / " 区切りで連結する。
    obs_control_result: str = "not_attempted"
    error_message: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# OBSコントローラ本体
# ============================================================================

class ObsController:
    """obsws-pythonのReqClient(要求送信)/EventClient(状態確認)を用いて
    OBS Studioの録画を制御する。既存のTobii視線取得ロジックからは独立した
    モジュールとして実装している (N-2 保守性)。

    プログラム内API (インターフェース要件に対応):
        connect_obs()
        start_obs_recording()
        stop_obs_recording()
        register_obs_event_handlers()
        write_obs_sync_log()
        disconnect_obs()

    start_session()/stop_session() 相当の全体シーケンスはmain側
    (tobii_capture_with_sync_flash_v2.py) が制御する。既存コードは
    開始・停止をHTTPリモートトリガと内部イベントで制御しているため、
    その開始確定箇所へ本クラスの呼び出しを挿入する構成としている。
    """

    def __init__(self, config: ObsConfig, session_id: str):
        self.config = config
        self.record = ObsSyncRecord(session_id=session_id)
        self.anomalies: "list[dict]" = []

        self._req_client = None
        self._event_client = None
        self._event_lock = threading.Lock()
        self._started_event = threading.Event()
        self._stopped_event = threading.Event()
        # F-9: 自分自身が発行した録画開始/停止要求の確認待ち状態。
        #   None    = 要求していない(待ち状態ではない)
        #   "start" = 開始要求発行済み・STARTED確認待ち
        #   "stop"  = 停止要求発行済み・STOPPED確認待ち
        self._pending_transition: "Optional[str]" = None
        self._sync_log_lines: "list[dict]" = []

        # obs_control_result / error_message はconnect/start/stopの3フェーズを
        # 合成した結果であるため、各フェーズの結果を個別に保持しておく
        # (詳細は ObsSyncRecord のコメント、および _recompute_control_result を参照)。
        self._phase_results: "dict[str, Optional[str]]" = {
            "connect": None, "start": None, "stop": None,
        }
        self._phase_errors: "dict[str, Optional[str]]" = {
            "connect": None, "start": None, "stop": None,
        }

    # ------------------------------------------------------------------
    # フェーズ別結果の記録・合成
    #
    # obs_control_result / error_message は要件定義書 F-6 の表では単一カラム
    # だが、実際には「接続」「開始」「停止」は独立に成功・失敗しうる
    # (最も典型的には: 開始は成功したが、セッション終了時の停止要求だけが
    #  タイムアウトする、というケース)。フェーズ単位で結果を保持しておき、
    # 都度合成することで、後から発生したフェーズの結果が先のフェーズの
    # 成功/失敗を消してしまう事故を防ぐ。
    # ------------------------------------------------------------------
    def _set_phase_result(self, phase: str, result: str, error: "Optional[str]" = None) -> None:
        self._phase_results[phase] = result
        self._phase_errors[phase] = error
        self._recompute_control_result()

    def _recompute_control_result(self) -> None:
        parts = []
        errors = []
        for phase in ("connect", "start", "stop"):
            result = self._phase_results.get(phase)
            if result is None:
                continue
            parts.append(f"{phase}:{result}")
            err = self._phase_errors.get(phase)
            if err:
                errors.append(f"{phase}: {err}")
        self.record.obs_control_result = ";".join(parts) if parts else "not_attempted"
        self.record.error_message = " / ".join(errors) if errors else None

    # ------------------------------------------------------------------
    # F-1: 接続
    # ------------------------------------------------------------------
    def connect_obs(self) -> bool:
        if not OBSWS_AVAILABLE:
            msg = ("obsws-python がインストールされていません。"
                   "`pip install obsws-python` を実行してください。")
            logging.error(f"[OBS] {msg}")
            self._set_phase_result("connect", "obsws_python_not_installed", msg)
            self._log_sync_event("connect_failed", error=msg)
            return False

        try:
            self._req_client = obsws.ReqClient(
                host=self.config.obs_host,
                port=self.config.obs_port,
                password=self.config.obs_password,
                timeout=self.config.obs_timeout_sec,
            )
            version = self._req_client.get_version()
            obs_ver = getattr(version, "obs_version", "不明")
            ws_ver = getattr(version, "obs_web_socket_version", "不明")
            logging.info(
                f"[OBS] 接続成功: host={self.config.obs_host} port={self.config.obs_port} "
                f"obs_version={obs_ver} websocket_version={ws_ver}"
            )
            self._log_sync_event("connected", obs_version=obs_ver, websocket_version=ws_ver)
        except OBSSDKTimeoutError as e:
            msg = (f"OBS接続がタイムアウトしました(host={self.config.obs_host}, "
                   f"port={self.config.obs_port})。OBSが起動しているか確認してください。詳細: {e}")
            self._connect_error("connect_timeout", msg, e)
            return False
        except OBSSDKError as e:
            msg = (f"OBSへの認証/識別に失敗しました(host={self.config.obs_host}, "
                   f"port={self.config.obs_port})。OBS側 Tools > WebSocket Server Settings の"
                   f"パスワード設定と一致しているか確認してください。詳細: {e}")
            self._connect_error("auth_failed", msg, e)
            return False
        except (ConnectionRefusedError, OSError) as e:
            msg = (f"OBSに接続できませんでした(host={self.config.obs_host}, "
                   f"port={self.config.obs_port})。OBS Studioの起動状態、WebSocketサーバの"
                   f"有効化設定、ポート番号を確認してください。詳細: {e}")
            self._connect_error("connect_refused", msg, e)
            return False
        except Exception as e:  # noqa: BLE001 (診断性優先。分類できない失敗も必ずログに残す)
            msg = f"OBS接続時に未分類の例外が発生しました: {e}"
            self._connect_error("connect_error", msg, e)
            return False

        # EventClientはベストエフォート接続 (F-5)。失敗してもReqClientによる
        # 制御自体は継続できるようにする。
        self._connect_event_client()
        return True

    def _connect_error(self, result_code: str, msg: str, exc: Exception) -> None:
        logging.error(f"[OBS] {msg}")
        self._set_phase_result("connect", result_code, str(exc))
        self._log_sync_event("connect_failed", error=msg)

    def _connect_event_client(self) -> None:
        try:
            self._event_client = obsws.EventClient(
                host=self.config.obs_host,
                port=self.config.obs_port,
                password=self.config.obs_password,
                timeout=self.config.obs_timeout_sec,
            )
            self.register_obs_event_handlers()
            logging.info("[OBS] EventClient接続成功。録画状態変化イベントを監視します。")
            self._log_sync_event("event_client_connected")
        except Exception as e:
            logging.warning(
                "[OBS] EventClientへの接続に失敗しました。録画開始/停止の確認イベントは"
                f"取得できません。ReqClientによる要求送信時刻ログのみで継続します。詳細: {e}"
            )
            self._log_sync_event("event_client_connect_failed", error=str(e))
            self._event_client = None

    # ------------------------------------------------------------------
    # F-5: イベント登録
    # 方針: @ws.event_callback ではなく callback.register(...) を用いる。
    # ------------------------------------------------------------------
    def register_obs_event_handlers(self) -> None:
        if self._event_client is None:
            return
        self._event_client.callback.register(self.on_record_state_changed)

    def on_record_state_changed(self, data) -> None:
        """RecordStateChanged イベントハンドラ。

        callback.register(...) 経由で登録するため、obsws-pythonの命名規則に従い
        関数名は "on_" + イベント名のスネークケースとしている。
        """
        state = getattr(data, "output_state", "")
        output_path = getattr(data, "output_path", None)
        now_utc = _utc_now_iso()

        with self._event_lock:
            if state == _STATE_STARTED:
                expected = self._pending_transition == "start"
                self.record.obs_record_started_utc = now_utc
                self._started_event.set()
                if expected:
                    self._pending_transition = None
                    logging.info(f"[OBS] 録画開始を確認しました。utc={now_utc}")
                    self._log_sync_event("record_started_confirmed", wall_utc=now_utc)
                else:
                    self._register_manual_operation("start", now_utc)
            elif state == _STATE_STOPPED:
                expected = self._pending_transition == "stop"
                self.record.obs_record_stopped_utc = now_utc
                if output_path:
                    self.record.obs_output_path = output_path
                self._stopped_event.set()
                if expected:
                    self._pending_transition = None
                    logging.info(
                        f"[OBS] 録画停止を確認しました。utc={now_utc} output_path={output_path}"
                    )
                    self._log_sync_event(
                        "record_stopped_confirmed", wall_utc=now_utc, output_path=output_path,
                    )
                else:
                    self._register_manual_operation("stop", now_utc, output_path=output_path)
            else:
                # STARTING / STOPPING 等の遷移中状態は異常系ではないためログのみ。
                logging.debug(f"[OBS] RecordStateChanged: output_state={state}")

    def _register_manual_operation(self, direction: str, when_utc: str, output_path=None) -> None:
        """F-9: 自プログラムが要求していない録画状態変化(=OBS側GUIからの手動操作、
        または外部要因による外乱の可能性)を検知した際の異常系記録。
        """
        entry = {
            "type": "obs_manual_operation_detected",
            "direction": direction,  # "start" または "stop"
            "wall_utc": when_utc,
            "output_path": output_path,
        }
        self.anomalies.append(entry)
        logging.warning(
            f"[OBS][異常系] 本プログラムが要求していない録画状態変化を検知しました "
            f"(direction={direction}, utc={when_utc})。OBS側の手動操作の可能性があります。"
        )
        self._log_sync_event("manual_operation_detected", **entry)

    # ------------------------------------------------------------------
    # F-2: 録画開始
    # ------------------------------------------------------------------
    def start_obs_recording(self) -> bool:
        if self._req_client is None:
            self._set_phase_result("start", "not_connected")
            return False

        try:
            status = self._req_client.get_record_status()
            if getattr(status, "output_active", False):
                logging.warning("[OBS] 既に録画中のため、開始要求をスキップしました。")
                self._set_phase_result("start", "skipped_already_recording")
                self._log_sync_event("start_skipped_already_recording")
                return True
        except Exception as e:
            logging.warning(f"[OBS] 録画状態の事前確認に失敗しました（開始要求は続行します）: {e}")

        with self._event_lock:
            self._pending_transition = "start"
        self._started_event.clear()

        try:
            self._req_client.start_record()
            self.record.obs_start_request_utc = _utc_now_iso()
            logging.info(f"[OBS] 録画開始要求を送信しました。utc={self.record.obs_start_request_utc}")
            self._log_sync_event("start_request_sent", wall_utc=self.record.obs_start_request_utc)
        except OBSSDKRequestError as e:
            msg = f"OBS録画開始要求が失敗しました(req={e.req_name}, code={e.code}): {e}"
            self._request_error("start", "start_request_failed", msg)
            return False
        except Exception as e:
            msg = f"OBS録画開始要求で例外が発生しました: {e}"
            self._request_error("start", "start_request_failed", msg)
            return False

        self._wait_for_confirmation(self._started_event, "start")
        return True

    # ------------------------------------------------------------------
    # F-3: 録画停止
    # ------------------------------------------------------------------
    def stop_obs_recording(self) -> bool:
        if self._req_client is None:
            self._set_phase_result("stop", "not_connected")
            return False

        try:
            status = self._req_client.get_record_status()
            if not getattr(status, "output_active", True):
                logging.info("[OBS] 録画中ではないため、停止要求は不要として扱います。")
                self._set_phase_result("stop", "skipped_not_recording")
                self._log_sync_event("stop_skipped_not_recording")
                return True
        except Exception as e:
            logging.warning(f"[OBS] 録画状態の事前確認に失敗しました（停止要求は続行します）: {e}")

        with self._event_lock:
            self._pending_transition = "stop"
        self._stopped_event.clear()

        try:
            resp = self._req_client.stop_record()
            self.record.obs_stop_request_utc = _utc_now_iso()
            # StopRecordの応答には録画ファイルパスが同期的に含まれる
            # (RecordStateChangedイベントのoutputPathは仕様上STOPPED遷移時のみ
            #  埋まるため、応答値の方が取りこぼしがなく確実)。
            output_path = getattr(resp, "output_path", None)
            if output_path:
                self.record.obs_output_path = output_path
            logging.info(
                f"[OBS] 録画停止要求を送信しました。utc={self.record.obs_stop_request_utc} "
                f"output_path={output_path}"
            )
            self._log_sync_event(
                "stop_request_sent", wall_utc=self.record.obs_stop_request_utc,
                output_path=output_path,
            )
        except OBSSDKRequestError as e:
            msg = f"OBS録画停止要求が失敗しました(req={e.req_name}, code={e.code}): {e}"
            self._request_error("stop", "stop_request_failed", msg)
            return False
        except Exception as e:
            msg = f"OBS録画停止要求で例外が発生しました: {e}"
            self._request_error("stop", "stop_request_failed", msg)
            return False

        self._wait_for_confirmation(self._stopped_event, "stop")
        return True

    def _request_error(self, phase: str, event_name: str, msg: str) -> None:
        logging.error(f"[OBS] {msg}")
        self._set_phase_result(phase, "failed", msg)
        self._log_sync_event(event_name, error=msg)

    def _wait_for_confirmation(self, event: threading.Event, direction: str) -> None:
        """F-5: 開始/停止確認イベントを待つ(設定で無効化可能)。
        イベント未受信でも要求自体は成功しているため、control_resultを
        success_unconfirmed とした上で処理を継続する(F-5 フェイルセーフ)。
        """
        if not (self.config.obs_wait_for_started_event and self._event_client is not None):
            self._set_phase_result(direction, "success_unconfirmed")
            return

        confirmed = event.wait(timeout=self.config.obs_timeout_sec)
        if confirmed:
            self._set_phase_result(direction, "success_confirmed")
        else:
            msg = (
                f"録画{'開始' if direction == 'start' else '停止'}確認イベントを"
                f"{self.config.obs_timeout_sec}秒以内に受信できませんでした。"
                f"要求送信時刻のみを記録して処理を継続します。"
            )
            logging.warning(f"[OBS] {msg}")
            self._set_phase_result(direction, "success_unconfirmed", msg)
            self._log_sync_event(f"{direction}_confirmation_timeout", error=msg)

    # ------------------------------------------------------------------
    # 内部ログ蓄積 (F-6)
    # ------------------------------------------------------------------
    def _log_sync_event(self, event: str, **fields) -> None:
        entry = {"event": event, "wall_utc": _utc_now_iso(), **fields}
        self._sync_log_lines.append(entry)

    # ------------------------------------------------------------------
    # F-6: ログ保存 (JSON Lines)
    # ------------------------------------------------------------------
    def write_obs_sync_log(self, output_dir: Path, base_name: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / f"{base_name}_obs_sync.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in self._sync_log_lines:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            summary = {"event": "summary", **self.record.as_dict()}
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        return log_path

    # ------------------------------------------------------------------
    # 後始末
    # ------------------------------------------------------------------
    def disconnect_obs(self) -> None:
        if self._event_client is not None:
            try:
                self._event_client.disconnect()
            except Exception as e:
                logging.debug(f"[OBS] EventClient切断時の例外（無視）: {e}")
        if self._req_client is not None:
            try:
                self._req_client.disconnect()
            except Exception as e:
                logging.debug(f"[OBS] ReqClient切断時の例外（無視）: {e}")
