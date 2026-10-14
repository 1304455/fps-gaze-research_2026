"""
AOI (Area of Interest) 解析スクリプト ver.3

ver.2からの主な変更点
---------------------
1. [設計変更] dataset_overview を「フラットなitem/value CSV」から
   「出所情報・AOIカバレッジ情報付きのJSON」に刷新。
   --append_master_csv を指定すると、セッション1件を横持ち1行として
   マスターテーブル（ランク群間比較用）に追記できる。
   旧形式のdataset_overview.csvは後方互換のため当面併存させる。

2. [バグ修正] aoi_summary / aoi_fixation_metrics / aoi_transitions が
   「そのセッションで実際に観測されたAOIのみ」を出力しており、
   設定JSON上は定義されているのに0件だったAOI（例: round_timer,
   enemy_team_status, ammo_weapon, credits）が結果から丸ごと消えていた。
   → 設定JSONに定義された全AOI + outside を必ず1行として出力し、
     未観測AOIは fixation_count=0 / count=0 と明示する。
     （「欠測」と「真の0件」を混同すると、ランク群間でのmerge/concat時に
       破綻するため。これは今回最も優先度の高い修正。）

3. [追加] 遷移テーブルを「観測ペアのみのスパースなロング形式」から
   「全AOIペアを網羅したロング形式（統計処理向け） + 正方行列（人間可読・
   遷移エントロピー計算向け）」の二本立てに変更。

4. [追加] AOI設定の座標系サニティチェック。coordinate_system=normalized
   のはずなのに極端な値（ピクセル座標混入など）があれば警告する。

5. [性能] is_valid_row / assign_aoi の行単位 apply をベクトル化し、
   大規模セッションでの実行時間を短縮。ロジックはver.2と等価。

6. [追加] 実行のたびに「設定AOI数 vs 今回0件だったAOI」をコンソールに
   明示し、設定ミスと純粋な行動的所見（本当に見ていない）を
   切り分けやすくした。
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "pc_time_sec", "center_x", "center_y",
    "left_gaze_point_validity", "right_gaze_point_validity", "gaze_missing",
]

OUTSIDE_LABEL = "outside"
# normalized座標系で許容する緩めの範囲（トラッキングノイズによる画面外はみ出しを許容）
NORMALIZED_SANITY_RANGE = (-1.5, 2.5)


# ---------------------------------------------------------------------------
# AOI設定の読み込み・検証
# ---------------------------------------------------------------------------

def load_aoi_config(path):
    """AOI設定JSONを読み込み、(aois, raw_config) を返す。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    aois = data.get("aois")
    if not isinstance(aois, list) or not aois:
        raise ValueError("AOI設定JSONには aois の配列が必要です。")

    names = [aoi.get("name") for aoi in aois]
    if any(n is None for n in names):
        raise ValueError("AOI設定に name が欠けている要素があります。")
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"AOI設定に重複した name があります: {dup}")

    _sanity_check_coordinate_system(aois, data.get("coordinate_system"))
    return aois, data


def _sanity_check_coordinate_system(aois, coordinate_system):
    """
    coordinate_system=normalized のはずなのにピクセル座標などが
    混入していないかを緩く検査する（ハードエラーにはしない）。
    """
    if coordinate_system not in (None, "normalized"):
        return  # 明示的に別の座標系が宣言されている場合はチェックしない

    lo, hi = NORMALIZED_SANITY_RANGE
    suspicious = []
    for aoi in aois:
        shape = aoi.get("type", "rectangle")
        if shape == "rectangle":
            values = [aoi.get("x_min"), aoi.get("x_max"), aoi.get("y_min"), aoi.get("y_max")]
        elif shape == "circle":
            values = [aoi.get("center_x"), aoi.get("center_y"), aoi.get("radius_x"), aoi.get("radius_y")]
        else:
            continue
        if any(v is not None and not (lo <= v <= hi) for v in values):
            suspicious.append(aoi.get("name"))
    if suspicious:
        print(
            "⚠ 警告: coordinate_system=normalized ですが、以下のAOIに "
            f"[{lo}, {hi}] を外れる座標値があります（ピクセル座標混入の可能性）: {suspicious}"
        )


def all_aoi_categories(aois):
    """設定順を保ったまま、AOI名 + outside の全カテゴリ一覧を返す。"""
    return [aoi["name"] for aoi in aois] + [OUTSIDE_LABEL]


def validate_columns(df):
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"CSVに必要な列がありません: {missing}")


# ---------------------------------------------------------------------------
# 有効行判定・AOI割当（ベクトル化）
# ---------------------------------------------------------------------------

def compute_valid_mask(df: pd.DataFrame) -> pd.Series:
    """
    行単位apply を使わないベクトル化版の有効判定。
    ロジックはver.2の is_valid_row と等価（下の point_in_aoi 同様、
    スカラー版が必要な場面のために is_valid_row も残してある）。
    """
    gaze_missing = df["gaze_missing"].astype(str).str.lower().eq("true")
    left_ok = pd.to_numeric(df["left_gaze_point_validity"], errors="coerce").eq(1)
    right_ok = pd.to_numeric(df["right_gaze_point_validity"], errors="coerce").eq(1)
    has_xy = df["center_x"].notna() & df["center_y"].notna()
    return (~gaze_missing) & (left_ok | right_ok) & has_xy


def is_valid_row(row):
    """ver.2互換のスカラー版（デバッグ・単発チェック用）。"""
    if str(row.get("gaze_missing", "")).lower() == "true":
        return False
    left_ok = row.get("left_gaze_point_validity", 0) == 1
    right_ok = row.get("right_gaze_point_validity", 0) == 1
    return (left_ok or right_ok) and pd.notna(row["center_x"]) and pd.notna(row["center_y"])


def point_in_aoi(x: float, y: float, aoi: dict) -> bool:
    """単一の点がAOI内にあるかを判定する（デバッグ・単体テスト用のスカラー版）。"""
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
    """ver.2互換のスカラー版（デバッグ・単発チェック用）。"""
    for aoi in aois:
        if point_in_aoi(x, y, aoi):
            return aoi["name"]
    return OUTSIDE_LABEL


def assign_aoi_vectorized(x: pd.Series, y: pd.Series, aois: list) -> pd.Series:
    """
    全行分を一括判定するベクトル化版。判定順は設定JSONの順序（先勝ち）を
    維持し、assign_aoi（スカラー版）と同じ結果になる。
    大規模セッション（数十万行）での実行時間を短縮する。
    """
    result = pd.Series(OUTSIDE_LABEL, index=x.index, dtype=object)
    unassigned = pd.Series(True, index=x.index)
    for aoi in aois:
        shape = aoi.get("type", "rectangle")
        if shape == "rectangle":
            mask = (
                unassigned
                & (x >= aoi["x_min"]) & (x < aoi["x_max"])
                & (y >= aoi["y_min"]) & (y < aoi["y_max"])
            )
        elif shape == "circle":
            dx = (x - aoi["center_x"]) / aoi["radius_x"]
            dy = (y - aoi["center_y"]) / aoi["radius_y"]
            mask = unassigned & ((dx * dx + dy * dy) <= 1)
        else:
            raise ValueError(f"未対応のAOI形状です: {shape}")
        result[mask] = aoi["name"]
        unassigned &= ~mask
    return result


def estimate_sample_dt(df_valid: pd.DataFrame) -> float:
    if len(df_valid) < 2:
        return 0.0
    dt = df_valid["pc_time_sec"].diff().dropna()
    dt = dt[(dt > 0) & (dt < dt.quantile(0.99))]
    return float(dt.median()) if not dt.empty else 0.0


# ---------------------------------------------------------------------------
# 指標計算
# ---------------------------------------------------------------------------

def compute_fixation_metrics(sequence: pd.DataFrame, sample_dt: float, all_categories: list) -> pd.DataFrame:
    """
    sequence: columns = ["pc_time_sec", "center_x", "center_y", "aoi"]
    sample_dt: 推定サンプル間隔（秒）
    all_categories: 設定JSON由来の全AOI名 + outside
    戻り値: 各AOIのFixation指標をまとめたDataFrame。
            0件のAOIも必ず1行として含む（fixation_count=0、
            average_fixation_duration_sec/ttff_secはNaNのまま＝「未発生」を明示）。
    """
    if sample_dt <= 0:
        raise ValueError("sample_dt が 0 以下のため、注視時間を推定できません。")

    sequence = sequence.copy()
    changed = sequence["aoi"] != sequence["aoi"].shift()
    sequence["run_id"] = changed.cumsum()

    runs = sequence.groupby(["run_id", "aoi"], observed=True).agg(
        start_time=("pc_time_sec", "min"),
        end_time=("pc_time_sec", "max"),
        samples=("pc_time_sec", "size"),
    ).reset_index()
    runs["duration_sec"] = runs["samples"] * sample_dt

    overall_start = sequence["pc_time_sec"].min()
    total_dwell = runs["duration_sec"].sum()

    aoi_stats = runs.groupby("aoi", observed=True).agg(
        fixation_count=("duration_sec", "size"),
        total_fixation_duration_sec=("duration_sec", "sum"),
        average_fixation_duration_sec=("duration_sec", "mean"),
        first_fixation_time=("start_time", "min"),
    )

    # ★ ver.2の問題点の修正: 観測されたAOIだけでなく設定済み全AOI（+outside）を
    #   必ず行として保持する。こうしないと「0件」なのか「集計から消えた」のか
    #   区別できず、群間比較でmerge/concatした際に欠測扱いになってしまう。
    aoi_stats = aoi_stats.reindex(all_categories)

    aoi_stats["fixation_count"] = aoi_stats["fixation_count"].fillna(0).astype(int)
    aoi_stats["total_fixation_duration_sec"] = aoi_stats["total_fixation_duration_sec"].fillna(0.0)
    # average_fixation_duration_sec / first_fixation_time / ttff_sec は
    # 「そもそも1回も注視していない」ことを示すため、意図的にNaNのまま残す（0で埋めない）。

    aoi_stats["ttff_sec"] = aoi_stats["first_fixation_time"] - overall_start
    aoi_stats["revisit_count"] = (aoi_stats["fixation_count"] - 1).clip(lower=0)

    if total_dwell > 0:
        aoi_stats["gaze_percent"] = (aoi_stats["total_fixation_duration_sec"] / total_dwell) * 100.0
    else:
        aoi_stats["gaze_percent"] = 0.0

    aoi_stats["observed"] = aoi_stats["fixation_count"] > 0

    aoi_stats = aoi_stats.reset_index()  # index名"aoi"がそのまま列名になる
    return aoi_stats


def compute_transition_tables(sequence: pd.DataFrame, all_categories: list):
    """
    全AOIペア（自己遷移を除く）を網羅したロング形式テーブルと、
    それをピボットした正方行列（from行 × to列）の2つを返す。
    観測されなかったペアは count=0 として明示する。
    """
    changed = sequence["aoi"] != sequence["aoi"].shift()
    transitions = pd.DataFrame({
        "from_aoi": sequence["aoi"].shift()[changed],
        "to_aoi": sequence["aoi"][changed],
    }).dropna()

    observed_counts = transitions.value_counts().rename("count").reset_index()

    all_pairs = pd.DataFrame(
        [(f, t) for f in all_categories for t in all_categories if f != t],
        columns=["from_aoi", "to_aoi"],
    )
    transition_long = all_pairs.merge(observed_counts, on=["from_aoi", "to_aoi"], how="left")
    transition_long["count"] = transition_long["count"].fillna(0).astype(int)

    transition_matrix = (
        transition_long.pivot(index="from_aoi", columns="to_aoi", values="count")
        .reindex(index=all_categories, columns=all_categories)
        .fillna(0)
        .astype(int)
    )

    return transition_long, transition_matrix


def build_dataset_overview(*, args, all_rows, valid_rows, sample_dt, overall_start, overall_end,
                            aois, raw_config, all_categories, observed_categories):
    zero_fixation_aois = sorted(set(all_categories) - set(observed_categories))
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(Path(args.input).resolve()),
        "aoi_config_path": str(Path(args.aoi).resolve()),
        "aoi_config_screen_name": raw_config.get("screen_name"),
        "aoi_config_coordinate_system": raw_config.get("coordinate_system"),
        "aoi_config_reference_resolution": raw_config.get("reference_resolution"),
        "participant_id": args.participant_id,
        "rank_tier": args.rank_tier,
        "session_id": args.session_id,
        "game_id": args.game_id,
        "all_rows": int(all_rows),
        "valid_rows": int(valid_rows),
        "valid_ratio": (valid_rows / all_rows) if all_rows else 0.0,
        "estimated_sample_interval_sec": sample_dt,
        "estimated_sampling_rate_hz": (1 / sample_dt) if sample_dt else 0.0,
        "task_duration_sec": float(overall_end - overall_start),
        "configured_aoi_count": len(aois),
        "configured_aoi_names": [aoi["name"] for aoi in aois],
        "observed_aoi_count": len(observed_categories),
        "zero_fixation_aois": zero_fixation_aois,
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tobii視線CSVを矩形・円形AOIで解析します。")
    parser.add_argument("--input", required=True, help="入力CSVファイル")
    parser.add_argument("--aoi", required=True, help="AOI設定JSONファイル")
    parser.add_argument("--output_dir", default="data/processed/aoi_result", help="出力フォルダ")
    parser.add_argument("--file_prefix", default="", help="出力ファイル名に付与する接頭辞（例: game01_）")
    parser.add_argument("--participant_id", default=None, help="参加者ID（マスターテーブル集計用、任意）")
    parser.add_argument("--rank_tier", default=None, help="ランク帯（マスターテーブル集計用、任意）")
    parser.add_argument("--session_id", default=None, help="セッションID（任意）")
    parser.add_argument("--game_id", default=None, help="ゲームID（任意。未指定なら入力ファイル名から推定）")
    parser.add_argument(
        "--append_master_csv", default=None,
        help="指定した場合、このセッションの概要を横持ち1行として追記する（ランク群間比較用マスターテーブル）",
    )
    args = parser.parse_args()

    if args.game_id is None:
        args.game_id = Path(args.input).stem

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.file_prefix

    def out_path(name: str) -> Path:
        return out_dir / f"{prefix}{name}"

    # --- 読み込み・検証・AOI付与 ---
    df = pd.read_csv(args.input)
    validate_columns(df)
    aois, raw_config = load_aoi_config(args.aoi)
    all_categories = all_aoi_categories(aois)

    df["valid_gaze"] = compute_valid_mask(df)
    df_valid = df[df["valid_gaze"]].copy()
    if df_valid.empty:
        raise ValueError("有効な視線データがありません。")

    df_valid["aoi"] = assign_aoi_vectorized(df_valid["center_x"], df_valid["center_y"], aois)

    sample_dt = estimate_sample_dt(df_valid)

    # --- aoi_summary（サンプル数ベース。全AOI + outsideを必ず含める） ---
    summary = (
        df_valid["aoi"].value_counts()
        .reindex(all_categories, fill_value=0)
        .rename_axis("aoi")
        .reset_index(name="samples")
    )
    total_samples = summary["samples"].sum()
    summary["ratio"] = summary["samples"] / total_samples if total_samples else 0.0
    summary["estimated_dwell_sec"] = summary["samples"] * sample_dt
    summary.to_csv(out_path("aoi_summary.csv"), index=False, encoding="utf-8-sig")

    # --- 時系列シーケンス（ver.2と同じ） ---
    sequence = df_valid[["pc_time_sec", "center_x", "center_y", "aoi"]].copy()
    sequence.to_csv(out_path("aoi_sequence.csv"), index=False, encoding="utf-8-sig")

    # --- 遷移テーブル（ロング形式 + 正方行列。両方とも全AOIペアを0埋めで網羅） ---
    transition_long, transition_matrix = compute_transition_tables(sequence, all_categories)
    transition_long.to_csv(out_path("aoi_transitions.csv"), index=False, encoding="utf-8-sig")
    transition_matrix.to_csv(out_path("aoi_transitions_matrix.csv"), encoding="utf-8-sig")

    # --- Fixation指標（全AOI + outsideを必ず含める） ---
    fixation_metrics = compute_fixation_metrics(sequence, sample_dt, all_categories)
    fixation_metrics.to_csv(out_path("aoi_fixation_metrics.csv"), index=False, encoding="utf-8-sig")

    # --- データセット概要（JSONが正、CSVは後方互換のため併存） ---
    observed_categories = sorted(df_valid["aoi"].unique().tolist())
    overview = build_dataset_overview(
        args=args,
        all_rows=len(df),
        valid_rows=len(df_valid),
        sample_dt=sample_dt,
        overall_start=sequence["pc_time_sec"].min(),
        overall_end=sequence["pc_time_sec"].max(),
        aois=aois,
        raw_config=raw_config,
        all_categories=all_categories,
        observed_categories=observed_categories,
    )
    with open(out_path("dataset_overview.json"), "w", encoding="utf-8") as f:
        json.dump(overview, f, ensure_ascii=False, indent=2)

    # 旧形式（フラットなitem/value CSV）は後方互換のため当面残す。
    # 他スクリプトからの依存がないと確認できれば、次イテレーションで削除してよい。
    overview_csv = pd.DataFrame([
        {"item": "all_rows", "value": overview["all_rows"]},
        {"item": "valid_rows", "value": overview["valid_rows"]},
        {"item": "valid_ratio", "value": overview["valid_ratio"]},
        {"item": "estimated_sample_interval_sec", "value": overview["estimated_sample_interval_sec"]},
        {"item": "estimated_sampling_rate_hz", "value": overview["estimated_sampling_rate_hz"]},
        {"item": "configured_aoi_count", "value": overview["configured_aoi_count"]},
        {"item": "observed_aoi_count", "value": overview["observed_aoi_count"]},
    ])
    overview_csv.to_csv(out_path("dataset_overview.csv"), index=False, encoding="utf-8-sig")

    # --- マスターテーブルへの追記（任意。ランク群間比較のための横持ち1行） ---
    if args.append_master_csv:
        master_row = {
            "game_id": args.game_id,
            "participant_id": args.participant_id,
            "rank_tier": args.rank_tier,
            "session_id": args.session_id,
            **{
                k: v for k, v in overview.items()
                if k not in (
                    "configured_aoi_names", "zero_fixation_aois",
                    "participant_id", "rank_tier", "session_id", "game_id",
                )
            },
            "zero_fixation_aois": ";".join(overview["zero_fixation_aois"]),
        }
        for _, row in fixation_metrics.iterrows():
            master_row[f"fixation_count__{row['aoi']}"] = row["fixation_count"]
            master_row[f"gaze_percent__{row['aoi']}"] = row["gaze_percent"]

        master_path = Path(args.append_master_csv)
        master_df = pd.DataFrame([master_row])
        if master_path.exists():
            existing = pd.read_csv(master_path)
            combined = pd.concat([existing, master_df], ignore_index=True, sort=False)
        else:
            combined = master_df
        master_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(master_path, index=False, encoding="utf-8-sig")

    # --- 実行時サニティチェック ---
    zero_fixation_aois = overview["zero_fixation_aois"]
    print(f"設定AOI: {len(aois)}件 {overview['configured_aoi_names']}")
    if zero_fixation_aois:
        print(f"⚠ 今回のデータで0件だったAOI（{len(zero_fixation_aois)}件）: {zero_fixation_aois}")
        print("  → 設定JSON自体は正しく読み込まれ、判定にも使われています。0件は「その領域を")
        print("    実際には見なかった」ことを意味しますが、解像度・HUDスケール・キャプチャ範囲の")
        print("    ズレが原因でないか、一度は目視で確認することを推奨します。")
    else:
        print("設定AOIは全て少なくとも1回は観測されました。")

    print(f"完了: {out_dir}")


if __name__ == "__main__":
    main()