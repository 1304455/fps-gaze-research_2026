import time
import csv
from datetime import datetime, timezone

import obsws_python as obs

CSV_PATH = "record_start_log.csv"

def init_csv():
    try:
        with open(CSV_PATH, "r", newline="", encoding="utf-8") as f:
            pass
    except FileNotFoundError:
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["utc_timestamp", "event", "note"])

def log_utc(event_name, note=""):
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([now_utc, event_name, note])
    print(f"{event_name}: {now_utc}  {note}")

def on_record_state_changed(data):
    """
    RecordStateChanged イベント用コールバック。
    data.output_state などの属性を使って録画開始／停止を判定する。[web:17]
    """
    print("RecordStateChanged:", data.output_state)

    # 録画開始を検知
    if data.output_state == "OBS_WEBSOCKET_OUTPUT_STARTED":
        log_utc("RecordStarted", note=f"path={getattr(data, 'output_path', '')}")

def main():
    init_csv()

    # EventClient は config.toml から接続設定を読むのが公式推奨ですが[web:31]、
    # host / port / password を直指定しても動きます。
    client_e = obs.EventClient(
        host="localhost",
        port=4455,
        password="TtAKpdZt72WkF9ED",
    )

    # ここがポイント：デコレータではなく register() で登録する。[web:31][web:17]
    client_e.callback.register(on_record_state_changed)

    print("OBS録画状態イベントを待機中...")
    # client_e は内部でイベントループを持っているので、ここでは単純に待機
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("終了します。")

if __name__ == "__main__":
    main()