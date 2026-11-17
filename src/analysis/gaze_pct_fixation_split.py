# -*- coding: utf-8 -*-
"""
予備実験（PL01〜PL03）の AOI 別 Gaze % を、AOI への注視（aoi_fixation）と非注視の AOI 内サンプル（aoi_nonfixation）に
分けた積み上げ棒グラフにする。週報・卒論で「Gaze % と注視ベースの指標の大小が逆転する場合がある」ことを示す図。

  棒全体の高さ ＝ Gaze %（分母＝区間内の有効サンプル数。offscreen の扱いは主解析の params に従う）
  濃い部分     ＝ aoi_fixation のサンプル ÷ 同じ分母（「AOIへの注視」のサンプルの割合）
  薄い部分     ＝ aoi_nonfixation のサンプル ÷ 同じ分母
  棒の上の数字 ＝ Gaze %、と「注視に入った割合」＝ aoi_fixation ÷（aoi_fixation ＋ aoi_nonfixation）

注意：濃い部分は「注視時間の割合」（注視の総時間 ÷ 有効時間）とは定義が違う。注視時間は注視の重心の AOI で数え、
最初〜最後のサンプル＋1サンプル間隔で測るので、重心が AOI の外にある注視のサンプルや、注視の内部の短い欠測の分だけずれる。
参考として CSV に fixation_duration_pct_of_valid_time（aoi_metrics_session の total_fixation_duration_sec ÷ valid_time_sec）
も出す。図の説明文で濃い部分を「注視時間の割合」と書かないこと。

定義は作り直さない：
- 分母と Gaze % は gaze_metrics.metrics_by_segment / metrics_session（ver3 と同じ関数）を、ver3 が保存した
  samples_classified.csv と segments_used.csv に当てて求める
- 積み上げの合計（aoi_fixation ＋ aoi_nonfixation）が、ver3 の出力 aoi_metrics_session.csv の gaze_pct と一致するかを
  確かめ、ずれたら止める
- 下部HUDまとめの構成要素は idt_threshold_check.LOWER_HUD（ANALYSIS_PLAN §3 の主要比較の定義）を使う

止める条件：
- 参加者 ID が PL で始まらない（本実験のデータは主解析を固める前に見ない）。--allow-non-pilot のような抜け道は作らない
- 区間ファイルなしの結果
- AOI 設定（content_sha256）・aoi_analysis の版・I-DT などの解析パラメータがセッション間で違う
  （aggregate_sessions.check_params_consistent と同じ判定）。--check-only でセッションごとの一覧を出す
- 出力先に条件の違う同名の出力がある（--overwrite で上書き）

使い方（PowerShell）
--------------------
# まず条件がそろっているかだけを確かめる（図は作らない）
python .\\src\\analysis\\gaze_pct_fixation_split.py --check-only --results `
    .\\data\\processed\\aoi_result\\<PL01 の1回目の session_base> `
    .\\data\\processed\\aoi_result\\<PL01 の2回目の session_base> `
    .\\data\\processed\\aoi_result\\<PL02 の session_base> `
    .\\data\\processed\\aoi_result\\<PL03 の session_base>
# 図と値の CSV を作る（既定の出力先 data/processed/figures/）
python .\\src\\analysis\\gaze_pct_fixation_split.py --results <同じフォルダ>

出力（--out-dir、既定 data/processed/figures/）
  gaze_pct_fixation_split.png              図（--dpi、既定 200）
  gaze_pct_fixation_split.csv              図に描いた値（セッション × AOI）
  gaze_pct_fixation_split_provenance.json  入力の sha256・AOI のハッシュ・パラメータ・git commit
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_AOI_DIR = _HERE.parent / "aoi_detector"
for _p in (str(_AOI_DIR), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gaze_metrics as gm  # noqa: E402
from aggregate_sessions import check_params_consistent, condition_breakdown, format_condition_breakdown  # noqa: E402
from idt_threshold_check import LOWER_HUD, LOWER_HUD_LABEL, is_pilot, sha256_of  # noqa: E402

SCRIPT_VERSION = "1.0.0"
FIGURE_NAME = "gaze_pct_fixation_split"
_PROJECT_ROOT = _HERE.parent.parent
DEFAULT_OUT_DIR = _PROJECT_ROOT / "data" / "processed" / "figures"
DEFAULT_AOIS = ("minimap", "crosshair", LOWER_HUD_LABEL)
# 解析・考察から外した AOI（CLAUDE.md §2「credits の扱い」2026-10-28）。図にも描かない
EXCLUDED_AOIS = ("credits",)
# 積み上げの合計と ver3 の gaze_pct の一致の許容幅 [%ポイント]（同じ式なので丸め誤差だけ）
PCT_TOLERANCE = 1e-9

# 色：1つの色相（青）の濃淡。濃い＝aoi_fixation、薄い＝aoi_nonfixation
COLOR_FIXATION = "#1c5cab"
COLOR_NONFIXATION = "#9ec5f4"
COLOR_TEXT = "#222222"
COLOR_MUTED = "#666666"
COLOR_GRID = "#e3e3e3"

JAPANESE_FONT_FAMILIES = [
    "Noto Sans CJK JP", "Noto Sans JP", "Yu Gothic", "Meiryo", "BIZ UDPGothic", "IPAexGothic", "IPAGothic",
    "IPAPGothic", "Hiragino Sans", "Hiragino Kaku Gothic ProN", "MS Gothic", "TakaoGothic",
]

LABELS = {
    "ja": {
        "aoi": {"minimap": "ミニマップ", "crosshair": "クロスヘア", LOWER_HUD_LABEL: "下部HUDまとめ",
                "health_armor": "HP・アーマー", "abilities": "アビリティ", "ammo_weapon": "弾数・武器",
                "ally_team_status": "味方チームの状態", "enemy_team_status": "敵チームの状態",
                "round_timer": "ラウンドタイマー"},
        "fixation": "AOIへの注視（aoi_fixation）",
        "nonfixation": "非注視のAOI内サンプル（aoi_nonfixation）",
        "ylabel": "区間内の有効サンプルに占める割合（%）",
        "title": "AOI別 Gaze % の内訳（予備実験、生存中）",
        "subtitle": "棒全体＝Gaze %、濃い部分＝AOIへの注視のサンプル。棒の上：Gaze % ／ 注視に入った割合",
        "fix_share": "注視",
    },
    "en": {
        "aoi": {"minimap": "Minimap", "crosshair": "Crosshair", LOWER_HUD_LABEL: "Lower HUD (combined)",
                "health_armor": "Health / armor", "abilities": "Abilities", "ammo_weapon": "Ammo / weapon",
                "ally_team_status": "Ally team status", "enemy_team_status": "Enemy team status",
                "round_timer": "Round timer"},
        "fixation": "Fixation on AOI (aoi_fixation)",
        "nonfixation": "Non-fixation samples in AOI (aoi_nonfixation)",
        "ylabel": "Share of valid samples in segments (%)",
        "title": "Gaze % by AOI, split by fixation (pilot sessions, alive)",
        "subtitle": "Bar = Gaze %; dark = samples in fixations on the AOI. Above bar: Gaze % / share in fixation",
        "fix_share": "fix",
    },
}


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------

def read_qc(result_dir: Path) -> dict:
    qc_path = result_dir / "qc.json"
    if not qc_path.exists():
        raise ValueError(f"{result_dir} に qc.json がありません（ver3 の結果フォルダを指定してください）。")
    with open(qc_path, "r", encoding="utf-8") as f:
        qc = json.load(f)
    qc.setdefault("session_base", result_dir.name)
    return qc


def check_pilot_and_segments(qc: dict) -> str:
    """予備実験（PL〜）で、区間付きの結果か。参加者 ID を返す。"""
    sb = qc.get("session_base")
    pid = (qc.get("manifest_row") or {}).get("participant_id") or None
    if not is_pilot(pid):
        raise ValueError(
            f"{sb}：参加者 ID が {pid!r} です。この図は予備実験（参加者 ID が PL で始まるセッション）だけで作る取り決めです"
            "（主解析を固める前に本実験のデータを見ないため）。ver3 を --manifest 付きで実行した結果か確認してください。")
    if (qc.get("inputs") or {}).get("segments_file") is None:
        raise ValueError(f"{sb}：区間ファイルなし（記録全体）の結果です。生存区間で解析した結果を指定してください。")
    return str(pid)


def load_result(result_dir: Path, qc: dict) -> dict:
    """ver3 の結果フォルダから、図に必要な出力を読む（qc.json は読み込み済みのものを使う）。"""
    sb = qc["session_base"]
    paths = {name: result_dir / name for name in
             ("samples_classified.csv", "segments_used.csv", "aoi_metrics_session.csv")}
    for name, p in paths.items():
        if not p.exists():
            raise ValueError(f"{sb}：{name} がありません（ver3 の出力が欠けています）。")
    samples = pd.read_csv(paths["samples_classified.csv"], encoding="utf-8-sig",
                          usecols=["segment_id", "valid", "offscreen", "sample_aoi", "sample_class"])
    for col in ("valid", "offscreen"):
        if samples[col].dtype != bool:
            raise ValueError(f"{sb}：samples_classified.csv の {col} 列が True/False ではありません。")
    segments = pd.read_csv(paths["segments_used.csv"], encoding="utf-8-sig")
    stored = pd.read_csv(paths["aoi_metrics_session.csv"], encoding="utf-8-sig")
    aoi_names = (qc.get("aoi") or {}).get("priority_order")
    if not aoi_names:
        raise ValueError(f"{sb}：qc.json に aoi.priority_order（AOI の一覧）がありません。")
    dt = (qc.get("data_quality") or {}).get("nominal_sample_interval_sec")
    if not dt:
        raise ValueError(f"{sb}：qc.json に data_quality.nominal_sample_interval_sec がありません。")
    return {
        "session_base": sb,
        "participant_id": check_pilot_and_segments(qc),
        "qc": qc,
        "samples": samples,
        "segments": segments,
        "stored": stored,
        "aoi_names": list(aoi_names),
        "params": gm.merge_params(qc.get("params")),
        "sample_dt": float(dt),
        "samples_sha256": sha256_of(paths["samples_classified.csv"]),
        "result_dir": str(result_dir.resolve()),
    }


# ---------------------------------------------------------------------------
# AOI の指定
# ---------------------------------------------------------------------------

def resolve_aois(requested: "list[str] | tuple[str, ...]", aoi_names: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """描く AOI と、その構成要素（下部HUDまとめは3つ、他は自分だけ）。使えない名前はエラー。"""
    out = []
    seen = set()
    for name in requested:
        if name in seen:
            raise ValueError(f"AOI {name} が2回指定されています。")
        seen.add(name)
        if name in EXCLUDED_AOIS:
            raise ValueError(f"{name} は解析・考察から外した AOI です（CLAUDE.md §2「credits の扱い」2026-10-28）。")
        comps = LOWER_HUD if name == LOWER_HUD_LABEL else (name,)
        missing = [c for c in comps if c not in aoi_names]
        if missing:
            raise ValueError(f"AOI {name} の {missing} が AOI 設定にありません（使える名前：{aoi_names + [LOWER_HUD_LABEL]}）。")
        out.append((name, tuple(comps)))
    return out


# ---------------------------------------------------------------------------
# 値の計算（定義は gaze_metrics の関数を呼ぶ）
# ---------------------------------------------------------------------------

def recompute_session_metrics(res: dict) -> pd.DataFrame:
    """
    ver3 が保存したサンプルの分類と区間に、ver3 と同じ関数（metrics_by_segment → metrics_session）を当てて
    分母（gaze_pct_denominator_samples）と Gaze % を出す。注視・訪問の指標はこの図で使わないので空の表を渡す
    （Gaze % と分母は注視・訪問に依存しない）。
    """
    cats = gm.all_categories([{"name": n} for n in res["aoi_names"]])
    no_fix = pd.DataFrame(columns=["segment_id", "aoi", "duration_sec", "start_sync_sec"])
    no_visit = pd.DataFrame(columns=["segment_id", "aoi"])
    by_seg = gm.metrics_by_segment(res["samples"], no_fix, no_visit, res["segments"], cats, res["params"],
                                   res["sample_dt"])
    return gm.metrics_session(by_seg, cats).set_index("aoi")


def in_segment_mask(samples: pd.DataFrame, session_base: str) -> np.ndarray:
    """区間内のサンプル（sample_class != out_of_segment）。segment_id の有無と食い違えば止める。"""
    in_seg = (samples["sample_class"] != gm.OUT_OF_SEGMENT).to_numpy()
    if not np.array_equal(in_seg, samples["segment_id"].notna().to_numpy()):
        raise ValueError(f"{session_base}：samples_classified.csv の sample_class=out_of_segment と segment_id の空欄が一致しません。")
    return in_seg


def session_rows(res: dict, aois: list[tuple[str, tuple[str, ...]]]) -> list[dict]:
    """1セッション分の図の値。積み上げの合計が ver3 の gaze_pct と一致しなければ止める。"""
    sb = res["session_base"]
    s = res["samples"]
    in_seg = in_segment_mask(s, sb)
    cls = s["sample_class"].to_numpy()
    sample_aoi = s["sample_aoi"].to_numpy()
    recomputed = recompute_session_metrics(res)
    stored = res["stored"].set_index("aoi")
    rows = []
    for name, comps in aois:
        missing = [c for c in comps if c not in stored.index or c not in recomputed.index]
        if missing:
            raise ValueError(f"{sb}：aoi_metrics_session.csv に {missing} の行がありません。")
        denom = int(recomputed.loc[comps[0], "gaze_pct_denominator_samples"])
        stored_denom = int(stored.loc[comps[0], "gaze_pct_denominator_samples"])
        if denom != stored_denom or denom <= 0:
            raise ValueError(f"{sb}：分母（有効サンプル数）が ver3 の出力と違うか0です（再計算 {denom}、出力 {stored_denom}）。")
        in_comps = in_seg & np.isin(sample_aoi, comps)
        n_fix = int((in_comps & (cls == "aoi_fixation")).sum())
        n_nonfix = int((in_comps & (cls == "aoi_nonfixation")).sum())
        n_in = int(recomputed.loc[list(comps), "n_samples_in_aoi"].sum())
        if n_fix + n_nonfix != n_in:
            raise ValueError(f"{sb} {name}：aoi_fixation（{n_fix}）＋ aoi_nonfixation（{n_nonfix}）が AOI 内の有効サンプル数"
                             f"（{n_in}）と一致しません。samples_classified.csv が ver3 の分類と合っていない可能性があります。")
        gaze_pct_recomputed = float(recomputed.loc[list(comps), "gaze_pct"].sum())
        gaze_pct_stored = float(stored.loc[list(comps), "gaze_pct"].sum())
        fix_pct = 100.0 * n_fix / denom
        nonfix_pct = 100.0 * n_nonfix / denom
        total = fix_pct + nonfix_pct
        for label, v in (("再計算した gaze_pct", gaze_pct_recomputed), ("ver3 の出力の gaze_pct", gaze_pct_stored)):
            if not np.isfinite(v) or abs(total - v) > PCT_TOLERANCE:
                raise ValueError(f"{sb} {name}：積み上げの合計 {total:.12f}% が{label} {v:.12f}% と一致しません。中止します。")
        valid_time = float(stored.loc[comps[0], "valid_time_sec"])
        fix_dur = float(stored.loc[list(comps), "total_fixation_duration_sec"].sum())
        rows.append({
            "session_base": sb,
            "participant_id": res["participant_id"],
            "aoi": name,
            "aoi_components": "+".join(comps),
            "n_samples_aoi_fixation": n_fix,
            "n_samples_aoi_nonfixation": n_nonfix,
            "n_samples_in_aoi": n_in,
            "gaze_pct_denominator_samples": denom,
            "offscreen_in_denominator": bool(res["params"]["offscreen_in_denominator"]),
            "aoi_fixation_pct": fix_pct,
            "aoi_nonfixation_pct": nonfix_pct,
            "gaze_pct": total,
            "gaze_pct_ver3_output": gaze_pct_stored,
            "fixation_share_of_aoi_samples": (n_fix / n_in) if n_in else np.nan,
            # 参考（定義が違う）：注視の総時間 ÷ 有効時間。注視は重心の AOI で数える
            "fixation_duration_pct_of_valid_time": 100.0 * fix_dur / valid_time if valid_time > 0 else np.nan,
            "valid_time_sec": valid_time,
            "analysis_time_sec": float(stored.loc[comps[0], "analysis_time_sec"]),
        })
    return rows


def session_labels(results: list[dict]) -> dict:
    """横軸のラベル。1人1セッションなら参加者 ID、複数なら ID-月日（例 PL01-1029）。それでも重なれば番号を足す。"""
    by_pid: dict[str, list[dict]] = {}
    for r in results:
        by_pid.setdefault(r["participant_id"], []).append(r)
    labels = {}
    for pid, rs in by_pid.items():
        if len(rs) == 1:
            labels[rs[0]["session_base"]] = pid
            continue
        for r in rs:
            m = re.search(r"_(\d{4})(\d{2})(\d{2})_\d{6}$", r["session_base"])
            labels[r["session_base"]] = f"{pid}-{m.group(2)}{m.group(3)}" if m else pid
    counts: dict[str, int] = {}
    for sb in [r["session_base"] for r in results]:
        lab = labels[sb]
        n = sum(1 for v in labels.values() if v == lab)
        if n > 1:
            counts[lab] = counts.get(lab, 0) + 1
            labels[sb] = f"{lab}-{counts[lab]}"
    return labels


def build_table(results: list[dict], aois: list[tuple[str, tuple[str, ...]]]) -> pd.DataFrame:
    """全セッションの値。並びは参加者 ID → セッション名（時刻）の順。"""
    ordered = sorted(results, key=lambda r: (r["participant_id"], r["session_base"]))
    labels = session_labels(ordered)
    rows = []
    for r in ordered:
        for row in session_rows(r, aois):
            rows.append({"session_label": labels[r["session_base"]], **row})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 条件の一致と上書き防止
# ---------------------------------------------------------------------------

def check_conditions(qcs: list[dict]) -> list[str]:
    """aggregate_sessions と同じ判定（AOI の content_sha256、aoi_analysis の版、I-DT などのパラメータ）。"""
    return check_params_consistent(qcs)


def git_state() -> dict:
    """図を作ったときのこのリポジトリの commit と、未コミットの変更の有無。"""
    def run(args):
        try:
            out = subprocess.run(["git", *args], cwd=_PROJECT_ROOT, capture_output=True, text=True, timeout=5)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None
    commit = run(["rev-parse", "HEAD"])
    status = run(["status", "--porcelain", "--untracked-files=no"])
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def provenance_conditions(results: list[dict], aois: list[tuple[str, tuple[str, ...]]]) -> dict:
    """値を変える条件（上書きしてよいかの判定に使う）。言語・dpi は値を変えないので入れない。"""
    q0 = results[0]["qc"]
    aoi_cfg = (q0.get("inputs") or {}).get("aoi_config") or {}
    return {
        "sessions": sorted(({"session_base": r["session_base"], "participant_id": r["participant_id"],
                             "samples_classified_sha256": r["samples_sha256"]} for r in results),
                           key=lambda d: d["session_base"]),
        "aois": [{"aoi": n, "components": list(c)} for n, c in aois],
        "aoi_config_content_sha256": aoi_cfg.get("content_sha256") or aoi_cfg.get("sha256"),
        "aoi_analysis_version": (q0.get("script") or {}).get("version"),
        "params": q0.get("params"),
    }


def check_existing_output(out_dir: Path, conditions: dict, overwrite: bool) -> None:
    """同じ出力先に条件の違う同名の出力があれば上書きしない（aggregate_sessions・ver3 と同じ流儀）。"""
    prov = out_dir / f"{FIGURE_NAME}_provenance.json"
    others = [out_dir / f"{FIGURE_NAME}{ext}" for ext in (".png", ".csv")]
    if overwrite:
        return
    if not prov.exists():
        if any(p.exists() for p in others):
            raise SystemExit(f"エラー: {out_dir} に {FIGURE_NAME} の図か CSV がありますが、条件の記録（provenance）がありません。"
                             "確認してから --overwrite で上書きするか、--out-dir を分けてください。")
        return
    try:
        with open(prov, "r", encoding="utf-8") as f:
            old = json.load(f).get("conditions")
    except (OSError, ValueError):
        raise SystemExit(f"エラー: 既存の {prov} を読めません。確認してから --overwrite で上書きしてください。")
    if old != json.loads(json.dumps(conditions)):
        diffs = sorted(k for k in set(conditions) | set(old or {}) if (old or {}).get(k) != json.loads(json.dumps(conditions)).get(k))
        raise SystemExit(f"エラー: {out_dir} には条件の違う {FIGURE_NAME} があります（違い: {diffs}）。上書きしないよう中止しました。\n"
                         "  出力先を分ける（--out-dir）か、意図して置き換える場合だけ --overwrite を付けてください。")


# ---------------------------------------------------------------------------
# 図
# ---------------------------------------------------------------------------

def find_japanese_font() -> "str | None":
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}
    for fam in JAPANESE_FONT_FAMILIES:
        if fam in available:
            return fam
    return None


def choose_language(lang: str) -> tuple[str, "str | None"]:
    """(使う言語, 日本語フォント名)。auto/ja で日本語フォントが無ければ英語にする。"""
    if lang == "en":
        return "en", None
    font = find_japanese_font()
    if font is None:
        print("⚠ 日本語のフォント（Noto Sans CJK JP / Yu Gothic / Meiryo など）が見つからないので、英語のラベルで描きます。")
        return "en", None
    return "ja", font


def footer_text(table: pd.DataFrame, conditions: dict, git: dict) -> str:
    commit = (git.get("commit") or "unknown")[:12] + ("+dirty" if git.get("dirty") else "")
    aoi_hash = (conditions.get("aoi_config_content_sha256") or "unknown")[:12]
    sessions = ", ".join(dict.fromkeys(table["session_base"]))
    text = (f"{FIGURE_NAME}.py {SCRIPT_VERSION} | git {commit} | aoi_analysis {conditions.get('aoi_analysis_version')} | "
            f"AOI {aoi_hash} | sessions: {sessions}")
    return "\n".join(textwrap.wrap(text, width=150))


def plot_figure(table: pd.DataFrame, aois: list[tuple[str, tuple[str, ...]]], footer: str, lang: str,
                font: "str | None", out_png: Path, dpi: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    L = LABELS[lang]
    rc = {"font.size": 9, "axes.edgecolor": COLOR_MUTED, "axes.labelcolor": COLOR_TEXT,
          "xtick.color": COLOR_TEXT, "ytick.color": COLOR_MUTED, "text.color": COLOR_TEXT}
    if font:
        rc["font.family"] = font
    labels_order = list(dict.fromkeys(table["session_label"]))
    n_panels = len(aois)
    with plt.rc_context(rc):
        fig, axes = plt.subplots(1, n_panels, figsize=(max(3.0, 0.75 * len(labels_order) + 1.2) * n_panels, 4.4),
                                 squeeze=False)
        axes = axes[0]
        x = np.arange(len(labels_order))
        for ax, (name, comps) in zip(axes, aois):
            d = table[table["aoi"] == name].set_index("session_label").reindex(labels_order)
            fix = d["aoi_fixation_pct"].to_numpy(dtype=float)
            nonfix = d["aoi_nonfixation_pct"].to_numpy(dtype=float)
            total = fix + nonfix
            # 区切りは白の隙間（surface gap）。枠線ではない
            ax.bar(x, fix, width=0.6, color=COLOR_FIXATION, edgecolor="white", linewidth=1.2, zorder=3)
            ax.bar(x, nonfix, bottom=fix, width=0.6, color=COLOR_NONFIXATION, edgecolor="white", linewidth=1.2,
                   zorder=3)
            top = float(np.nanmax(total)) if np.isfinite(total).any() and np.nanmax(total) > 0 else 1.0
            ax.set_ylim(0, top * 1.28)
            for xi, t, share in zip(x, total, d["fixation_share_of_aoi_samples"].to_numpy(dtype=float)):
                share_txt = f"{L['fix_share']} {share:.0%}" if np.isfinite(share) else f"{L['fix_share']} –"
                ax.annotate(f"{t:.1f}%", (xi, t), xytext=(0, 13), textcoords="offset points", ha="center",
                            va="bottom", fontsize=8.5, color=COLOR_TEXT)
                ax.annotate(share_txt, (xi, t), xytext=(0, 3), textcoords="offset points", ha="center",
                            va="bottom", fontsize=7, color=COLOR_MUTED)
            title = L["aoi"].get(name, name)
            ax.set_title(title, fontsize=10, pad=15)  # 下部HUDまとめの構成要素の行の分をそろえて空ける
            if name == LOWER_HUD_LABEL:
                ax.text(0.5, 1.01, "＋".join(comps) if lang == "ja" else " + ".join(comps), transform=ax.transAxes,
                        ha="center", va="bottom", fontsize=6.5, color=COLOR_MUTED)
            ax.set_xticks(x, labels_order)
            ax.yaxis.grid(True, color=COLOR_GRID, linewidth=0.8, zorder=0)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.tick_params(axis="x", length=0)
        axes[0].set_ylabel(L["ylabel"])
        handles = [Patch(color=COLOR_FIXATION, label=L["fixation"]),
                   Patch(color=COLOR_NONFIXATION, label=L["nonfixation"])]
        fig.suptitle(L["title"], fontsize=11, x=0.01, ha="left", y=0.995)
        fig.text(0.01, 0.925, L["subtitle"], fontsize=7.5, color=COLOR_MUTED, ha="left", va="center")
        fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.995, 0.995), ncol=2, frameon=False,
                   fontsize=8)
        fig.text(0.01, 0.01, footer, fontsize=5.5, color=COLOR_MUTED, ha="left", va="bottom")
        n_footer = footer.count("\n") + 1
        fig.tight_layout(rect=(0, 0.035 + 0.02 * n_footer, 1, 0.92))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fig.savefig(out_png, dpi=dpi, facecolor="white")
        plt.close(fig)


# ---------------------------------------------------------------------------
# 全体
# ---------------------------------------------------------------------------

def to_jsonable(o):
    if isinstance(o, dict):
        return {k: to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(float(o)) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def build_arg_parser():
    p = argparse.ArgumentParser(description="予備実験の AOI 別 Gaze % を、AOI への注視と非注視に分けた積み上げ棒グラフにする。")
    p.add_argument("--results", nargs="+", required=True, help="ver3 の主解析の結果フォルダ（qc.json のあるフォルダ）")
    p.add_argument("--aois", nargs="+", default=list(DEFAULT_AOIS),
                   help=f"描く AOI（既定: {' '.join(DEFAULT_AOIS)}。{LOWER_HUD_LABEL} は {'+'.join(LOWER_HUD)}）")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="出力先（既定 data/processed/figures）")
    p.add_argument("--lang", choices=("auto", "ja", "en"), default="auto",
                   help="ラベルの言語（auto/ja：日本語のフォントが無ければ英語）")
    p.add_argument("--dpi", type=int, default=200, help="PNG の解像度（150 以上）")
    p.add_argument("--check-only", action="store_true",
                   help="AOI 設定・aoi_analysis の版・解析パラメータがセッション間でそろっているかを一覧にするだけ（図は作らない）")
    p.add_argument("--overwrite", action="store_true", help="出力先に条件の違う同名の出力があっても上書きする")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.dpi < 150:
        raise SystemExit("エラー: --dpi は 150 以上にしてください。")
    out_dir = Path(args.out_dir)
    if out_dir.resolve().is_relative_to((_PROJECT_ROOT / "data" / "raw").resolve()):
        raise SystemExit("エラー: data/raw の下には出力しません。")

    dirs = [Path(d) for d in args.results]
    qcs = []
    try:
        for d in dirs:
            qc = read_qc(d)
            check_pilot_and_segments(qc)
            qcs.append(qc)
    except ValueError as e:
        raise SystemExit(f"エラー: {e}")
    names = [q["session_base"] for q in qcs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise SystemExit(f"エラー: 同じセッションが2回指定されています: {dup}")

    problems = check_conditions(qcs)
    if args.check_only:
        print(format_condition_breakdown(condition_breakdown(qcs), only_differing=False))
        if problems:
            raise SystemExit("\n条件がそろっていません（上の「⚠」の項目）。同じ AOI 設定・同じ版・同じパラメータで解析し直してください。")
        print("\n条件はそろっています。")
        return
    if problems:
        for pr in problems:
            print(f"⚠ {pr}")
        print(format_condition_breakdown(condition_breakdown(qcs), only_differing=True))
        raise SystemExit("エラー: 条件の違う結果は1枚の図にできません。同じ AOI 設定・同じ版・同じパラメータで解析し直してください。")

    try:
        results = [load_result(d, q) for d, q in zip(dirs, qcs)]
        aois = resolve_aois(args.aois, results[0]["aoi_names"])
        for r in results[1:]:
            resolve_aois(args.aois, r["aoi_names"])
        table = build_table(results, aois)
    except ValueError as e:
        raise SystemExit(f"エラー: {e}")

    conditions = provenance_conditions(results, aois)
    check_existing_output(out_dir, conditions, args.overwrite)
    lang, font = choose_language(args.lang)
    git = git_state()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"{FIGURE_NAME}.csv"
    out_png = out_dir / f"{FIGURE_NAME}.png"
    table.to_csv(out_csv, index=False, encoding="utf-8-sig")
    plot_figure(table, aois, footer_text(table, conditions, git), lang, font, out_png, args.dpi)
    prov = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": {"name": Path(__file__).name, "version": SCRIPT_VERSION, "git_commit": git["commit"],
                   "git_dirty": git["dirty"]},
        "conditions": conditions,
        "results_dirs": [r["result_dir"] for r in results],
        "lang": lang, "font": font, "dpi": args.dpi,
        "definitions": {
            "gaze_pct": "gaze_metrics.metrics_session の gaze_pct（区間内で sample_aoi がその AOI の有効サンプル数 / 区間内の有効サンプル数）。"
                        "下部HUDまとめは構成要素の和",
            "aoi_fixation_pct": "区間内の sample_class=aoi_fixation のサンプル数 / 同じ分母",
            "aoi_nonfixation_pct": "区間内の sample_class=aoi_nonfixation のサンプル数 / 同じ分母",
            "fixation_share_of_aoi_samples": "aoi_fixation / (aoi_fixation + aoi_nonfixation)",
            "fixation_duration_pct_of_valid_time": "参考。aoi_metrics_session の total_fixation_duration_sec / valid_time_sec。"
                                                   "注視は重心の AOI で数えるので aoi_fixation_pct とは一致しない",
            "check": f"積み上げの合計が、再計算した gaze_pct と ver3 の aoi_metrics_session.csv の gaze_pct の両方と "
                     f"{PCT_TOLERANCE:g} %ポイント以内で一致することを確認済み",
        },
    }
    with open(out_dir / f"{FIGURE_NAME}_provenance.json", "w", encoding="utf-8") as f:
        json.dump(to_jsonable(prov), f, ensure_ascii=False, indent=2)

    show = table[["session_label", "aoi", "gaze_pct", "aoi_fixation_pct", "fixation_share_of_aoi_samples",
                  "fixation_duration_pct_of_valid_time"]]
    with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 160):
        print(show.to_string(index=False))
    print(f"\n積み上げの合計は ver3 の gaze_pct と一致しました（{len(table)} 行）。")
    print(f"出力：{out_png}\n      {out_csv}")


if __name__ == "__main__":
    main()
