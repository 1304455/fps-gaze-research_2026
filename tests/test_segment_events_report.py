# -*- coding: utf-8 -*-
"""segment_events_report（注釈の状態確認。読むだけ）のテスト。"""
import json

import segment_annotator as sa
import segment_events_report as rep


def ev(kind, t, side="attack"):
    return {"kind": kind, "video_sec": t, "side": side}


def full_match(n_rounds=24, half=12, period=120.0, first="attack"):
    other = {"attack": "defense", "defense": "attack"}
    out = []
    for i in range(n_rounds):
        side = first if i < half else other[first]
        t0 = 100.0 + i * period
        out += [ev("buy_end", t0, side), ev("death", t0 + 40.0, side)]
    return out


def test_clean_match_has_no_issues():
    events = full_match()
    rounds = rep.build_rounds(events)
    assert len(rounds) == 24 and all(r["problem"] == "" for r in rounds)
    assert rep.find_issues(events, rounds, expected_rounds=24) == []


def test_rounds_match_events_to_segments():
    """表のラウンドと終了は、区間 CSV に出る行と同じ規則で決まる。"""
    events = full_match()
    del events[5]  # ラウンド3の death を消す
    rows, _ = sa.events_to_segments(events)
    rounds = [r for r in rep.build_rounds(events) if r["problem"] == ""]
    assert [(r["round"], r["start"], r["end"]) for r in rounds] == \
        [(r["round"], r["video_start_sec"], r["video_end_sec"]) for r in rows]


def test_missing_end_and_missing_start():
    events = full_match()
    del events[5]  # ラウンド3の終了なし
    del events[7]  # 元のラウンド5の b なし → 終了だけ残る
    rounds = rep.build_rounds(events)
    issues = rep.find_issues(events, rounds)
    assert any("ラウンド3" in s and "終了（k/e）がありません" in s for s in issues)
    assert any("購入フェーズ終了（b）がありません" in s for s in issues)


def test_forgotten_side_switch():
    events = full_match()
    for e in events:
        e["side"] = "attack"
    issues = rep.find_issues(events, rep.build_rounds(events))
    assert any("一度も切り替わっていません" in s for s in issues)


def test_missing_whole_round_shifts_switch_and_flags_gap():
    events = full_match()
    del events[4:6]  # ラウンド3を丸ごと付け忘れ
    issues = rep.find_issues(events, rep.build_rounds(events), expected_rounds=24)
    assert any("丸ごと抜けている可能性" in s for s in issues)
    assert any("ラウンド12" in s and "最初の攻守の切り替え" in s for s in issues)  # 番号が1つずれる
    assert any("b の数 23" in s for s in issues)


def test_double_press():
    events = full_match()
    events.append(ev("death", 140.3))
    issues = rep.find_issues(events, rep.build_rounds(events))
    assert any("二重押し" in s for s in issues)


def test_report_shows_resume_side_by_press_order_and_does_not_write(tmp_path):
    """再開時の攻守は「最後に押した」イベントのもの（時刻順の最後ではない）。ファイルは書き換えない。"""
    events = full_match()
    events.append(ev("buy_end", 50.0, "defense"))  # 最後に、時刻の早い位置へ b を足した
    ev_path = tmp_path / "s_segments_events.json"
    sa.save_events(ev_path, events, tmp_path / "v.mp4", 60.0)
    before = ev_path.read_bytes()
    rep.main(["--session-base", "s", "--out-dir", str(tmp_path)])
    assert ev_path.read_bytes() == before
    assert not (tmp_path / "s_segments.csv").exists()
    text = rep.format_report(events, tmp_path / "s_segments.csv")
    assert "next side：defense" in text
    assert "区間 CSV：まだありません" in text


def test_csv_status(tmp_path):
    events = full_match()
    csv_path, ev_path = tmp_path / "s_segments.csv", tmp_path / "s_segments_events.json"
    sa.save_segments(csv_path, ev_path, events, tmp_path / "v.mp4", 60.0, backup_pending=False)
    assert "イベントと一致しています" in rep.format_report(events, csv_path)
    events2 = events[:-1]
    assert "イベントから作った内容と違います" in rep.format_report(events2, csv_path)
    json.loads(ev_path.read_text(encoding="utf-8"))
