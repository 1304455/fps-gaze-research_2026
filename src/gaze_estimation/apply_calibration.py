# -*- coding: utf-8 -*-
"""
保存した較正データをアイトラッカーに入れ直す（2026-10-30 決定：較正が3回とも不合格だった参加者）。

calibrate_validate.py（1.8.0 以降）は較正に成功した回の較正データを calib_<subject>_<日時>.calibdata に保存する。
アイトラッカーは最後に行った較正しか保持しないため、3回とも不合格だったときに「一番良い回」の較正で試合をするには、
その回のデータを入れ直す必要がある。

選び方（CLAUDE.md §2、案1。実験前に決めた規則）
  候補：SDK の較正が成功し（calibration.succeeded）、較正データのファイルがある calib JSON
  1. 合格した回を優先
  2. HUD の点の不合格（summary.hud_points_failed）の数が少ない回
  3. 全点平均 accuracy（summary.mean_accuracy_deg）が小さい回
  4. それでも同じなら新しい回（試合に近い時刻の較正）

入れ直した後に装置から較正データを取り出し、SHA-256 がファイルと一致することを確かめる。
結果は data/raw/calibapply_<subject>_<日時>.json に残す（どの回を、なぜ選んだか）。
試合後の検証（<ID>-post）の active_calibration.sha256 が、ここで入れ直した SHA-256 と一致すれば、試合中もその較正だった。

使い方（PowerShell。試合の直前、頭と Spark を動かさずに）
--------------------------------------------------------------
# 3回の calib JSON から選んで入れ直す（先に --dry-run で選ばれる回を確認できる）
python .\\src\\gaze_estimation\\apply_calibration.py (Get-ChildItem .\\data\\raw\\calib_P01_*.json).FullName --dry-run
python .\\src\\gaze_estimation\\apply_calibration.py (Get-ChildItem .\\data\\raw\\calib_P01_*.json).FullName
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_VERSION = "1.0.0"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "raw"
SELECTION_RULE = ("合格した回を優先 → HUD の点の不合格の数が少ない → 全点平均 accuracy が小さい → 新しい回"
                  "（SDK の較正が成功し、較正データのファイルがある回だけが候補）")


# ---------------------------------------------------------------------------
# 選び方と照合（SDK に依存しない部分。テスト対象）
# ---------------------------------------------------------------------------

def load_candidate(path: Path) -> dict:
    """calib JSON 1つを候補の情報にまとめる。候補にならない理由があれば reason_excluded に入れる。"""
    with open(path, "r", encoding="utf-8") as f:
        calib = json.load(f)
    summ = calib.get("summary") or {}
    active = calib.get("active_calibration") or {}
    calibration = calib.get("calibration") or {}
    data_file = active.get("file")
    reason = None
    if calibration.get("succeeded") is not True:
        reason = "SDK の較正が成功していない（--validate-only または較正の失敗）"
    elif not data_file:
        reason = "較正データのファイルが無い（calibrate_validate 1.8.0 より前、または取り出しに失敗）"
    elif not (path.parent / data_file).exists():
        reason = f"較正データのファイルが見つからない: {data_file}"
    mean = summ.get("mean_accuracy_deg")
    return {
        "calib_json": path.name,
        "path": path,
        "subject_id": calib.get("subject_id"),
        "created_at_utc": calib.get("created_at_utc") or "",
        "passed": bool(summ.get("passed")),
        "n_hud_failed": len(summ.get("hud_points_failed") or []),
        "hud_points_failed": summ.get("hud_points_failed") or [],
        "mean_accuracy_deg": mean,
        "data_file": data_file,
        "sha256": active.get("sha256"),
        "serial_number": (calib.get("eyetracker") or {}).get("serial_number"),
        "display_area_ucs_mm": calib.get("display_area_ucs_mm"),
        "reason_excluded": reason,
    }


def rank_key(c: dict) -> tuple:
    """小さいほど良い（合格 → HUD の不合格の数 → 全点平均）。同順位の「新しい回を優先」は select_best で扱う。"""
    mean = c["mean_accuracy_deg"] if c["mean_accuracy_deg"] is not None else math.inf
    return (0 if c["passed"] else 1, c["n_hud_failed"], mean)


def select_best(candidates: list[dict]) -> "dict | None":
    """候補から規則（SELECTION_RULE）で1つ選ぶ。候補が無ければ None。"""
    eligible = [c for c in candidates if c["reason_excluded"] is None]
    if not eligible:
        return None
    # 同順位なら新しい回：まず新しい順に並べてから安定ソートする
    eligible = sorted(eligible, key=lambda c: c["created_at_utc"], reverse=True)
    return sorted(eligible, key=rank_key)[0]


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def same_display_area(a: "dict | None", b: "dict | None", tol_mm: float = 0.5) -> bool:
    """表示領域（ETM の画面設定）が同じか。どちらかが無ければ判断しない（True）。"""
    if not a or not b:
        return True
    for key in ("top_left", "top_right", "bottom_left"):
        if any(abs(float(x) - float(y)) > tol_mm for x, y in zip(a[key], b[key])):
            return False
    return True


def apply_and_verify(eyetracker, data: bytes) -> dict:
    """較正データを入れ直し、取り出したデータの SHA-256 が一致するか確かめる。"""
    eyetracker.apply_calibration_data(data)
    back = eyetracker.retrieve_calibration_data()
    back = bytes(back) if back else b""
    return {"applied_sha256": sha256_of(data), "retrieved_sha256": sha256_of(back) if back else None,
            "verified": bool(back) and sha256_of(back) == sha256_of(data)}


# ---------------------------------------------------------------------------
# 研究室PCでのみ動く部分
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="保存した較正データを選んでアイトラッカーに入れ直す（試合の直前）")
    p.add_argument("calib_json", nargs="+", type=Path, help="候補の calib JSON（同じ参加者の較正＋検証の回）")
    p.add_argument("--dry-run", action="store_true", help="選ばれる回を表示するだけで、入れ直さない")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = p.parse_args(argv)

    candidates = [load_candidate(path) for path in args.calib_json]
    subjects = {c["subject_id"] for c in candidates}
    if len(subjects) > 1:
        sys.exit(f"エラー: 参加者 ID の違う calib JSON が混ざっています: {sorted(map(str, subjects))}")
    print("=== 候補 ===")
    for c in sorted(candidates, key=lambda c: c["created_at_utc"]):
        mean = "—" if c["mean_accuracy_deg"] is None else f"{c['mean_accuracy_deg']:.2f}°"
        state = c["reason_excluded"] or ("合格" if c["passed"] else f"不合格（HUD {c['n_hud_failed']} 点: {c['hud_points_failed']}）")
        print(f"  {c['calib_json']:<40} 平均 {mean:>6}  {state}")
    best = select_best(candidates)
    if best is None:
        sys.exit("エラー: 入れ直せる回がありません（SDK の較正が成功し、較正データのファイルがある回が必要）。")
    print(f"\n選ばれた回: {best['calib_json']}\n規則: {SELECTION_RULE}")
    if args.dry_run:
        print("（--dry-run のため入れ直していません）")
        return

    data_path = best["path"].parent / best["data_file"]
    data = data_path.read_bytes()
    if best["sha256"] and sha256_of(data) != best["sha256"]:
        sys.exit(f"エラー: {best['data_file']} の SHA-256 が calib JSON の記録と一致しません。ファイルを確認してください。")

    try:
        import tobii_research as tr
    except ImportError:
        sys.exit("tobii-research が見つかりません。pip install tobii-research")
    found = tr.find_all_eyetrackers()
    if not found:
        sys.exit("Tobiiアイトラッカーが見つかりませんでした。接続を確認してください。")
    eyetracker = found[0]
    serial = getattr(eyetracker, "serial_number", None)
    if best["serial_number"] and serial != best["serial_number"]:
        sys.exit(f"エラー: アイトラッカーが違います（記録 {best['serial_number']} / 接続中 {serial}）。入れ直しません。")
    da = eyetracker.get_display_area()
    current_area = {"top_left": tuple(da.top_left), "top_right": tuple(da.top_right), "bottom_left": tuple(da.bottom_left)}
    area_same = same_display_area(best["display_area_ucs_mm"], current_area)
    if not area_same:
        print("⚠ 警告: ETM の画面設定（表示領域）が較正した時と違います。入れ直しても精度が保証されません。")

    result = apply_and_verify(eyetracker, data)
    print(f"入れ直し: {'成功（SHA-256 一致）' if result['verified'] else '⚠ 確認できませんでした（SHA-256 不一致）'}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.output_dir / f"calibapply_{best['subject_id']}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "script": {"name": Path(__file__).name, "version": SCRIPT_VERSION},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "subject_id": best["subject_id"],
        "selection_rule": SELECTION_RULE,
        "candidates": [{k: v for k, v in c.items() if k not in ("path", "display_area_ucs_mm")} for c in candidates],
        "selected_calib_json": best["calib_json"],
        "selected_data_file": best["data_file"],
        "eyetracker_serial_number": serial,
        "display_area_same_as_calibration": area_same,
        **result,
        "note": "試合後の検証（<ID>-post）の active_calibration.sha256 が applied_sha256 と一致すれば、試合中もこの較正だった",
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"保存しました: {out}")
    if not result["verified"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
