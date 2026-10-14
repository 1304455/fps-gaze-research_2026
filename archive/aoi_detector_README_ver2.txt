AOI解析の出力説明
1. aoi_summary.csv: AOIごとのサンプル数、割合、推定滞在時間
2. aoi_sequence.csv: 各時点の視線座標と所属AOI
3. aoi_transitions.csv: AOI間の遷移回数
4. dataset_overview.csv: データ全体の件数、有効率、推定サンプリング間隔

注意:
- center_x, center_y はこのCSVでは正規化座標らしく、0〜1付近で扱う想定です。
- 画面外では 1 を超える値が出ることがあるため、その場合は outside になります。
- 厳密な注視(fixation)解析ではなく、まずはAOI分布を把握するための入門版です。
