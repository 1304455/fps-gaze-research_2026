import argparse
import json
import sys
import threading
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QMainWindow


class AOIOverlay(QMainWindow):
    def __init__(self, aoi_config_path: str, screen):
        super().__init__()
        with Path(aoi_config_path).open("r", encoding="utf-8") as f:
            self.aois = json.load(f).get("aois", [])
        if not self.aois:
            raise ValueError("JSON内に 'aois' がありません。")

        geom = screen.geometry()
        self.screen_width = geom.width()
        self.screen_height = geom.height()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(geom)
        self.font = QFont("Arial", 14)
        self.colors = [
            QColor(255, 0, 0, 160), QColor(0, 128, 0, 160),
            QColor(0, 0, 255, 160), QColor(255, 165, 0, 160),
            QColor(128, 0, 128, 160),
        ]

    def aoi_rect(self, aoi):
        if aoi.get("type", "rectangle") == "circle":
            cx = aoi["center_x"] * self.screen_width
            cy = aoi["center_y"] * self.screen_height
            rx = aoi["radius_x"] * self.screen_width
            ry = aoi["radius_y"] * self.screen_height
            return QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry)
        x0 = aoi["x_min"] * self.screen_width
        x1 = aoi["x_max"] * self.screen_width
        y0 = aoi["y_min"] * self.screen_height
        y1 = aoi["y_max"] * self.screen_height
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font)

        for i, aoi in enumerate(self.aois):
            color = self.colors[i % len(self.colors)]
            rect = self.aoi_rect(aoi)
            painter.setPen(QPen(color, 3))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
            if aoi.get("type", "rectangle") == "circle":
                painter.drawEllipse(rect)
            else:
                painter.drawRect(rect)
            painter.setPen(QColor(color.red(), color.green(), color.blue(), 200))
            painter.drawText(rect.topLeft() + QPointF(5, 20), aoi.get("name", f"aoi_{i}"))


def input_thread(app):
    print("オーバレイを終了するには Enter キーを押してください...")
    input()
    app.quit()


def main():
    parser = argparse.ArgumentParser(description="AOI形状を画面上にオーバーレイ表示します。")
    parser.add_argument("--aoi", required=True, help="AOI設定JSONファイル")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    screens = app.screens()
    if not screens:
        raise RuntimeError("ディスプレイが取得できませんでした。")
    for i, screen in enumerate(screens):
        geom = screen.geometry()
        print(f"[{i}] {screen.name()} pos=({geom.left()},{geom.top()}) size={geom.width()}x{geom.height()}")
    while True:
        try:
            choice = int(input("AOIオーバレイを表示するディスプレイ番号: "))
            if 0 <= choice < len(screens):
                break
        except ValueError:
            pass
        print("一覧にある番号を入力してください。")

    overlay = AOIOverlay(args.aoi, screens[choice])
    overlay.show()
    threading.Thread(target=input_thread, args=(app,), daemon=True).start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
