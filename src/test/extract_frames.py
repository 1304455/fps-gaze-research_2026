import cv2

def extract_frame_by_number(video_path, frame_number, output_image_path):
    """
    指定したフレーム番号の画像を抽出して保存する
    """
    # 動画ファイルを読み込む
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print(f"エラー: 動画ファイル '{video_path}' を開けませんでした。")
        return False

    # 指定したフレーム位置に移動
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

    # フレームを取得
    ret, frame = cap.read()

    if ret:
        # 画像として保存
        cv2.imwrite(output_image_path, frame)
        print(f"成功: フレーム {frame_number} を '{output_image_path}' に保存しました。")
        success = True
    else:
        print(f"エラー: フレーム {frame_number} の取得に失敗しました。")
        success = False

    # キャプチャを解放
    cap.release()
    return success


def extract_frame_by_time(video_path, time_sec, output_image_path):
    """
    指定した時間（秒）の画像を抽出して保存する
    """
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print(f"エラー: 動画ファイル '{video_path}' を開けませんでした。")
        return False

    # FPS（フレームレート）を取得して、指定秒数に相当するフレーム番号を計算
    fps = cap.get(cv2.CAP_PROP_FPS)
    target_frame = int(fps * time_sec)

    # フレーム位置を指定して取得
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()

    if ret:
        cv2.imwrite(output_image_path, frame)
        print(f"成功: {time_sec}秒目（フレーム {target_frame}）を '{output_image_path}' に保存しました。")
        success = True
    else:
        print(f"エラー: {time_sec}秒目のフレーム取得に失敗しました。")
        success = False

    cap.release()
    return success


# --- 実行例 ---
if __name__ == "__main__":
    input_video = r"C:\Users\kawalab\Videos\2026-08-03 16-27-20.mp4"       # 入力するMP4ファイル名

    extract_frame_by_time(input_video, time_sec=190, output_image_path="time_310.png")

    """
    # 例1: 100フレーム目を「frame_100.jpg」として保存
    extract_frame_by_number(input_video, frame_number=100, output_image_path="frame_100.jpg")

    # 例2: 5.5秒目のフレームを「time_5.5s.png」として保存
    extract_frame_by_time(input_video, time_sec=5.5, output_image_path="time_5.5s.png")
    """