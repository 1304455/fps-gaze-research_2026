# -*- coding: utf-8 -*-
"""
分析区間（セグメント）の注釈ツール（オフライン専用。試合中には起動しない）。

OBS録画を再生しながら、キー操作で「購入フェーズ終了」「死亡」「ラウンド終了」を記録し、
data/annotations/<session_base>_segments.csv を書き出す。aoi_analysis-ver3.py が読む。

- 時刻は動画時刻（frame_idx / fps。gaze_visualizer_v3.py と同じ定義）で保存する。
  視線時刻への変換は解析時に sync の offset_sec で行う
- 区間 = 「購入フェーズ終了」→「死亡」または「ラウンド終了」（生存中、segment_type=alive）
- 押したイベントは都度 <session_base>_segments_events.json に保存し、途中で閉じても再開できる

キー操作
--------
  SPACE        再生 / 一時停止
  d / a        1フレーム進む / 戻る
  l / j        5秒進む / 戻る         L / J  30秒進む / 戻る
  ] / [        再生速度 ×2 / ×1/2（0.25〜4倍）
  b            購入フェーズ終了（区間開始。ラウンド番号は自動で +1）
  k            死亡（区間終了 end_reason=death）
  e            ラウンド終了（区間終了 end_reason=round_end）
  t            次に開始する区間の攻守を切り替え（attack / defense）
  u            直前のイベントを取り消し
  s            CSV を保存
  q / ESC      保存して終了

使い方（PowerShell）
--------------------
python .\\src\\visualizer\\segment_annotator.py "C:\\Users\\kawalab\\Videos\\2026-10-20 14-04-19.mp4" `
    --session-base gaze_P01_1_default_20261020_140358 --start-side attack
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ANNOTATION_DIR = _PROJECT_ROOT / "data" / "annotations"

SEGMENT_CSV_COLUMNS = ["round", "segment_type", "video_start_sec", "video_end_sec", "end_reason", "side", "notes"]
EVENT_KINDS = {"buy_end": "購入フェーズ終了", "death": "死亡", "round_end": "ラウンド終了"}
SIDES = ("attack", "defense")


# ---------------------------------------------------------------------------
# イベント列 → 区間（UI に依存しない部分。テスト対象）
# ---------------------------------------------------------------------------

def events_to_segments(events: list[dict]) -> tuple[list[dict], list[str]]:
    """
    イベント列（時刻順でなくてもよい）から alive 区間の行を作る。
    events: {"kind": "buy_end"|"death"|"round_end", "video_sec": float, "side": str} のリスト
    戻り値: (区間行のリスト, 警告メッセージのリスト)
    """
    rows, warnings = [], []
    open_seg = None
    round_no = 0
    for ev in sorted(events, key=lambda e: e["video_sec"]):
        kind, t = ev["kind"], float(ev["video_sec"])
        if kind == "buy_end":
            round_no += 1
            if open_seg is not None:
                warnings.append(
                    f"ラウンド{open_seg['round']}の区間が閉じられないまま次の購入フェーズ終了（{t:.2f}s）"
                    "が来ました。この区間は出力しません。"
                )
            open_seg = {"round": round_no, "video_start_sec": t, "side": ev.get("side", "")}
        elif kind in ("death", "round_end"):
            if open_seg is None:
                warnings.append(f"{EVENT_KINDS[kind]}（{t:.2f}s）の前に購入フェーズ終了がありません。無視します。")
                continue
            if t <= open_seg["video_start_sec"]:
                warnings.append(f"ラウンド{open_seg['round']}：終了が開始以前です。無視します。")
                continue
            rows.append({
                "round": open_seg["round"],
                "segment_type": "alive",
                "video_start_sec": round(open_seg["video_start_sec"], 4),
                "video_end_sec": round(t, 4),
                "end_reason": kind,
                "side": open_seg["side"],
                "notes": "",
            })
            open_seg = None
        else:
            raise ValueError(f"未知のイベント種別です: {kind}")
    if open_seg is not None:
        warnings.append(f"ラウンド{open_seg['round']}の区間が閉じられていません（出力しません）。")
    return rows, warnings


def write_segments_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SEGMENT_CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("events", [])


def save_events(path: Path, events: list[dict], video_path: Path, fps: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"video": str(video_path), "fps": fps, "time_definition": "video_sec = frame_idx / fps",
                   "events": events}, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# UI（OpenCV）
# ---------------------------------------------------------------------------

def run_ui(video_path: Path, segments_csv: Path, events_json: Path, start_side: str, display_width: int) -> None:
    import cv2  # UI を使うときだけ必要

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"エラー: 動画を開けません: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    events = load_events(events_json)
    if events:
        print(f"[annotator] 既存のイベント {len(events)} 件を読み込みました: {events_json}")
    side = events[-1]["side"] if events else start_side

    frame_idx = 0
    playing = False
    speed = 1.0
    frame = None
    need_seek = True
    message = ""
    win = "segment_annotator"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    def save():
        rows, warnings = events_to_segments(events)
        write_segments_csv(segments_csv, rows)
        save_events(events_json, events, video_path, fps)
        for w_ in warnings:
            print(f"⚠ {w_}")
        return f"保存しました（{len(rows)}区間）: {segments_csv.name}"

    while True:
        if need_seek:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame_new = cap.read()
            if ok:
                frame = frame_new
            need_seek = False
        elif playing:
            step = max(1, int(round(speed))) if speed >= 1 else 1
            for _ in range(step):
                ok, frame_new = cap.read()
                if not ok:
                    playing = False
                    break
                frame = frame_new
                frame_idx += 1
        if frame is None:
            raise SystemExit("エラー: フレームを読み込めません。")

        t = frame_idx / fps
        rows, _ = events_to_segments(events)
        open_now = bool(events) and sorted(events, key=lambda e: e["video_sec"])[-1]["kind"] == "buy_end"
        h, w = frame.shape[:2]
        scale = display_width / w
        view = cv2.resize(frame, (display_width, int(h * scale)))
        lines = [
            f"t={t:9.3f}s  frame={frame_idx}/{n_frames}  fps={fps:.2f}  speed=x{speed:g}  {'PLAY' if playing else 'PAUSE'}",
            f"next side={side}  segments={len(rows)}  state={'ALIVE(open)' if open_now else '-'}",
            "SPACE play  a/d 1f  j/l 5s  J/L 30s  [/] speed  b buy_end  k death  e round_end  t side  u undo  s save  q quit",
        ]
        if events:
            last = sorted(events, key=lambda e: e["video_sec"])[-3:]
            lines.append("last: " + " | ".join(f"{e['kind']}@{e['video_sec']:.2f}" for e in last))
        if message:
            lines.append(message)
        for i, text in enumerate(lines):
            y = 24 + i * 24
            cv2.putText(view, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(view, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(win, view)

        delay = max(1, int(1000 / (fps * min(speed, 1.0)))) if playing else 30
        key = cv2.waitKey(delay) & 0xFF
        if key == 255:
            continue
        ch = chr(key)
        message = ""
        if key in (27,) or ch == "q":
            message = save()
            print(f"[annotator] {message}")
            break
        if ch == " ":
            playing = not playing
        elif ch in "adjlJL":
            delta = {"a": -1, "d": 1, "j": -5 * fps, "l": 5 * fps, "J": -30 * fps, "L": 30 * fps}[ch]
            frame_idx = int(min(max(0, frame_idx + delta), max(0, n_frames - 1)))
            playing = False if ch in "ad" else playing
            need_seek = True
        elif ch == "]":
            speed = min(4.0, speed * 2)
        elif ch == "[":
            speed = max(0.25, speed / 2)
        elif ch in ("b", "k", "e"):
            kind = {"b": "buy_end", "k": "death", "e": "round_end"}[ch]
            events.append({"kind": kind, "video_sec": round(t, 4), "frame_idx": frame_idx, "side": side})
            save_events(events_json, events, video_path, fps)
            message = f"記録: {EVENT_KINDS[kind]} @ {t:.3f}s"
        elif ch == "t":
            side = SIDES[(SIDES.index(side) + 1) % 2] if side in SIDES else SIDES[0]
            message = f"次の区間の攻守: {side}"
        elif ch == "u":
            if events:
                removed = events.pop()
                save_events(events_json, events, video_path, fps)
                message = f"取り消し: {removed['kind']} @ {removed['video_sec']:.3f}s"
        elif ch == "s":
            message = save()

    cap.release()
    cv2.destroyAllWindows()


def main(argv=None):
    p = argparse.ArgumentParser(description="OBS録画から分析区間（購入フェーズ終了→死亡/ラウンド終了）を注釈します。")
    p.add_argument("video_path", type=Path, help="OBS録画（mp4/mkv）")
    p.add_argument("--session-base", required=True, help="視線CSVのファイル名から拡張子を除いたもの（出力ファイル名に使う）")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_ANNOTATION_DIR, help="出力先（既定: data/annotations）")
    p.add_argument("--start-side", choices=SIDES, default="attack", help="最初のラウンドの攻守")
    p.add_argument("--display-width", type=int, default=1280, help="表示幅(px)")
    args = p.parse_args(argv)

    segments_csv = args.out_dir / f"{args.session_base}_segments.csv"
    events_json = args.out_dir / f"{args.session_base}_segments_events.json"
    run_ui(args.video_path, segments_csv, events_json, args.start_side, args.display_width)


if __name__ == "__main__":
    main()
