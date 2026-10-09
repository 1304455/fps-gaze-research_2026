# -*- coding: utf-8 -*-
"""segment_annotator のイベント→区間変換のテスト（UI は対象外）。"""
import pandas as pd

import gaze_metrics as gm
from segment_annotator import events_to_segments, write_segments_csv


def ev(kind, t, side="attack"):
    return {"kind": kind, "video_sec": t, "side": side}


def test_basic_rounds():
    rows, warns = events_to_segments([
        ev("buy_end", 30.0), ev("death", 55.5),
        ev("buy_end", 130.0, "attack"), ev("round_end", 200.0),
    ])
    assert warns == []
    assert [r["round"] for r in rows] == [1, 2]
    assert [r["end_reason"] for r in rows] == ["death", "round_end"]
    assert rows[0]["video_start_sec"] == 30.0 and rows[0]["video_end_sec"] == 55.5


def test_unsorted_input_is_sorted():
    rows, _ = events_to_segments([ev("death", 55.0), ev("buy_end", 30.0)])
    assert len(rows) == 1


def test_unclosed_and_orphan_events_warn():
    rows, warns = events_to_segments([
        ev("death", 10.0),               # 開始前の死亡
        ev("buy_end", 30.0),             # 閉じられない
        ev("buy_end", 130.0), ev("death", 150.0),
        ev("buy_end", 230.0),            # 末尾で開いたまま
    ])
    assert [r["round"] for r in rows] == [2]
    assert len(warns) == 3


def test_csv_is_readable_by_analysis(tmp_path):
    rows, _ = events_to_segments([ev("buy_end", 30.0), ev("death", 55.0), ev("buy_end", 130.0, "defense"), ev("round_end", 180.0)])
    p = tmp_path / "s_segments.csv"
    write_segments_csv(p, rows)
    seg = gm.segments_from_video_time(pd.read_csv(p, encoding="utf-8-sig"), 0.02, ["alive"])
    assert len(seg) == 2
    assert seg["gaze_start_sec"].tolist() == [30.02, 130.02]
    assert seg["side"].tolist() == ["attack", "defense"]
