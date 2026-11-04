# -*- coding: utf-8 -*-
"""
検証点の大きなずれが「次に提示された点」の方向を向いているか（先読み）を調べる（オフライン専用。研究データは書き換えない）。

背景（docs/LAB_CHECKLIST.md 2c の 2026-10-29）：calibrate_validate は外円の縮小が終わってから 0.8〜1.3 秒後を集計し、
点の順番は毎回同じ。仕組みを知らない参加者は、縮み終わった時点で次の点へ目を動かしうる。

提示順
  calibrate_validate 1.4.0 以降は各測定の presentation_index を使う。
  1.3.0 以前は実装から組み立てる（1周目は validation_points の順、測り直しは retried の点を index 順）。
  どちらの場合も、採用された測定（selected_attempt）の直後に提示された点と比べる。
  1.5.0 以降は点の順番がランダムなので、参加者は次の点の位置を予測できない。一致が偶然の水準（約 25%）に
  とどまることの確認として使う（目が動いた点そのものの数は、順番によらず「見続けていたか」の指標になる）。

判定（既定の閾値は 10/29 の PL01 の結果を見てから決めた値。以後は変えずに使う）
  対象：両眼平均のずれ（左右眼の bias_*_norm の平均を度に換算）が --min-error-deg 超
  一致：ずれの向きと次の点の方向の cos が --cos 超（向きがでたらめなら約 25%）
  目が動いた点：precision（RMS sample-to-sample）が --min-prec-deg 超

使い方（PowerShell）
--------------------
python .\\src\\analysis\\calib_anticipation.py ".\\data\\raw\\calib_PL01_20261029_*.json"
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path

_AOI_DIR = Path(__file__).resolve().parent.parent / "aoi_detector"
if str(_AOI_DIR) not in sys.path:
    sys.path.insert(0, str(_AOI_DIR))

from aoi_geometry import eye_mean  # noqa: E402

DEFAULT_MIN_ERROR_DEG = 1.5
DEFAULT_MIN_PREC_DEG = 0.8
DEFAULT_COS = 0.7


def presentation_sequence(points: list[dict]) -> tuple[list[int], str]:
    """
    採用された測定を提示順に並べた点の index と、順序の出所（recorded / inferred）。
    採用されなかった測定（測り直しで捨てた側）も提示はされているので、順序の中に含めて「次の点」を決める。
    戻り値の列は (提示順の各測定の点 index) で、採用の判定は next_targets で行う。
    """
    recorded = all(p.get("presentation_index") is not None for p in points)
    if recorded:
        events = []
        for i, p in enumerate(points):
            if p.get("retried"):
                for key in ("first_attempt", "retry_attempt"):
                    idx = (p.get(key) or {}).get("presentation_index")
                    if idx is not None:
                        events.append((idx, i))
            else:
                events.append((p["presentation_index"], i))
        return [i for _, i in sorted(events)], "recorded"
    retried = [i for i, p in enumerate(points) if p.get("retried")]
    return list(range(len(points))) + retried, "inferred"


def next_targets(points: list[dict]) -> tuple[dict[int, "int | None"], str]:
    """各点について、採用された測定の直後に提示された点の index（最後なら None）と、順序の出所。"""
    seq, source = presentation_sequence(points)
    seen: dict[int, int] = {}
    nxt: dict[int, "int | None"] = {}
    for k, i in enumerate(seq):
        seen[i] = seen.get(i, 0) + 1
        used_nth = 2 if points[i].get("selected_attempt") == "retry" else 1
        if seen[i] == used_nth:
            nxt[i] = seq[k + 1] if k + 1 < len(seq) else None
    return nxt, source


def binom_tail(n: int, k: int, p: float) -> float:
    """二項分布で k 以上になる確率。"""
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def analyze(calib: dict, min_err: float = DEFAULT_MIN_ERROR_DEG, min_prec: float = DEFAULT_MIN_PREC_DEG) -> dict:
    """calib JSON 1つについて、ずれが min_err を超えた点ごとに次の点の方向との cos を出す。"""
    da = calib.get("display_area_ucs_mm") or {}
    w, h = da.get("width_mm", 596.7), da.get("height_mm", 335.6)
    z = ((calib.get("head_position") or {}).get("recorded") or {}).get("z_mid_mm") or 650.0

    def deg(v: float, size: float) -> float:
        return math.degrees(math.atan(v * size / z))

    pts = calib.get("validation_points", [])
    nxt, source = next_targets(pts)
    rows = []
    for i, p in enumerate(pts):
        bx, by = eye_mean(p, "bias_x_norm"), eye_mean(p, "bias_y_norm")
        j = nxt.get(i)
        if j is None or math.isnan(bx) or math.isnan(by):
            continue
        ex, ey = deg(bx, w), deg(by, h)
        err = math.hypot(ex, ey)
        if err <= min_err:
            continue
        dx = deg(pts[j]["target_x"] - p["target_x"], w)
        dy = deg(pts[j]["target_y"] - p["target_y"], h)
        prec = p.get("precision_rms_s2s_deg")
        rows.append({"label": p["label"], "error_x_deg": ex, "error_y_deg": ey, "error_deg": err,
                     "precision_deg": prec, "moving": prec is not None and prec > min_prec,
                     "next_label": pts[j]["label"], "next_dx_deg": dx, "next_dy_deg": dy,
                     "cos": (ex * dx + ey * dy) / (err * math.hypot(dx, dy))})
    return {"subject_id": calib.get("subject_id"), "order_source": source, "n_points": len(pts), "rows": rows}


def expand_inputs(items: list[str]) -> list[Path]:
    out = []
    for s in items:
        hits = sorted(glob.glob(s))
        out.extend(Path(h) for h in hits) if hits else out.append(Path(s))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="検証点のずれが次に提示された点の方向を向いているか（先読み）を調べる")
    ap.add_argument("inputs", nargs="+", help="calib JSON のパスまたはワイルドカード")
    ap.add_argument("--min-error-deg", type=float, default=DEFAULT_MIN_ERROR_DEG, help="対象にする両眼平均のずれ [deg]")
    ap.add_argument("--min-prec-deg", type=float, default=DEFAULT_MIN_PREC_DEG,
                    help="検証区間の中で目が動いたとみなす precision [deg]")
    ap.add_argument("--cos", type=float, default=DEFAULT_COS, help="次の点の方向と一致とみなす cos")
    a = ap.parse_args(argv)

    chance = 2 * math.degrees(math.acos(a.cos)) / 360  # 向きがでたらめなときに一致する確率
    tot = {"all": [0, 0], "moving": [0, 0]}
    for path in expand_inputs(a.inputs):
        with open(path, "r", encoding="utf-8") as f:
            res = analyze(json.load(f), a.min_error_deg, a.min_prec_deg)
        rows = res["rows"]
        order = "記録" if res["order_source"] == "recorded" else "実装から推定（1.3.0 以前）"
        print(f"# {path.name}（subject {res['subject_id']}、提示順: {order}）")
        for r in rows:
            mark = "*" if r["cos"] > a.cos else " "
            prec = "  -  " if r["precision_deg"] is None else f"{r['precision_deg']:5.2f}"
            print(f"  {mark} {r['label']:<24} ずれ=({r['error_x_deg']:+6.2f},{r['error_y_deg']:+6.2f}) "
                  f"|{r['error_deg']:5.2f}| prec={prec}{' 動' if r['moving'] else '   '}  次={r['next_label']:<24} "
                  f"方向=({r['next_dx_deg']:+5.1f},{r['next_dy_deg']:+5.1f}) cos={r['cos']:+.2f}")
        moving = [r for r in rows if r["moving"]]
        n_al = sum(r["cos"] > a.cos for r in rows)
        m_al = sum(r["cos"] > a.cos for r in moving)
        print(f"  ずれ {a.min_error_deg}° 超 {len(rows)} 点（全 {res['n_points']} 点）中 一致 {n_al} 点 ／ "
              f"うち区間内で目が動いた {len(moving)} 点中 一致 {m_al} 点\n")
        tot["all"][0] += len(rows)
        tot["all"][1] += n_al
        tot["moving"][0] += len(moving)
        tot["moving"][1] += m_al
    print(f"=== 合計（向きがでたらめなら一致は約 {chance:.0%}）===")
    for key, name in (("all", f"ずれ {a.min_error_deg}° 超の点"), ("moving", f"うち precision {a.min_prec_deg}° 超")):
        n, k = tot[key]
        p = f"{binom_tail(n, k, chance):.3f}" if n else "—"
        print(f"  {name}: {k}/{n} 点が次の点の方向（偶然でこれ以上になる確率 {p}）")
    print("※ * は一致、『動』は検証区間の中で目が動いた点。ずれの y は正が下向き。閾値は結果を見てから変えない。")


if __name__ == "__main__":
    main()
