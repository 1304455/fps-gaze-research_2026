# -*- coding: utf-8 -*-
"""
分析区間の注釈（*_segments_events.json）の今の状態を表示する確認ツール（読むだけ。ファイルは一切書き換えない）。

segment_annotator.py で付けたイベントを動画の時刻順に並べ、ラウンドごとの表と、付け忘れ・攻守の切り替え忘れの
疑いを出す。注釈をやり直す前と後に使う。

表示するもの
------------
- ラウンドの表：区間の開始（b）・終了（k/e）の動画時刻（秒と分:秒）、終了の理由、攻守、生存時間、前の b からの間隔
  蘇生後の区間（r で開始。2026-11-10）は番号の後ろに r を付けて同じラウンドの行として出す。
  ラウンドの数・b の間隔・攻守の判定には数えない
- 問題の一覧
  * 終了が無い区間（次の b まで k/e が無い、または最後の区間が閉じていない）→ 区間 CSV に出力されない
  * 開始（b）が無い終了（k/e）→ 無視されている
  * 前の b からの間隔が中央値の --gap-factor 倍（既定 1.8）を超える → ラウンドが丸ごと抜けている可能性（目安）
  * ほぼ同時刻（--dup-sec 秒未満、既定 1.0）の同じ種類のイベント → 二重押しの可能性
  * 攻守：どのラウンドで切り替わったか。--half-rounds（既定 12）+1 ラウンド目で切り替わっていない場合に警告
    （b が抜けているとラウンド番号がずれるので、上の「抜けている可能性」と合わせて見る）
  * --expected-rounds（試合のスコアの合計）を渡すと、b の数と比べる
- 再開したときの攻守：注釈ツールを起動し直したときの next side（最後に押したイベントの攻守）
- 区間 CSV の状態：イベントから作った内容と一致しているか

使い方（PowerShell）
--------------------
python .\\src\\visualizer\\segment_events_report.py --session-base $base
python .\\src\\visualizer\\segment_events_report.py --session-base $base --expected-rounds 22
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from segment_annotator import (  # noqa: E402
    DEFAULT_ANNOTATION_DIR, EVENT_KINDS, events_to_segments, existing_csv_differs, load_events,
)

REPORT_VERSION = "1.1.0"
END_KINDS = ("death", "round_end")


def fmt_time(sec: "float | None") -> str:
    """動画時刻を「秒 (分:秒.1桁)」で表す。録画を別のプレーヤーで探すとき用。"""
    if sec is None:
        return "-"
    m, s = divmod(float(sec), 60.0)
    return f"{sec:9.2f} ({int(m)}:{s:04.1f})"


def build_rounds(events: list[dict]) -> list[dict]:
    """
    イベントを時刻順に並べ、ラウンド（b から次の k/e まで）の行にまとめる。events_to_segments と同じ規則で読む。
    各行: {"round", "start", "end", "end_reason", "side", "problem", "revive"}
      round は b の順番（b の無い終了は None）。revive は蘇生後の区間（r で開始）なら True。
      problem は "no_end" / "no_start" / "end_before_start" / "revive_without_death" / ""。
    """
    rounds: list[dict] = []
    open_row = None
    n = 0
    for ev in sorted(events, key=lambda e: e["video_sec"]):
        kind, t = ev["kind"], float(ev["video_sec"])
        if kind == "buy_end":
            if open_row is not None:
                open_row["problem"] = "no_end"
                rounds.append(open_row)
            n += 1
            open_row = {"round": n, "start": t, "end": None, "end_reason": "", "side": ev.get("side", ""),
                        "problem": "", "revive": False}
        elif kind == "revive":
            # events_to_segments と同じ規則：直前の行が死亡で閉じた区間のときだけ、同じラウンドの区間を開く
            prev = rounds[-1] if rounds else None
            ok = (open_row is None and prev is not None and prev["problem"] == "" and prev["end_reason"] == "death"
                  and prev["end"] is not None and t > prev["end"])
            if not ok:
                rounds.append({"round": None, "start": t, "end": None, "end_reason": "", "side": "",
                               "problem": "revive_without_death", "revive": True})
                continue
            open_row = {"round": prev["round"], "start": t, "end": None, "end_reason": "", "side": prev["side"],
                        "problem": "", "revive": True}
        elif kind in END_KINDS:
            if open_row is None:
                rounds.append({"round": None, "start": None, "end": t, "end_reason": kind, "side": "",
                               "problem": "no_start", "revive": False})
            elif t <= open_row["start"]:
                rounds.append({"round": None, "start": None, "end": t, "end_reason": kind, "side": "",
                               "problem": "end_before_start", "revive": False})
            else:
                open_row.update(end=t, end_reason=kind)
                rounds.append(open_row)
                open_row = None
        else:
            raise ValueError(f"未知のイベント種別です: {kind}")
    if open_row is not None:
        open_row["problem"] = "no_end"
        rounds.append(open_row)
    return rounds


def find_issues(events: list[dict], rounds: list[dict], half_rounds: int = 12, gap_factor: float = 1.8,
                dup_sec: float = 1.0, expected_rounds: "int | None" = None) -> list[str]:
    """付け忘れ・攻守の切り替え忘れの疑いを文章で返す（無ければ空リスト）。"""
    issues: list[str] = []

    for r in rounds:
        if r["problem"] == "no_end":
            issues.append(f"ラウンド{r['round']}（開始 {r['start']:.2f}s）：終了（k/e）がありません → この区間は出力されません")
        elif r["problem"] == "no_start":
            issues.append(f"{EVENT_KINDS[r['end_reason']]}（{r['end']:.2f}s）：前に購入フェーズ終了（b）がありません → 無視されています")
        elif r["problem"] == "end_before_start":
            issues.append(f"{EVENT_KINDS[r['end_reason']]}（{r['end']:.2f}s）：開始より前か同時刻です → 無視されています")
        elif r["problem"] == "revive_without_death":
            issues.append(f"蘇生後の再開（{r['start']:.2f}s）：直前に死亡（k）で閉じた区間がありません → 無視されています")

    # 二重押し
    by_time = sorted(events, key=lambda e: e["video_sec"])
    for a, b in zip(by_time, by_time[1:]):
        if a["kind"] == b["kind"] and float(b["video_sec"]) - float(a["video_sec"]) < dup_sec:
            issues.append(f"{EVENT_KINDS[a['kind']]}が {a['video_sec']:.2f}s と {b['video_sec']:.2f}s に続けてあります（二重押しの可能性）")

    # ラウンドが丸ごと抜けている可能性（b と b の間隔）
    # ラウンドの数・b の間隔・攻守は b で始まる区間だけで見る（蘇生後の区間は同じラウンドの続き）
    starts = [r for r in rounds if r["start"] is not None and not r.get("revive")]
    gaps = [(prev, cur, cur["start"] - prev["start"]) for prev, cur in zip(starts, starts[1:])]
    if len(gaps) >= 3:
        med = statistics.median(g for _, _, g in gaps)
        for prev, cur, g in gaps:
            if g > gap_factor * med:
                issues.append(
                    f"ラウンド{prev['round']}→{cur['round']}：b の間隔 {g:.0f}s が中央値 {med:.0f}s の {g / med:.1f} 倍です"
                    f"（{prev['start']:.2f}s〜{cur['start']:.2f}s にラウンドが丸ごと抜けている可能性。目安）")

    # 攻守
    sided = [r for r in starts if r["side"]]
    changes = [cur for prev, cur in zip(sided, sided[1:]) if cur["side"] != prev["side"]]
    if len(sided) > half_rounds and not changes:
        issues.append(f"攻守が一度も切り替わっていません（全 {len(sided)} ラウンドが {sided[0]['side']}）。"
                      f"{half_rounds + 1} ラウンド目の前の t を忘れた可能性")
    elif changes and changes[0]["round"] != half_rounds + 1:
        issues.append(f"最初の攻守の切り替えがラウンド{changes[0]['round']}です（通常は {half_rounds + 1}）。"
                      "b の付け忘れでラウンド番号がずれているか、t を押す位置がずれた可能性")

    if expected_rounds is not None:
        n_b = len(starts)
        if n_b != expected_rounds:
            issues.append(f"b の数 {n_b} が、試合のラウンド数 {expected_rounds} と合いません")
    return issues


def side_changes(rounds: list[dict]) -> list[str]:
    sided = [r for r in rounds if r["start"] is not None and r["side"] and not r.get("revive")]
    if not sided:
        return []
    out = [f"ラウンド{sided[0]['round']}〜：{sided[0]['side']}"]
    for prev, cur in zip(sided, sided[1:]):
        if cur["side"] != prev["side"]:
            out.append(f"ラウンド{cur['round']}〜：{cur['side']}（{cur['start']:.2f}s）")
    return out


def format_report(events: list[dict], segments_csv: "Path | None" = None, half_rounds: int = 12,
                  gap_factor: float = 1.8, dup_sec: float = 1.0, expected_rounds: "int | None" = None) -> str:
    rounds = build_rounds(events)
    rows, _ = events_to_segments(events)
    lines = [f"イベント {len(events)} 件 → 出力される区間 {len(rows)} 件（segment_events_report {REPORT_VERSION}）", ""]

    header = f"{'No':>3}  {'開始 b [s] (分:秒)':<20}  {'終了 k/e [s] (分:秒)':<20}  {'理由':<9}  {'攻守':<7}  {'生存[s]':>7}  {'b間隔[s]':>8}  問題"
    lines.append(header)
    prev_start = None
    problem_label = {"no_end": "終了なし", "no_start": "開始なし", "end_before_start": "終了が開始以前",
                     "revive_without_death": "死亡の無い蘇生", "": ""}
    for r in rounds:
        alive = f"{r['end'] - r['start']:.1f}" if r["start"] is not None and r["end"] is not None else "-"
        is_b = r["start"] is not None and not r.get("revive")
        gap = f"{r['start'] - prev_start:.1f}" if is_b and prev_start is not None else "-"
        if is_b:
            prev_start = r["start"]
        no = (str(r["round"]) if r["round"] is not None else "-") + ("r" if r.get("revive") else "")
        lines.append(f"{no:>3}  {fmt_time(r['start']):<20}  {fmt_time(r['end']):<20}  {r['end_reason'] or '-':<9}  "
                     f"{r['side'] or '-':<7}  {alive:>7}  {gap:>8}  {problem_label[r['problem']]}")

    lines += ["", "攻守の並び：" + (" → ".join(side_changes(rounds)) or "（b がありません）")]
    if events:
        last = events[-1]  # 注釈ツールは「最後に押したイベント」の攻守で再開する（時刻順ではない）
        lines.append(f"注釈ツールを起動し直したときの next side：{last.get('side', '')}"
                     f"（最後に押したイベント：{EVENT_KINDS.get(last['kind'], last['kind'])} @ {last['video_sec']:.2f}s）")

    if segments_csv is not None:
        if not segments_csv.exists():
            lines.append(f"区間 CSV：まだありません（{segments_csv.name}）")
        elif existing_csv_differs(segments_csv, events):
            lines.append(f"区間 CSV：イベントから作った内容と違います（{segments_csv.name}）。注釈ツールで保存し直すと作り直されます（元の CSV は退避されます）")
        else:
            lines.append(f"区間 CSV：イベントと一致しています（{segments_csv.name}）")

    issues = find_issues(events, rounds, half_rounds, gap_factor, dup_sec, expected_rounds)
    lines += ["", f"問題の疑い：{len(issues)} 件"]
    lines += [f"  ⚠ {s}" for s in issues] or ["  なし"]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="分析区間の注釈（*_segments_events.json）の今の状態を表示します（読むだけ）。")
    p.add_argument("--session-base", required=True, help="注釈ツールに渡したものと同じ session_base")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_ANNOTATION_DIR, help="注釈の場所（既定: data/annotations）")
    p.add_argument("--expected-rounds", type=int, default=None, help="試合のラウンド数（スコアの合計）。b の数と比べる")
    p.add_argument("--half-rounds", type=int, default=12, help="前半のラウンド数（既定 12）")
    p.add_argument("--gap-factor", type=float, default=1.8, help="b の間隔が中央値の何倍を超えたら抜けを疑うか（既定 1.8）")
    p.add_argument("--dup-sec", type=float, default=1.0, help="同じ種類のイベントがこの秒数未満で続いたら二重押しを疑う（既定 1.0）")
    args = p.parse_args(argv)

    events_json = args.out_dir / f"{args.session_base}_segments_events.json"
    segments_csv = args.out_dir / f"{args.session_base}_segments.csv"
    if not events_json.exists():
        raise SystemExit(f"エラー: イベントの記録がありません: {events_json}")
    events = load_events(events_json)
    print(f"[report] {events_json}")
    print(format_report(events, segments_csv, args.half_rounds, args.gap_factor, args.dup_sec, args.expected_rounds))


if __name__ == "__main__":
    main()
