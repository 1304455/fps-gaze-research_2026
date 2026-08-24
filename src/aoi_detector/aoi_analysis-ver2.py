import argparse
import json
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "pc_time_sec", "center_x", "center_y",
    "left_gaze_point_validity", "right_gaze_point_validity", "gaze_missing",
]

def load_aoi_config(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    aois = data.get("aois")
    if not isinstance(aois, list):
        raise ValueError("AOI設定JSONには aois の配列が必要です。")
    return aois

def validate_columns(df):
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"CSVに必要な列がありません: {missing}")

def is_valid_row(row):
    if str(row.get("gaze_missing", "")).lower() == "true":
        return False
    left_ok = row.get("left_gaze_point_validity", 0) == 1
    right_ok = row.get("right_gaze_point_validity", 0) == 1
    return (left_ok or right_ok) and pd.notna(row["center_x"]) and pd.notna(row["center_y"])

def point_in_aoi(x, y, aoi):
    shape = aoi.get("type", "rectangle")
    if shape == "rectangle":
        return (aoi["x_min"] <= x < aoi["x_max"] and
                aoi["y_min"] <= y < aoi["y_max"])
    if shape == "circle":
        dx = (x - aoi["center_x"]) / aoi["radius_x"]
        dy = (y - aoi["center_y"]) / aoi["radius_y"]
        return dx * dx + dy * dy <= 1
    raise ValueError(f"未対応のAOI形状です: {shape}")

def assign_aoi(x, y, aois):
    for aoi in aois:
        if point_in_aoi(x, y, aoi):
            return aoi["name"]
    return "outside"

def estimate_sample_dt(df_valid):
    if len(df_valid) < 2:
        return 0.0
    dt = df_valid["pc_time_sec"].diff().dropna()
    dt = dt[(dt > 0) & (dt < dt.quantile(0.99))]
    return float(dt.median()) if not dt.empty else 0.0

def compute_fixation_metrics(sequence: pd.DataFrame, sample_dt: float) -> pd.DataFrame:
    """
    sequence: columns = ["pc_time_sec", "center_x", "center_y", "aoi"]
    sample_dt: 推定サンプル間隔（秒）
    戻り値: 各AOIの Fixation 指標をまとめた DataFrame
    """
    if sample_dt <= 0:
        raise ValueError("sample_dt が 0 以下のため、注視時間を推定できません。")

    # 連続する AOI ブロック（run）を抽出
    # run_id: AOIが変わるたびに +1 される
    sequence = sequence.copy()
    changed = sequence["aoi"] != sequence["aoi"].shift()
    sequence["run_id"] = changed.cumsum()

    # 各 run ごとに注視情報を集計
    runs = sequence.groupby(["run_id", "aoi"]).agg(
        start_time=("pc_time_sec", "min"),
        end_time=("pc_time_sec", "max"),
        samples=("pc_time_sec", "size"),
    ).reset_index()

    # duration をサンプル数 × sample_dt で推定
    runs["duration_sec"] = runs["samples"] * sample_dt

    # outside AOI を含めるかどうかは用途次第
    # HUD評価などの主要AOIのみ使いたければフィルタする
    # ここでは一旦全部残し、あとで必要なら outside を除外
    fixation_all = runs

    # 各AOIごとの指標計算
    # Fixation Count = run数
    # Average Fixation Duration = duration_sec の平均
    # TTFF = 最初にそのAOIが出現した start_time - 全体の開始時間
    # Revisit Count = Fixation Count - 1
    # Gaze % = AOIの総注視時間 / 全AOIの総注視時間 × 100
    overall_start = sequence["pc_time_sec"].min()
    total_dwell = fixation_all["duration_sec"].sum()

    aoi_stats = fixation_all.groupby("aoi").agg(
        fixation_count=("duration_sec", "size"),  # run数
        total_fixation_duration_sec=("duration_sec", "sum"),
        average_fixation_duration_sec=("duration_sec", "mean"),
        first_fixation_time=("start_time", "min"),
    ).reset_index()

    # TTFF = first_fixation_time - overall_start
    aoi_stats["ttff_sec"] = aoi_stats["first_fixation_time"] - overall_start

    # Revisit Count = fixation_count - 1 （1回目を初回訪問とみなす）
    aoi_stats["revisit_count"] = aoi_stats["fixation_count"] - 1

    # Gaze %（主要指標）
    aoi_stats["gaze_percent"] = (aoi_stats["total_fixation_duration_sec"] / total_dwell) * 100.0

    return aoi_stats

def main():
    parser = argparse.ArgumentParser(description="Tobii視線CSVを矩形・円形AOIで解析します。")
    parser.add_argument("--input", required=True, help="入力CSVファイル")
    parser.add_argument("--aoi", required=True, help="AOI設定JSONファイル")
    parser.add_argument("--output_dir", default="data/processed/aoi_result", help="出力フォルダ")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # CSV読み込み・AOI付与（既存ロジック）
    df = pd.read_csv(args.input)
    validate_columns(df)
    aois = load_aoi_config(args.aoi)

    df["valid_gaze"] = df.apply(is_valid_row, axis=1)
    df_valid = df[df["valid_gaze"]].copy()
    if df_valid.empty:
        raise ValueError("有効な視線データがありません。")

    df_valid["aoi"] = df_valid.apply(
        lambda row: assign_aoi(row["center_x"], row["center_y"], aois), axis=1
    )

    sample_dt = estimate_sample_dt(df_valid)

    # 既存のサマリ（サンプル数ベース）
    summary = df_valid["aoi"].value_counts().rename_axis("aoi").reset_index(name="samples")
    summary["ratio"] = summary["samples"] / summary["samples"].sum()
    summary["estimated_dwell_sec"] = summary["samples"] * sample_dt
    summary.to_csv(out_dir / "aoi_summary.csv", index=False, encoding="utf-8-sig")

    # 時系列シーケンス
    sequence = df_valid[["pc_time_sec", "center_x", "center_y", "aoi"]].copy()
    sequence.to_csv(out_dir / "aoi_sequence.csv", index=False, encoding="utf-8-sig")

    # 遷移行列
    changed = sequence["aoi"] != sequence["aoi"].shift()
    transitions = pd.DataFrame({
        "from_aoi": sequence["aoi"].shift()[changed],
        "to_aoi": sequence["aoi"][changed],
    }).dropna()
    transition_summary = transitions.value_counts().reset_index(name="count")
    transition_summary.to_csv(out_dir / "aoi_transitions.csv", index=False, encoding="utf-8-sig")

    # データセット概要
    overview = pd.DataFrame([
        {"item": "all_rows", "value": len(df)},
        {"item": "valid_rows", "value": len(df_valid)},
        {"item": "valid_ratio", "value": len(df_valid) / len(df)},
        {"item": "estimated_sample_interval_sec", "value": sample_dt},
        {"item": "estimated_sampling_rate_hz", "value": 1 / sample_dt if sample_dt else 0},
    ])
    overview.to_csv(out_dir / "dataset_overview.csv", index=False, encoding="utf-8-sig")

    # ★ 追加: 視線指標（Fixation, TTFF, Revisit, Gaze %）の算出
    fixation_metrics = compute_fixation_metrics(sequence, sample_dt)

    # outside を除外したい場合はここでフィルタ
    # fixation_metrics = fixation_metrics[fixation_metrics["aoi"] != "outside"]

    fixation_metrics.to_csv(
        out_dir / "aoi_fixation_metrics.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print(f"完了: {out_dir}")

if __name__ == "__main__":
    main()