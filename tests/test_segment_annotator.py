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


# ---------------------------------------------------------------------------
# 蘇生後の区間（2026-11-10 決定：蘇生後も使う。同じラウンドの2つ目の区間にし、前の区間とはつなげない）
# ---------------------------------------------------------------------------

def test_revive_opens_second_segment_in_same_round():
    rows, warns = events_to_segments([
        ev("buy_end", 30.0), ev("death", 55.0), ev("revive", 60.0), ev("round_end", 90.0),
        ev("buy_end", 130.0, "defense"), ev("death", 150.0),
    ])
    assert warns == []
    assert [(r["round"], r["start_reason"], r["end_reason"]) for r in rows] == [
        (1, "buy_end", "death"), (1, "revive", "round_end"), (2, "buy_end", "death")]
    # 蘇生後の区間は、蘇生前の区間の攻守を引き継ぐ（t を押し間違えても変わらない）
    assert rows[1]["side"] == "attack"
    assert rows[1]["video_start_sec"] == 60.0 and rows[1]["video_end_sec"] == 90.0


def test_revive_twice_in_one_round():
    rows, warns = events_to_segments([
        ev("buy_end", 30.0), ev("death", 40.0), ev("revive", 45.0), ev("death", 50.0),
        ev("revive", 55.0), ev("death", 70.0),
    ])
    assert warns == []
    assert [(r["round"], r["start_reason"]) for r in rows] == [(1, "buy_end"), (1, "revive"), (1, "revive")]


def test_invalid_revive_is_ignored_with_warning():
    rows, warns = events_to_segments([
        ev("revive", 10.0),                                          # 区間がまだ無い
        ev("buy_end", 30.0), ev("revive", 40.0), ev("death", 50.0),  # 区間が開いたまま
        ev("buy_end", 130.0), ev("round_end", 150.0), ev("revive", 155.0),  # 死亡ではなくラウンド終了の後
        ev("buy_end", 230.0), ev("death", 250.0),
    ])
    assert [(r["round"], r["start_reason"]) for r in rows] == [(1, "buy_end"), (2, "buy_end"), (3, "buy_end")]
    assert len(warns) == 3


def test_revive_after_next_buy_end_is_not_attached_to_previous_round():
    rows, warns = events_to_segments([
        ev("buy_end", 30.0), ev("death", 40.0), ev("buy_end", 130.0), ev("revive", 135.0), ev("death", 150.0),
    ])
    # ラウンド2は開いたまま r が来るので無視され、ラウンド2の区間は 130〜150
    assert [(r["round"], r["video_start_sec"], r["start_reason"]) for r in rows] == [
        (1, 30.0, "buy_end"), (2, 130.0, "buy_end")]
    assert len(warns) == 1


def test_revive_csv_is_readable_by_analysis(tmp_path):
    rows, _ = events_to_segments([ev("buy_end", 30.0), ev("death", 55.0), ev("revive", 60.0), ev("death", 80.0)])
    p = tmp_path / "s_segments.csv"
    write_segments_csv(p, rows)
    seg = gm.segments_from_video_time(pd.read_csv(p, encoding="utf-8-sig"), 0.0, ["alive"])
    assert seg["start_reason"].tolist() == ["buy_end", "revive"]
    assert seg["round"].tolist() == [1, 1]
    assert seg["segment_id"].tolist() == [0, 1]


def test_legacy_csv_without_start_reason_is_not_flagged_as_edited(tmp_path):
    """11/10 より前のツールで保存した CSV（start_reason の列なし）は、蘇生が無ければ「手で編集した」と扱わない。"""
    import csv
    from segment_annotator import LEGACY_SEGMENT_CSV_COLUMNS, existing_csv_differs
    events = [ev("buy_end", 30.0), ev("death", 55.0), ev("buy_end", 130.0), ev("round_end", 180.0)]
    rows, _ = events_to_segments(events)
    p = tmp_path / "s_segments.csv"
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LEGACY_SEGMENT_CSV_COLUMNS)
        w.writeheader()
        w.writerows([{k: v for k, v in r.items() if k != "start_reason"} for r in rows])
    assert not existing_csv_differs(p, events)
    # 旧 CSV はそのまま解析で読め、start_reason は buy_end になる
    seg = gm.segments_from_video_time(pd.read_csv(p, encoding="utf-8-sig"), 0.0, ["alive"])
    assert seg["start_reason"].tolist() == ["buy_end", "buy_end"]
    # 蘇生を足すと作り直しが必要なので「違う」
    events_rev = [ev("buy_end", 30.0), ev("death", 55.0), ev("revive", 60.0), ev("death", 70.0),
                  ev("buy_end", 130.0), ev("round_end", 180.0)]
    assert existing_csv_differs(p, events_rev)


def test_unknown_start_reason_is_rejected():
    import pytest
    raw = pd.DataFrame([{"round": 1, "segment_type": "alive", "video_start_sec": 0.0, "video_end_sec": 1.0,
                         "end_reason": "death", "side": "attack", "notes": "", "start_reason": "revival"}])
    with pytest.raises(ValueError, match="start_reason"):
        gm.segments_from_video_time(raw, 0.0, ["alive"])
