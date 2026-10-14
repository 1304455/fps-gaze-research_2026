# -*- coding: utf-8 -*-
"""テスト共通設定。ハイフン付きファイル名を避けるため、ロジック本体のモジュールを直接 import できるようにする。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("src/aoi_detector", "src/visualizer", "src/gaze_estimation", "src/analysis"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
