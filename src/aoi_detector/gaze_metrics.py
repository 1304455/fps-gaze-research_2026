# -*- coding: utf-8 -*-
"""
視線データの AOI 解析ロジック（aoi_analysis-ver3.py の本体。テストから import する）。

処理順（CLAUDE.md の合意事項どおり）
------------------------------------
1. 有効性判定      gaze_missing=False、左右いずれかの validity=1、座標が非NaN。
                   画面外（0未満・1超）は「有効だが offscreen」として区別する
2. 区間付与        各サンプルに segment_id を付ける。区間外は行を消さず segment_id=NaN
3. 注視検出（I-DT） 区間ごと、かつ欠測ギャップで系列を分割した「チャンク」の内部でだけ検出する。
                   分散 D = (max θx − min θx) + (max θy − min θy) [deg]
4. AOI割り当て     サンプル単位（Gaze % 用）と注視単位（注視の重心で判定）
5. 訪問・遷移      同一AOIへの連続注視を1訪問にまとめる。区間・チャンクをまたがない

用語（卒論でもこの語を使う）
--------------------------
- sample_aoi   ：サンプルの視線位置が入っている AOI（outside / offscreen / invalid を含む）
- sample_class ：aoi_fixation（AOI内の視線位置で、かつ注視に属する）
                 aoi_nonfixation（AOI内の視線位置だが、注視に属さない）
                 outside（画面内だがどのAOIにも入らない）／ offscreen（画面外）／ invalid（無効・判定不能）
                 区間外のサンプルは out_of_segment（注視検出の対象外。sample_aoi は付く）
- fixation_aoi ：注視の重心が入っている AOI
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd

from aoi_geometry import (
    INVALID_LABEL,
    OFFSCREEN_LABEL,
    OUTSIDE_LABEL,
    DisplayGeometry,
    assign_aoi,
    norm_to_deg,
    offscreen_edge,
    offscreen_mask,
)

REQUIRED_COLUMNS = [
    "pc_time_sec", "center_x", "center_y",
    "left_gaze_point_validity", "right_gaze_point_validity", "gaze_missing",
]

SAMPLE_CLASSES = ["aoi_fixation", "aoi_nonfixation", "outside", "offscreen", "invalid"]
# 分析区間外のサンプル。注視検出をしていないので上の5分類に入れない（行は削除せず残す）
OUT_OF_SEGMENT = "out_of_segment"

# 浮動小数の比較誤差で 100 ms ちょうどの注視が落ちないための許容幅 [sec]
_TIME_EPS = 1e-6

# 解析パラメータの既定値。研究上の定義に関わる値は「要合意」として
# docs/IMPROVEMENT_PLAN.md の T4 決定事項に対応させている。
DEFAULT_PARAMS = {
    # 眼−画面距離 [mm]。主解析は固定値（推奨）。"manifest" にすると台帳の実測値を使う
    "eye_screen_distance_mm": 650.0,
    "eye_screen_distance_source": "fixed",
    # I-DT（Salvucci & Goldberg, 2000）
    "idt_min_duration_ms": 100.0,
    "idt_max_dispersion_deg": 1.0,
    # 有効サンプル間の時間差がこれを超えたら系列を分割する（決定事項1）
    "max_gap_ms": 75.0,
    # Gaze % の分母に offscreen（有効だが画面外）を含めるか（決定事項2）
    "offscreen_in_denominator": True,
    # 注視時間・ギャップ判定に使う時刻列（決定事項4）。区間付与・同期は常に pc_time_sec
    "duration_time_column": "pc_time_sec",
    # 主解析に使う区間の種類
    "segment_types": ["alive"],
    # 欠測ギャップをまたぐ同一AOIの連続注視を別訪問として数えるか
    "split_visits_at_gaps": True,
}


def merge_params(user_params: "dict | None") -> dict:
    """既定値にユーザー指定を上書きした解析パラメータを返す。未知のキーはエラー。"""
    params = copy.deepcopy(DEFAULT_PARAMS)
    if user_params:
        unknown = sorted(set(user_params) - set(DEFAULT_PARAMS) - {"_comment"})
        if unknown:
            raise ValueError(f"未知の解析パラメータがあります: {unknown}")
        params.update({k: v for k, v in user_params.items() if k != "_comment"})
    if params["duration_time_column"] not in ("pc_time_sec", "system_time_stamp_us"):
        raise ValueError("duration_time_column は pc_time_sec か system_time_stamp_us です。")
    if params["eye_screen_distance_source"] not in ("fixed", "manifest"):
        raise ValueError("eye_screen_distance_source は fixed か manifest です。")
    return params


def all_categories(aois: list[dict]) -> list[str]:
    """設定順を保った全カテゴリ（AOI名 + outside + offscreen）。0件でも必ず行を出すための基準。"""
    return [a["name"] for a in aois] + [OUTSIDE_LABEL, OFFSCREEN_LABEL]


# ---------------------------------------------------------------------------
# 1. 有効性判定
# ---------------------------------------------------------------------------

def validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSVに必要な列がありません: {missing}")


def compute_valid_mask(df: pd.DataFrame) -> np.ndarray:
    """gaze_missing=False、かつ左右いずれかの validity=1、かつ center 座標が非NaN。"""
    gaze_missing = df["gaze_missing"].astype(str).str.strip().str.lower().eq("true").to_numpy()
    left_ok = pd.to_numeric(df["left_gaze_point_validity"], errors="coerce").eq(1).to_numpy()
    right_ok = pd.to_numeric(df["right_gaze_point_validity"], errors="coerce").eq(1).to_numpy()
    x = pd.to_numeric(df["center_x"], errors="coerce").to_numpy()
    y = pd.to_numeric(df["center_y"], errors="coerce").to_numpy()
    has_xy = ~np.isnan(x) & ~np.isnan(y)
    return (~gaze_missing) & (left_ok | right_ok) & has_xy


def duration_time_seconds(df: pd.DataFrame, column: str) -> np.ndarray:
    """注視時間の計算に使う時刻 [sec]。system_time_stamp_us は µs → s に換算し先頭を 0 にする。"""
    if column == "pc_time_sec":
        return pd.to_numeric(df["pc_time_sec"], errors="coerce").to_numpy(dtype=float)
    if column not in df.columns:
        raise ValueError(f"時刻列 {column} がCSVにありません。")
    t = pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
    if np.isnan(t).any():
        raise ValueError(f"時刻列 {column} に欠測があります。pc_time_sec を使ってください。")
    return (t - t[0]) / 1e6


def estimate_sample_dt(t: np.ndarray) -> float:
    """名目サンプル間隔 [sec]。正の時間差の中央値（上位1%の外れ値は除く）。"""
    t = np.asarray(t, dtype=float)
    t = t[~np.isnan(t)]
    if len(t) < 2:
        return 0.0
    d = np.diff(t)
    d = d[d > 0]
    if len(d) == 0:
        return 0.0
    d = d[d <= np.quantile(d, 0.99)]
    return float(np.median(d)) if len(d) else 0.0


# ---------------------------------------------------------------------------
# 2. 区間
# ---------------------------------------------------------------------------

SEGMENT_COLUMNS = ["round", "segment_type", "video_start_sec", "video_end_sec", "end_reason", "side", "notes"]


def segments_from_video_time(seg_df: pd.DataFrame, offset_sec: float, segment_types: list[str]) -> pd.DataFrame:
    """
    区間定義（動画時刻）を視線時刻に変換し、segment_id を振る。
    gaze_time = video_time + offset_sec（gaze_visualizer_v3 の sync と同じ定義）。
    """
    missing = [c for c in ("round", "segment_type", "video_start_sec", "video_end_sec") if c not in seg_df.columns]
    if missing:
        raise ValueError(f"区間ファイルに必要な列がありません: {missing}")
    seg = seg_df.copy()
    for c in SEGMENT_COLUMNS:
        if c not in seg.columns:
            seg[c] = np.nan
    seg = seg[seg["segment_type"].astype(str).isin(segment_types)].copy()
    seg["video_start_sec"] = pd.to_numeric(seg["video_start_sec"], errors="raise")
    seg["video_end_sec"] = pd.to_numeric(seg["video_end_sec"], errors="raise")
    bad = seg[seg["video_end_sec"] <= seg["video_start_sec"]]
    if not bad.empty:
        raise ValueError(f"終了 <= 開始 の区間があります（round={bad['round'].tolist()}）")
    seg = seg.sort_values("video_start_sec").reset_index(drop=True)
    overlap = seg["video_start_sec"].iloc[1:].to_numpy() < seg["video_end_sec"].iloc[:-1].to_numpy()
    if overlap.any():
        rounds = seg["round"].iloc[1:][overlap].tolist()
        raise ValueError(f"区間が重なっています（round={rounds}）")
    seg.insert(0, "segment_id", np.arange(len(seg), dtype=int))
    seg["gaze_start_sec"] = seg["video_start_sec"] + offset_sec
    seg["gaze_end_sec"] = seg["video_end_sec"] + offset_sec
    return seg


def whole_session_segment(t_sync: np.ndarray, sample_dt: float) -> pd.DataFrame:
    """区間ファイルが無い場合に、記録全体を1区間として扱うための区間表。"""
    t = t_sync[~np.isnan(t_sync)]
    start, end = float(t.min()), float(t.max()) + sample_dt
    return pd.DataFrame([{
        "segment_id": 0, "round": np.nan, "segment_type": "whole_session",
        "video_start_sec": np.nan, "video_end_sec": np.nan, "end_reason": np.nan,
        "side": np.nan, "notes": "区間ファイルなし（記録全体）",
        "gaze_start_sec": start, "gaze_end_sec": end,
    }])


def assign_segments(t_sync: np.ndarray, segments: pd.DataFrame) -> np.ndarray:
    """各サンプルの segment_id（区間外は NaN）。区間は [start, end)。"""
    seg_id = np.full(len(t_sync), np.nan)
    for row in segments.itertuples(index=False):
        m = (t_sync >= row.gaze_start_sec) & (t_sync < row.gaze_end_sec)
        seg_id[m] = row.segment_id
    return seg_id


# ---------------------------------------------------------------------------
# 3. 注視検出（I-DT）
# ---------------------------------------------------------------------------

def build_chunks(t: np.ndarray, valid: np.ndarray, segment_id: np.ndarray, max_gap_sec: float) -> np.ndarray:
    """
    注視検出の単位（チャンク）を作る。有効かつ区間内のサンプルだけを時刻順に見て、
    区間が変わる、または有効サンプル間の時間差が max_gap_sec を超えるところで分割する。
    対象外のサンプルは NaN。
    """
    chunk = np.full(len(t), np.nan)
    idx = np.flatnonzero(valid & ~np.isnan(segment_id))
    if len(idx) == 0:
        return chunk
    idx = idx[np.argsort(t[idx], kind="stable")]
    tt = t[idx]
    ss = segment_id[idx]
    new_chunk = np.ones(len(idx), dtype=bool)
    new_chunk[1:] = (ss[1:] != ss[:-1]) | ((tt[1:] - tt[:-1]) > max_gap_sec + _TIME_EPS)
    chunk[idx] = np.cumsum(new_chunk) - 1
    return chunk


def _dispersion(ax: np.ndarray, ay: np.ndarray) -> float:
    return float((ax.max() - ax.min()) + (ay.max() - ay.min()))


def detect_fixations_idt(t: np.ndarray, ax: np.ndarray, ay: np.ndarray, *,
                         min_duration_sec: float, max_dispersion_deg: float,
                         sample_dt: float) -> list[tuple[int, int]]:
    """
    1チャンク分（時刻順・欠測なし）に I-DT を適用し、注視の (開始index, 終了index)（両端含む）を返す。

    窓の時間 = t[j] − t[i] + sample_dt（最後のサンプルの持続分を足す）。
    1. 窓の時間が min_duration_sec 以上になる最小の窓を作る
    2. 分散 <= 閾値なら、閾値を超えるまで窓を1サンプルずつ広げ、それを注視とする
    3. 分散 > 閾値なら、窓の先頭を1サンプル進める
    """
    n = len(t)
    fixations = []
    i = 0
    while i < n:
        target = t[i] + min_duration_sec - sample_dt - _TIME_EPS
        j = int(np.searchsorted(t, target, side="left"))
        j = max(j, i)
        if j >= n:
            break
        if _dispersion(ax[i:j + 1], ay[i:j + 1]) <= max_dispersion_deg:
            xmin, xmax = ax[i:j + 1].min(), ax[i:j + 1].max()
            ymin, ymax = ay[i:j + 1].min(), ay[i:j + 1].max()
            k = j + 1
            while k < n:
                nxmin, nxmax = min(xmin, ax[k]), max(xmax, ax[k])
                nymin, nymax = min(ymin, ay[k]), max(ymax, ay[k])
                if (nxmax - nxmin) + (nymax - nymin) > max_dispersion_deg:
                    break
                xmin, xmax, ymin, ymax = nxmin, nxmax, nymin, nymax
                k += 1
            fixations.append((i, k - 1))
            i = k
        else:
            i += 1
    return fixations


# ---------------------------------------------------------------------------
# セッション解析（1〜5 をまとめて実行）
# ---------------------------------------------------------------------------

def analyze_session(df: pd.DataFrame, aois: list[dict], display: DisplayGeometry,
                    params: dict, segments: "pd.DataFrame | None") -> dict:
    """
    1セッション分を解析し、出力用の DataFrame と QC 用の値を dict で返す。

    df       : 視線CSVをそのまま読んだもの（行の削除・並べ替えはしない）
    segments : segments_from_video_time() の結果。None なら記録全体を1区間とする
    """
    validate_columns(df)
    distance_mm = float(params["eye_screen_distance_mm"])
    cats = all_categories(aois)
    aoi_names = [a["name"] for a in aois]

    t_sync = pd.to_numeric(df["pc_time_sec"], errors="coerce").to_numpy(dtype=float)
    t = duration_time_seconds(df, params["duration_time_column"])
    x = pd.to_numeric(df["center_x"], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(df["center_y"], errors="coerce").to_numpy(dtype=float)
    if np.any(np.diff(t_sync[~np.isnan(t_sync)]) < 0):
        raise ValueError("pc_time_sec が単調増加していません。行の並べ替え・削除がされていないか確認してください。")

    sample_dt = estimate_sample_dt(t)
    if sample_dt <= 0:
        raise ValueError("サンプル間隔を推定できません。")

    # 1. 有効性
    valid = compute_valid_mask(df)
    offscreen = valid & offscreen_mask(x, y)

    # 2. 区間
    if segments is None:
        segments = whole_session_segment(t_sync, sample_dt)
    segment_id = assign_segments(t_sync, segments)
    in_seg = ~np.isnan(segment_id)

    # サンプル位置のAOI（Gaze % 用）
    sample_aoi = assign_aoi(x, y, aois)
    sample_aoi[offscreen] = OFFSCREEN_LABEL
    sample_aoi[~valid] = INVALID_LABEL

    # 3. 注視検出
    ax, ay = norm_to_deg(x, y, display, distance_mm)
    max_gap = params["max_gap_ms"] / 1000.0
    chunk = build_chunks(t, valid, segment_id, max_gap)
    fixation_id = np.full(len(df), np.nan)
    fix_rows = []
    for cid in np.unique(chunk[~np.isnan(chunk)]):
        idx = np.flatnonzero(chunk == cid)
        idx = idx[np.argsort(t[idx], kind="stable")]
        for s, e in detect_fixations_idt(
            t[idx], ax[idx], ay[idx],
            min_duration_sec=params["idt_min_duration_ms"] / 1000.0,
            max_dispersion_deg=params["idt_max_dispersion_deg"],
            sample_dt=sample_dt,
        ):
            members = idx[s:e + 1]
            fid = len(fix_rows)
            fixation_id[members] = fid
            fix_rows.append({
                "fixation_id": fid,
                "segment_id": int(segment_id[members[0]]),
                "chunk_id": int(cid),
                "start_sync_sec": float(t_sync[members[0]]),
                "end_sync_sec": float(t_sync[members[-1]]),
                "duration_sec": float(t[members[-1]] - t[members[0]] + sample_dt),
                "centroid_x": float(x[members].mean()),
                "centroid_y": float(y[members].mean()),
                "dispersion_deg": _dispersion(ax[members], ay[members]),
                "n_samples": int(len(members)),
            })
    fixations = pd.DataFrame(fix_rows, columns=[
        "fixation_id", "segment_id", "chunk_id", "start_sync_sec", "end_sync_sec", "duration_sec",
        "centroid_x", "centroid_y", "dispersion_deg", "n_samples",
    ])

    # 4. 注視のAOI（重心で判定）
    if len(fixations):
        cx = fixations["centroid_x"].to_numpy()
        cy = fixations["centroid_y"].to_numpy()
        fa = assign_aoi(cx, cy, aois)
        fa[offscreen_mask(cx, cy)] = OFFSCREEN_LABEL
        fixations["aoi"] = fa
    else:
        fixations["aoi"] = pd.Series(dtype=object)

    fixation_aoi = np.full(len(df), "", dtype=object)
    has_fix = ~np.isnan(fixation_id)
    if has_fix.any():
        fixation_aoi[has_fix] = fixations["aoi"].to_numpy()[fixation_id[has_fix].astype(int)]

    # サンプル分類
    sample_class = np.full(len(df), OUTSIDE_LABEL, dtype=object)
    in_aoi = np.isin(sample_aoi, aoi_names)
    sample_class[in_aoi & has_fix] = "aoi_fixation"
    sample_class[in_aoi & ~has_fix] = "aoi_nonfixation"
    sample_class[sample_aoi == OFFSCREEN_LABEL] = OFFSCREEN_LABEL
    sample_class[~valid] = INVALID_LABEL
    sample_class[~in_seg] = OUT_OF_SEGMENT

    samples = pd.DataFrame({
        "pc_time_sec": t_sync,
        "duration_time_sec": t,
        "center_x": x,
        "center_y": y,
        "valid": valid,
        "offscreen": offscreen,
        "offscreen_edge": np.where(offscreen, offscreen_edge(x, y), ""),
        "segment_id": segment_id,
        "chunk_id": chunk,
        "sample_aoi": sample_aoi,
        "fixation_id": fixation_id,
        "fixation_aoi": fixation_aoi,
        "sample_class": sample_class,
    })

    # 5. 訪問・遷移
    visits = build_visits(fixations, params["split_visits_at_gaps"])
    transitions_long, transitions_matrix = build_transitions(visits, cats, params["split_visits_at_gaps"])

    by_segment = metrics_by_segment(samples, fixations, visits, segments, cats, params, sample_dt)
    session = metrics_session(by_segment, cats)

    return {
        "samples": samples,
        "fixations": fixations,
        "visits": visits,
        "segments": segments,
        "metrics_by_segment": by_segment,
        "metrics_session": session,
        "transitions_long": transitions_long,
        "transitions_matrix": transitions_matrix,
        "sample_dt": sample_dt,
        "categories": cats,
    }


def build_visits(fixations: pd.DataFrame, split_at_gaps: bool) -> pd.DataFrame:
    """
    同一AOIに割り当てられた連続注視を1訪問にまとめる。
    区間（segment_id）をまたがない。split_at_gaps=True ならチャンク（欠測ギャップ）もまたがない。
    """
    cols = ["visit_id", "segment_id", "chunk_id", "aoi", "start_sync_sec", "end_sync_sec",
            "n_fixations", "total_fixation_duration_sec"]
    if fixations.empty:
        return pd.DataFrame(columns=cols)
    f = fixations.sort_values("start_sync_sec", kind="stable").reset_index(drop=True)
    group_keys = ["segment_id", "chunk_id"] if split_at_gaps else ["segment_id"]
    new_visit = (f["aoi"] != f["aoi"].shift())
    for k in group_keys:
        new_visit |= (f[k] != f[k].shift())
    f["visit_id"] = new_visit.cumsum() - 1
    visits = f.groupby("visit_id", sort=True).agg(
        segment_id=("segment_id", "first"),
        chunk_id=("chunk_id", "first"),
        aoi=("aoi", "first"),
        start_sync_sec=("start_sync_sec", "min"),
        end_sync_sec=("end_sync_sec", "max"),
        n_fixations=("fixation_id", "size"),
        total_fixation_duration_sec=("duration_sec", "sum"),
    ).reset_index()
    return visits[cols]


def build_transitions(visits: pd.DataFrame, cats: list[str], split_at_gaps: bool):
    """
    連続する2訪問の AOI 間遷移を数える（注視ベース）。区間をまたがない。
    split_at_gaps=True なら欠測ギャップもまたがない。全ペアを 0 埋めで網羅する。
    """
    pairs = pd.DataFrame(
        [(a, b) for a in cats for b in cats if a != b], columns=["from_aoi", "to_aoi"],
    )
    if len(visits) >= 2:
        v = visits.sort_values("start_sync_sec", kind="stable").reset_index(drop=True)
        same = v["segment_id"].iloc[1:].to_numpy() == v["segment_id"].iloc[:-1].to_numpy()
        if split_at_gaps:
            same &= v["chunk_id"].iloc[1:].to_numpy() == v["chunk_id"].iloc[:-1].to_numpy()
        tr = pd.DataFrame({
            "from_aoi": v["aoi"].iloc[:-1].to_numpy()[same],
            "to_aoi": v["aoi"].iloc[1:].to_numpy()[same],
        })
        counts = tr.groupby(["from_aoi", "to_aoi"]).size().rename("count").reset_index()
    else:
        counts = pd.DataFrame(columns=["from_aoi", "to_aoi", "count"])
    long = pairs.merge(counts, on=["from_aoi", "to_aoi"], how="left")
    long["count"] = long["count"].fillna(0).astype(int)
    matrix = (
        long.pivot(index="from_aoi", columns="to_aoi", values="count")
        .reindex(index=cats, columns=cats).fillna(0).astype(int)
    )
    return long, matrix


def metrics_by_segment(samples: pd.DataFrame, fixations: pd.DataFrame, visits: pd.DataFrame,
                       segments: pd.DataFrame, cats: list[str], params: dict, sample_dt: float) -> pd.DataFrame:
    """区間 × 全カテゴリの指標表（0件のカテゴリも必ず行を出す）。"""
    rows = []
    include_off = params["offscreen_in_denominator"]
    for seg in segments.itertuples(index=False):
        sid = seg.segment_id
        s = samples[samples["segment_id"] == sid]
        n_total = len(s)
        n_valid = int(s["valid"].sum())
        n_offscreen = int(s["offscreen"].sum())
        denom = n_valid if include_off else n_valid - n_offscreen
        valid_time_sec = n_valid * sample_dt
        f = fixations[fixations["segment_id"] == sid]
        v = visits[visits["segment_id"] == sid]
        sample_counts = s["sample_aoi"].value_counts()
        for cat in cats:
            n_in = int(sample_counts.get(cat, 0))
            fc = f[f["aoi"] == cat]
            n_fix = len(fc)
            n_visit = int((v["aoi"] == cat).sum())
            if cat == OFFSCREEN_LABEL and not include_off:
                gaze_pct = np.nan
            else:
                gaze_pct = 100.0 * n_in / denom if denom > 0 else np.nan
            ttff = float(fc["start_sync_sec"].min() - seg.gaze_start_sec) if n_fix else np.nan
            rows.append({
                "segment_id": sid,
                "round": seg.round,
                "segment_type": seg.segment_type,
                "side": seg.side,
                "end_reason": seg.end_reason,
                "segment_start_sync_sec": seg.gaze_start_sec,
                "segment_end_sync_sec": seg.gaze_end_sec,
                "segment_duration_sec": seg.gaze_end_sec - seg.gaze_start_sec,
                "n_samples_total": n_total,
                "n_samples_valid": n_valid,
                "n_samples_offscreen": n_offscreen,
                "gaze_pct_denominator_samples": denom,
                "valid_time_sec": valid_time_sec,
                "aoi": cat,
                "n_samples_in_aoi": n_in,
                "gaze_pct": gaze_pct,
                "fixation_count": n_fix,
                "fixations_per_min": n_fix / (valid_time_sec / 60.0) if valid_time_sec > 0 else np.nan,
                "total_fixation_duration_sec": float(fc["duration_sec"].sum()),
                "mean_fixation_duration_sec": float(fc["duration_sec"].mean()) if n_fix else np.nan,
                "visit_count": n_visit,
                "revisit_count": max(n_visit - 1, 0),
                "ttff_sec": ttff,
                "ttff_censored": n_fix == 0,
            })
    return pd.DataFrame(rows)


def metrics_session(by_segment: pd.DataFrame, cats: list[str]) -> pd.DataFrame:
    """
    セッション全体の指標。割合の平均ではなく、サンプル数・件数・時間を合算してから比率を出す
    （区間の長さの違いで重みが歪まないように）。TTFF は区間ごとの値の平均と打ち切り数を併記する。
    """
    rows = []
    seg_info = by_segment.drop_duplicates("segment_id")
    denom = int(seg_info["gaze_pct_denominator_samples"].sum())
    valid_time = float(seg_info["valid_time_sec"].sum())
    for cat in cats:
        c = by_segment[by_segment["aoi"] == cat]
        n_in = int(c["n_samples_in_aoi"].sum())
        n_fix = int(c["fixation_count"].sum())
        total_dur = float(c["total_fixation_duration_sec"].sum())
        pct_defined = c["gaze_pct"].notna().any()
        rows.append({
            "aoi": cat,
            "n_segments": int(len(c)),
            "analysis_time_sec": float(c["segment_duration_sec"].sum()),
            "valid_time_sec": valid_time,
            "gaze_pct_denominator_samples": denom,
            "n_samples_in_aoi": n_in,
            "gaze_pct": (100.0 * n_in / denom) if (denom > 0 and pct_defined) else np.nan,
            "fixation_count": n_fix,
            "fixations_per_min": n_fix / (valid_time / 60.0) if valid_time > 0 else np.nan,
            "total_fixation_duration_sec": total_dur,
            "mean_fixation_duration_sec": total_dur / n_fix if n_fix else np.nan,
            "visit_count": int(c["visit_count"].sum()),
            "revisit_count": int(c["revisit_count"].sum()),
            "ttff_sec_mean_uncensored": float(c["ttff_sec"].mean()) if c["ttff_sec"].notna().any() else np.nan,
            "ttff_censored_segments": int(c["ttff_censored"].sum()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# QC 用の補助
# ---------------------------------------------------------------------------

def time_base_comparison(df: pd.DataFrame) -> dict:
    """
    pc_time_sec（ホスト到着時刻）と system_time_stamp_us（SDKのシステム時刻）の
    サンプル間隔のばらつきを比べる（決定事項4の判断材料）。
    """
    out = {"available": False}
    if "system_time_stamp_us" not in df.columns:
        return out
    pc = pd.to_numeric(df["pc_time_sec"], errors="coerce").to_numpy(dtype=float)
    st = pd.to_numeric(df["system_time_stamp_us"], errors="coerce").to_numpy(dtype=float) / 1e6
    ok = ~np.isnan(pc) & ~np.isnan(st)
    if ok.sum() < 3:
        return out
    dpc = np.diff(pc[ok])
    dst = np.diff(st[ok])
    resid = dpc - dst
    return {
        "available": True,
        "n_intervals": int(len(resid)),
        "pc_interval_mean_ms": float(dpc.mean() * 1000),
        "pc_interval_sd_ms": float(dpc.std(ddof=1) * 1000),
        "system_interval_mean_ms": float(dst.mean() * 1000),
        "system_interval_sd_ms": float(dst.std(ddof=1) * 1000),
        "interval_difference_sd_ms": float(resid.std(ddof=1) * 1000),
        "interval_difference_abs_p99_ms": float(np.quantile(np.abs(resid), 0.99) * 1000),
        "note": "interval_difference = Δpc_time − Δsystem_time。SDが大きいほど pc_time_sec の"
                "到着時刻ゆらぎが注視時間に混入している。",
    }


def eye_distance_summary(df: pd.DataFrame) -> dict:
    """T1 で追加した gaze_origin の z [mm]（眼−トラッカー距離）の要約。列が無ければ available=False。"""
    cols = ["left_gaze_origin_z_mm", "right_gaze_origin_z_mm"]
    if not all(c in df.columns for c in cols):
        return {"available": False}
    vals = []
    for side in ("left", "right"):
        z = pd.to_numeric(df[f"{side}_gaze_origin_z_mm"], errors="coerce")
        vcol = f"{side}_gaze_origin_validity"
        if vcol in df.columns:
            z = z[pd.to_numeric(df[vcol], errors="coerce").eq(1)]
        vals.append(z.dropna().to_numpy())
    z = np.concatenate(vals)
    if len(z) == 0:
        return {"available": False}
    return {
        "available": True,
        "median_mm": float(np.median(z)),
        "p05_mm": float(np.quantile(z, 0.05)),
        "p95_mm": float(np.quantile(z, 0.95)),
        "n": int(len(z)),
        "note": "user coordinate system の z（眼−トラッカー距離）。眼−画面中央距離とは厳密には一致しない。",
    }


def offscreen_summary(samples: pd.DataFrame) -> dict:
    """区間内の画面外サンプルが、どの辺の外にあるかの内訳。"""
    s = samples[(samples["segment_id"].notna()) & samples["offscreen"]]
    counts = s["offscreen_edge"].value_counts()
    n_valid = int(samples.loc[samples["segment_id"].notna(), "valid"].sum())
    out = {"n_offscreen": int(len(s)), "rate_of_valid": (len(s) / n_valid) if n_valid else math.nan}
    for edge in ("top", "bottom", "left", "right"):
        out[f"n_{edge}"] = int(counts.get(edge, 0))
    return out
