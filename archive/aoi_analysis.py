import argparse
import json
from pathlib import Path
import pandas as pd

REQUIRED_COLUMNS = [
    'pc_time_sec',
    'center_x',
    'center_y',
    'left_gaze_point_validity',
    'right_gaze_point_validity',
    'gaze_missing',
]

EXAMPLE_AOI = {
    "screen_name": "example_stimulus",
    "note": "AOI座標は正規化座標です。左上=(0,0), 右下=(1,1) を想定しています。必要に応じて値を調整してください。",
    "aois": [
        {"name": "left_top", "x_min": 0.00, "x_max": 0.50, "y_min": 0.00, "y_max": 0.50},
        {"name": "right_top", "x_min": 0.50, "x_max": 1.00, "y_min": 0.00, "y_max": 0.50},
        {"name": "left_bottom", "x_min": 0.00, "x_max": 0.50, "y_min": 0.50, "y_max": 1.00},
        {"name": "right_bottom", "x_min": 0.50, "x_max": 1.00, "y_min": 0.50, "y_max": 1.00}
    ]
}


def load_aoi_config(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if 'aois' not in data or not isinstance(data['aois'], list):
        raise ValueError('AOI設定JSONには aois の配列が必要です。')
    return data['aois']


def validate_columns(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'CSVに必要な列がありません: {missing}')


def is_valid_row(row):
    if str(row.get('gaze_missing', '')).lower() == 'true':
        return False
    left_ok = row.get('left_gaze_point_validity', 0) == 1
    right_ok = row.get('right_gaze_point_validity', 0) == 1
    center_ok = pd.notna(row.get('center_x')) and pd.notna(row.get('center_y'))
    return (left_ok or right_ok) and center_ok


def assign_aoi(x, y, aois):
    for aoi in aois:
        if aoi['x_min'] <= x < aoi['x_max'] and aoi['y_min'] <= y < aoi['y_max']:
            return aoi['name']
    return 'outside'


def estimate_sample_dt(df_valid: pd.DataFrame):
    if len(df_valid) < 2:
        return 0.0
    dt = df_valid['pc_time_sec'].diff().dropna()
    dt = dt[(dt > 0) & (dt < dt.quantile(0.99))]
    if len(dt) == 0:
        return 0.0
    return float(dt.median())


def main():
    parser = argparse.ArgumentParser(description='Tobii Pro Spark の視線CSVからAOI解析を行います。')
    parser.add_argument('--input', required=True, help='入力CSVファイル')
    parser.add_argument('--aoi', required=True, help='AOI設定JSONファイル')
    parser.add_argument('--output_dir', default='output/aoi_result', help='出力フォルダ')
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input)
    validate_columns(df)
    aois = load_aoi_config(args.aoi)

    df['valid_gaze'] = df.apply(is_valid_row, axis=1)
    df_valid = df[df['valid_gaze']].copy()

    if df_valid.empty:
        raise ValueError('有効な視線データがありません。validity列やgaze_missing列を確認してください。')

    df_valid['aoi'] = df_valid.apply(lambda r: assign_aoi(r['center_x'], r['center_y'], aois), axis=1)

    sample_dt = estimate_sample_dt(df_valid)

    counts = df_valid['aoi'].value_counts().rename_axis('aoi').reset_index(name='samples')
    counts['ratio'] = counts['samples'] / counts['samples'].sum()
    counts['estimated_dwell_sec'] = counts['samples'] * sample_dt
    counts = counts.sort_values('samples', ascending=False)
    counts.to_csv(out_dir / 'aoi_summary.csv', index=False, encoding='utf-8-sig')

    sequence = df_valid[['pc_time_sec', 'center_x', 'center_y', 'aoi']].copy()
    sequence.to_csv(out_dir / 'aoi_sequence.csv', index=False, encoding='utf-8-sig')

    transitions = []
    prev = None
    for aoi in sequence['aoi']:
        if prev is not None and aoi != prev:
            transitions.append((prev, aoi))
        prev = aoi

    if transitions:
        trans_df = pd.DataFrame(transitions, columns=['from_aoi', 'to_aoi'])
        trans_summary = trans_df.value_counts().reset_index(name='count')
    else:
        trans_summary = pd.DataFrame(columns=['from_aoi', 'to_aoi', 'count'])
    trans_summary.to_csv(out_dir / 'aoi_transitions.csv', index=False, encoding='utf-8-sig')

    overview = pd.DataFrame([
        {'item': 'all_rows', 'value': len(df)},
        {'item': 'valid_rows', 'value': len(df_valid)},
        {'item': 'valid_ratio', 'value': len(df_valid) / len(df) if len(df) else 0},
        {'item': 'estimated_sample_interval_sec', 'value': sample_dt},
        {'item': 'estimated_sampling_rate_hz', 'value': (1 / sample_dt) if sample_dt > 0 else 0},
    ])
    overview.to_csv(out_dir / 'dataset_overview.csv', index=False, encoding='utf-8-sig')

    with open(out_dir / 'README.txt', 'w', encoding='utf-8') as f:
        f.write(
            'AOI解析の出力説明\n'
            '1. aoi_summary.csv: AOIごとのサンプル数、割合、推定滞在時間\n'
            '2. aoi_sequence.csv: 各時点の視線座標と所属AOI\n'
            '3. aoi_transitions.csv: AOI間の遷移回数\n'
            '4. dataset_overview.csv: データ全体の件数、有効率、推定サンプリング間隔\n\n'
            '注意:\n'
            '- center_x, center_y はこのCSVでは正規化座標らしく、0〜1付近で扱う想定です。\n'
            '- 画面外では 1 を超える値が出ることがあるため、その場合は outside になります。\n'
            '- 厳密な注視(fixation)解析ではなく、まずはAOI分布を把握するための入門版です。\n'
        )

    print(f'完了: {out_dir}')


if __name__ == '__main__':
    main()
