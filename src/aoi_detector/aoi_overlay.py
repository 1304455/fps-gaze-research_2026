import sys
import json
import threading
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtGui import QPainter, QColor, QPen, QFont
from PyQt5.QtCore import Qt, QRectF, QPointF


class AOIOverlay(QMainWindow):
    def __init__(self, aoi_config_path: str, screen):
        super().__init__()

        # AOI設定を読み込み
        cfg_path = Path(aoi_config_path)
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.aois = cfg.get("aois", [])
        if not self.aois:
            raise ValueError("JSON内に 'aois' がありません。")

        # 選択したスクリーンのジオメトリからサイズを取得
        geom = screen.geometry()
        self.screen_width = geom.width()
        self.screen_height = geom.height()
        self.screen_left = geom.left()
        self.screen_top = geom.top()

        # ウィンドウ設定：枠なし・常時最前面
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        # 透明背景
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        # 選択したスクリーンの位置とサイズに合わせて配置
        self.setGeometry(
            self.screen_left,
            self.screen_top,
            self.screen_width,
            self.screen_height,
        )

        # フォント
        self.font = QFont("Arial", 14)

        # AOIごとの色
        self.colors = [
            QColor(255, 0, 0, 160),     # 赤 (半透明)
            QColor(0, 128, 0, 160),     # 緑
            QColor(0, 0, 255, 160),     # 青
            QColor(255, 165, 0, 160),   # オレンジ
            QColor(128, 0, 128, 160),   # 紫
        ]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font)

        for i, aoi in enumerate(self.aois):
            color = self.colors[i % len(self.colors)]

            # 正規化座標(0〜1)→画面ピクセルへ変換
            x0 = aoi["x_min"] * self.screen_width
            x1 = aoi["x_max"] * self.screen_width
            y0 = aoi["y_min"] * self.screen_height
            y1 = aoi["y_max"] * self.screen_height

            rect = QRectF(x0, y0, x1 - x0, y1 - y0)

            # 枠線（半透明）
            pen = QPen(color)
            pen.setWidth(3)
            painter.setPen(pen)

            # 塗りつぶしは薄い透明色
            fill_color = QColor(color.red(), color.green(), color.blue(), 40)
            painter.setBrush(fill_color)
            painter.drawRect(rect)

            # AOI名のラベル
            name = aoi.get("name", f"aoi_{i}")
            text_color = QColor(color.red(), color.green(), color.blue(), 200)
            painter.setPen(text_color)
            painter.drawText(rect.topLeft() + QPointF(5, 20), name)


def input_thread(app):
    """
    別スレッドでターミナルからの入力を待ち、
    Enter が押されたらアプリを終了する。
    """
    print("オーバレイを終了するには Enter キーを押してください...")
    input()
    print("終了要求を受け取りました。オーバレイを閉じます。")
    app.quit()


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="ディスプレイ選択＆AOIオーバレイ表示＋Enterで終了"
    )
    parser.add_argument(
        "--aoi",
        required=True,
        help="AOI設定JSONファイル（正規化座標: x_min/x_max/y_min/y_max）",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)

    # 利用可能なディスプレイ一覧を取得
    screens = app.screens()
    if not screens:
        print("ディスプレイが取得できませんでした。")
        sys.exit(1)

    print("検出されたディスプレイ一覧:")
    for idx, s in enumerate(screens):
        geom = s.geometry()
        print(
            f"[{idx}] name={s.name()}, "
            f"pos=({geom.left()},{geom.top()}), "
            f"size={geom.width()}x{geom.height()}"
        )  # [web:54][web:58]

    # ターミナルから対象ディスプレイ番号を入力
    while True:
        try:
            choice = int(input("AOIオーバレイを表示するディスプレイ番号を入力してください: "))
        except ValueError:
            print("数字で入力してください。")
            continue

        if 0 <= choice < len(screens):
            target_screen = screens[choice]
            break
        else:
            print("範囲外の番号です。もう一度入力してください。")

    # 選択したディスプレイ上にオーバレイ表示
    overlay = AOIOverlay(args.aoi, target_screen)
    overlay.show()

    # Enterで終了するためのスレッドを起動
    t = threading.Thread(target=input_thread, args=(app,), daemon=True)
    t.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()