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

parser = argparse.ArgumentParser(description="指定した動画のフレームを対話的に確認するツール")
parser.add_argument("video_path", type=Path, nargs="?", help="確認対象の動画ファイルパス (例: data/raw/sample.mp4)")
args = parser.parse_args()

if not args.video_path:
    print("エラー: 動画ファイルのパスを指定してください。")
    print("使用法: python Checking_the_video.py <video_path>")
    sys.exit(1)

video_path = str(args.video_path)
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print("動画ファイルを開けませんでした。")
    exit()

# 動画の総フレーム数を取得
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
current_frame = 0

window_name = "Frame Viewer"
cv2.namedWindow(window_name)

print("【操作方法】")
print("  → キー: 1フレーム進む")
print("  ← キー: 1フレーム戻る")
print("  ESC キー: 終了")

while True:
    # 指定したフレーム位置に移動して読み込み
    cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
    ret, frame = cap.read()
    
    if ret:
        # ウィンドウに現在のフレーム番号を描画（任意）
        display_frame = frame.copy()
        text = f"Frame: {current_frame} / {total_frames - 1}"
        cv2.putText(display_frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        
        # 画面に表示
        cv2.imshow(window_name, display_frame)
    else:
        print(f"フレーム {current_frame} の読み込みに失敗しました。")

    # キー入力を待つ（Windows/Linux/Mac環境対応）
    # OpenCVのwaitKeyExを使用することで矢印キーの拡張キーコードを取得
    key = cv2.waitKeyEx(0)

    # プラットフォームによって矢印キーのコードが異なる場合があります
    # 一般的な環境（Windows + OpenCV）のキーコード:
    # 左矢印: 2424832, 右矢印: 2555904
    # Linux環境などでの標準キーコード: 左 81, 右 83
    
    if key in [2555904]: # 右矢印キー (→)
        if current_frame < total_frames - 1:
            current_frame += 1
    elif key in [2424832]: # 左矢印キー (←)
        if current_frame > 0:
            current_frame -= 1
    elif key == 27: # ESCキーで終了
        break

cap.release()
cv2.destroyAllWindows()