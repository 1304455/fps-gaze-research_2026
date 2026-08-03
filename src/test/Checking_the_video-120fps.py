import argparse
import sys
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

parser = argparse.ArgumentParser(description="指定した動画を120フレームごとに間引き確認するツール")
parser.add_argument("video_path", type=Path, nargs="?", help="確認対象の動画ファイルパス (例: data/raw/sample.mp4)")
args = parser.parse_args()

if not args.video_path:
    print("エラー: 動画ファイルのパスを指定してください。")
    print("使用法: python Checking_the_video-120fps.py <video_path>")
    sys.exit(1)

video_path = str(args.video_path)
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print("動画ファイルを開けませんでした。")
    exit()

# FPSと総フレーム数を取得
fps = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

if fps <= 0:
    print("FPSを取得できませんでした。")
    cap.release()
    exit()

# 18秒地点までのフレーム番号
end_frame = int(fps * 18)

# 動画の長さより長い場合に備えて補正
end_frame = min(end_frame, total_frames - 1)

window_name = "Frame Viewer"
cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

print("0秒から18秒地点まで、120フレームごとに表示します。")
print("何かキーを押すと次の画像へ進みます。")
print("ESCキーで終了します。")

# 0フレーム目から120フレームごとに走査
for current_frame in range(0, end_frame + 1, 120):
    cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
    ret, frame = cap.read()

    if not ret:
        print(f"フレーム {current_frame} の読み込みに失敗しました。")
        continue

    # 表示用テキスト
    current_time = current_frame / fps
    display_frame = frame.copy()
    text1 = f"Frame: {current_frame} / {total_frames - 1}"
    text2 = f"Time: {current_time:.2f} sec"
    cv2.putText(display_frame, text1, (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.putText(display_frame, text2, (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

    cv2.imshow(window_name, display_frame)

    key = cv2.waitKeyEx(0)
    if key == 27:  # ESCキー
        break

cap.release()
cv2.destroyAllWindows()