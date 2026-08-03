import csv
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import tobii_research as tr

OUTPUT_DIR = Path('.')
FILE_STAMP = datetime.now().strftime('%Y%m%d_%H%M%S')
CSV_FILE = OUTPUT_DIR / f'gaze_data_{FILE_STAMP}.csv'
BUFFER_SIZE = 200
RECORD_SECONDS = 13
USE_CALIBRATION = False

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
]

buffer = deque()
start_perf = None
writer_file = None
writer = None
running = True
my_eyetracker = None


def open_writer():
    global writer_file, writer
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    writer_file = open(CSV_FILE, 'w', newline='', encoding='utf-8-sig')
    writer = csv.DictWriter(writer_file, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer_file.flush()


def close_writer():
    global writer_file, writer
    if writer_file is not None:
        writer_file.close()
    writer_file = None
    writer = None


def flush_buffer():
    if not buffer or writer is None:
        return
    writer.writerows(buffer)
    writer_file.flush()
    buffer.clear()


def safe_xy(value):
    if value is None:
        return None, None
    try:
        return value[0], value[1]
    except Exception:
        return None, None


def average_xy(left_x, left_y, left_valid, right_x, right_y, right_valid):
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


def gaze_data_callback(gaze_data):
    now_local = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)
    pc_time_sec = time.perf_counter() - start_perf

    left_x, left_y = safe_xy(gaze_data.get('left_gaze_point_on_display_area'))
    right_x, right_y = safe_xy(gaze_data.get('right_gaze_point_on_display_area'))

    left_valid = gaze_data.get('left_gaze_point_validity')
    right_valid = gaze_data.get('right_gaze_point_validity')

    center_x, center_y = average_xy(
        left_x, left_y, left_valid,
        right_x, right_y, right_valid,
    )

    row = {
        'wall_timestamp_local': now_local.isoformat(timespec='milliseconds'),
        'wall_timestamp_utc': now_utc.isoformat(timespec='milliseconds'),
        'pc_time_sec': round(pc_time_sec, 6),
        'device_time_stamp_us': gaze_data.get('device_time_stamp'),
        'system_time_stamp_us': gaze_data.get('system_time_stamp'),
        'left_x': left_x,
        'left_y': left_y,
        'right_x': right_x,
        'right_y': right_y,
        'center_x': center_x,
        'center_y': center_y,
        'left_gaze_point_validity': left_valid,
        'right_gaze_point_validity': right_valid,
    }

    buffer.append(row)
    if len(buffer) >= BUFFER_SIZE:
        flush_buffer()


def stop_handler(signum=None, frame=None):
    global running
    running = False


def optional_calibration(eyetracker):
    if not USE_CALIBRATION:
        return
    calibration = tr.ScreenBasedCalibration(eyetracker)
    calibration.enter_calibration_mode()
    points_to_calibrate = [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)]
    for x, y in points_to_calibrate:
        print(f'Collect calibration point: {(x, y)}')
        time.sleep(1.0)
        calibration.collect_data(x, y)
    result = calibration.compute_and_apply()
    print(f'Calibration status: {result.status}')
    calibration.leave_calibration_mode()


def main():
    global start_perf, my_eyetracker

    signal.signal(signal.SIGINT, stop_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, stop_handler)

    found_eyetrackers = tr.find_all_eyetrackers()
    if not found_eyetrackers:
        print('アイトラッカーが見つかりませんでした。')
        sys.exit(1)

    my_eyetracker = found_eyetrackers[0]
    print(f'接続デバイス: {my_eyetracker.device_name}')
    print(f'モデル: {my_eyetracker.model}')
    print(f'保存先: {CSV_FILE.resolve()}')

    optional_calibration(my_eyetracker)
    open_writer()
    start_perf = time.perf_counter()

    my_eyetracker.subscribe_to(
        tr.EYETRACKER_GAZE_DATA,
        gaze_data_callback,
        as_dictionary=True,
    )

    print('視線データの取得を開始しました... Ctrl+C で停止できます。')

    try:
        end_time = start_perf + RECORD_SECONDS if RECORD_SECONDS is not None else None
        while running:
            time.sleep(0.05)
            if end_time is not None and time.perf_counter() >= end_time:
                break
    finally:
        try:
            my_eyetracker.unsubscribe_from(tr.EYETRACKER_GAZE_DATA, gaze_data_callback)
        except Exception:
            pass
        flush_buffer()
        close_writer()
        print('ストリーミングを停止しました。')
        print(f'CSV保存完了: {CSV_FILE.resolve()}')


if __name__ == '__main__':
    main()
