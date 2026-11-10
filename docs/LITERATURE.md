# 文献台帳（引用の段階）

- 状態：確定版（2026-11-10）
- 基準の commit：`bb0ee74`（ブランチ `claude/nice-brahmagupta-kh2us0`。`docs/GLOSSARY.md` と同じ）
- 置き場所：`docs/LITERATURE.md`（公開リポジトリ）。**入れるのは書誌・ページ・段階・短い引用・注意だけ**。長い抜き書きと PDF は claude.ai の Project に置く
- この台帳に無い文献は、卒論で引用しない。足すときは、書誌を確認してから、まずここに行を足す

## 段階の定義

| 段階 | 条件 | 卒論での扱い |
|---|---|---|
| 1 | Project に PDF がある | 引用できる。該当ページを確認して書く |
| 2 | PDF は無いが、Project のメモに原文の引用とページがある | 引用できる。本文中に【原典未確認】を付ける |
| 3 | 書誌だけ、または要約だけ | 本文中に【要出典・原典確認】を付ける。内容を根拠にした文は書かない |

**柱になる主張**の文献は、提出前に段階1まで上げる。段階2・3のままでは提出しない。書籍は、該当するページの写しを Project に入れ、ページ番号を台帳に書いた時点で段階1とする。

---

## 1. 柱になる主張と、その出典

文献の役割と、自分のデータの役割を分ける。

| 柱 | 文献が担う部分 | 自分のデータが担う部分 | 段階 |
|---|---|---|---|
| 主要比較の区分（AOI1〜6）、新規性の比較対象 | Furukado & Hagiwara (2023) | — | **1** |
| 並べ替え検定の方法 | Edgington & Onghena (2007) | — | 3 |
| 並べ替え検定の p 値の式 p =（k + 1）/（N + 1） | Phipson & Smyth (2010) | — | 3 |
| ρ の信頼区間の標準誤差 | Bonett & Wright (2000) | — | 3 |
| 多重比較の補正 | Holm (1979) | — | 3 |
| 注視の定義（I-DT の方法） | Salvucci & Goldberg (2000) | — | 3 |
| I-DT の閾値（最短 100 ms、分散 1°） | 閾値の範囲：Blignaut (2009) | 予備実験（PL01〜PL03）での妥当性の確認。手順と見直しの目安は `docs/LAB_CHECKLIST.md` 4（結果を見る前に記録）。確認が終わるまで、方法の章の「閾値の妥当性」の段落は【予備実験の確認待ち】 | 3 |
| 注視を分ける欠測 75 ms（主解析の `max_gap_ms`） | Olsen (2012)〔メーカー文書・非査読〕：Tobii I-VT の gap fill-in の既定値 | 参加者ごとの欠測の頻度（`missing_gaps`）と、200 ms の感度分析 | 3 |
| クロスヘアの半径 2.0° | 中心窩の大きさ（約 2°）：Holmqvist et al. (2011) | 正確度の余裕（0.4〜1°）：自分の較正の結果（`docs/LAB_CHECKLIST.md` 2c） | 3 |
| accuracy と precision の定義 | Holmqvist et al. (2011) | — | 3 |

### 柱の文献の書誌

書誌は 2026-11-10 にウェブで確認した。どれも PDF・本文は読んでいない。

| 文献 | 段階を上げるためにやること |
|---|---|
| Furukado, R., & Hagiwara, G. (2023). Gaze and electroencephalography (EEG) parameters in esports: Examinations considering genres and skill levels. *2023 International Workshop on Smart Info-Media Systems in Asia (SISA 2023)*, 107–112. https://doi.org/10.34385/proc.77.SS-2 | 済み（`22_SS-2_28.pdf`）。引用時の注意は §4 |
| Edgington, E. S., & Onghena, P. (2007). *Randomization tests* (4th ed.). Chapman & Hall/CRC. ISBN 978-1-58488-589-4 | 相関の並べ替え検定を扱う章とページを確認し、写しを Project に入れる |
| Phipson, B., & Smyth, G. K. (2010). Permutation P-values should never be zero: Calculating exact P-values when permutations are randomly drawn. *Statistical Applications in Genetics and Molecular Biology, 9*(1), Article 39. https://doi.org/10.2202/1544-6115.1585 | PDF を Project に入れる（著者版は arXiv:1603.05766）。（k + 1）/（N + 1）の式の箇所を確認する |
| Bonett, D. G., & Wright, T. A. (2000). Sample size requirements for estimating Pearson, Kendall and Spearman correlations. *Psychometrika, 65*(1), 23–28. https://doi.org/10.1007/BF02294183 | PDF を Project に入れる。標準誤差 √((1 + ρ²/2)/(n − 3)) が論文のどの式かを確認する |
| Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian Journal of Statistics, 6*(2), 65–70. https://www.jstor.org/stable/4615733 | PDF を Project に入れる |
| Salvucci, D. D., & Goldberg, J. H. (2000). Identifying fixations and saccades in eye-tracking protocols. In *Proceedings of the 2000 Symposium on Eye Tracking Research & Applications (ETRA '00)* (pp. 71–78). ACM. https://doi.org/10.1145/355017.355028 | PDF を Project に入れる。I-DT の定義と、分散の式（x の範囲 ＋ y の範囲）のページを確認する |
| Blignaut, P. (2009). Fixation identification: The optimum threshold for a dispersion algorithm. *Attention, Perception, & Psychophysics, 71*(4), 881–895. https://doi.org/10.3758/APP.71.4.881 | PDF を Project に入れる。**この論文は分散の測り方を4通り比べている**。この研究の測り方（x の範囲 ＋ y の範囲）に当たる測り方での閾値の範囲を確認する。別の測り方の値を引かない |
| Olsen, A. (2012). *The Tobii I-VT fixation filter*. Tobii Technology.〔メーカー文書・非査読〕 | 文書を入手して Project に入れる。正式な題・版・ページ数と、gap fill-in の既定値 75 ms の記載箇所を確認する（題と値は、今回は二次的な記載でしか確認していない） |
| Holmqvist, K., Nyström, M., Andersson, R., Dewhurst, R., Jarodzka, H., & van de Weijer, J. (2011). *Eye tracking: A comprehensive guide to methods and measures*. Oxford University Press. ISBN 978-0-19-969708-3 | 中心窩の大きさ、accuracy・precision の定義のページを確認し、写しを Project に入れる |

### 取り違えに注意する文献

- **Holmqvist et al. (2011) の教科書と、撤回された Holmqvist et al. (2023) は別物**。撤回されたのは "Eye tracking: empirical foundations for a minimal reporting guideline"（*Behavior Research Methods, 55*(1), 364–416）。撤回通知は *Behavior Research Methods, 56*(1), 511–512（2024）、https://doi.org/10.3758/s13428-023-02285-0 。2023 年の論文は引かない。報告の基準を引くなら Dunn et al. (2024)（段階3）。
- **Blignaut (2009) と Blignaut & Beelders (2009) は別の論文**。後者は *Journal of Eye Movement Research, 2*(5) で、台帳に無い。
- **Olsen (2012) と Olsen & Matos (2012, ETRA) は別の文献**。後者は台帳に無い。
- Brunner & Munzel (2000)、Neubert & Brunner (2007) は、主検定が変わったので柱ではない。計画を変えた経緯を書く場合だけ使う（段階3）。

---

## 2. 段階1（PDF あり）：3本

| 文献 | PDF | 使いどころ | 注意 |
|---|---|---|---|
| Furukado & Hagiwara (2023) | `22_SS-2_28.pdf` | §1 | §4 |
| Jeong, I., Nobuto, T., Kaneko, N., Kato, T., & Nakazawa, K. (2023). Investigating the gaze control ability of VALORANT players using a Python-based tool. arXiv:2310.15542 | `2310.15542v1.pdf` | VALORANT の実プレイでの ROI 別の割合。区分の対応（Information 1・2） | **査読前のプレプリント**。本文に [Preprint] と書く。§4 |
| Luo, Y., Chen, Y., Cho, J., Yan, C., & Seo, J. (2025). Differences in eye movement characteristics between expert and non-expert eSports players: A systematic review and meta-analysis. *Scientific Reports, 15*, 30185. https://doi.org/10.1038/s41598-025-12101-8 | `Differences_in_eye_movement_characteristics_betwee.pdf` | eスポーツの視線研究の現状。含まれた7研究に FPS が無いこと | §4 |

## 3. 段階2（メモに原文の引用とページ）：5本

引用とページは Project の `claude/文献抽出_HUD注視研究_2026-10.md` にある。

| 文献 | 使いどころ | 注意 |
|---|---|---|
| Yoon, J.-S., Moon, H.-S., Boudaoud, B., Spjut, J., Frosio, I., Lee, B., & Kim, J. (2025). Modeling visually-guided aim-and-shoot behavior in first-person shooters. | ランクで熟練度を定義した例、無効データの除外の例 | プレプリント。出版版の有無を確認する |
| Holm, S. K. (2023). *Preferences, emotions, and visual attention in the first-person shooter game experience*. University of Turku（博士論文） | FPS の視線研究は各群4〜10名、AOI 解析は今後の課題 | 孫引き（Choi & Kim 2018 など）は原典を見るまで引かない |
| Jin, P., Ge, Z., & Fan, T. (2023). Research on visual search behaviors of basketball players at different levels of sports expertise. *Scientific Reports, 13*, 1406 | 注視の多い・少ないは文献で割れる、AOI を分ける意義 | — |
| Chia, J. S., Burns, S. F., Barrett, L. A., & Chow, J. Y. (2017). Increased complexities in visual search behavior in skilled players for a self-paced aiming task. *Frontiers in Psychology, 8*, 987 | 時間の制約で熟練者の注視が変わる | 補助的（バドミントン） |
| Dahl, M., Tryding, M., Heckler, A., & Nyström, M. (2021). Quiet eye and computerized precision tasks in first-person shooter perspective esport games. *Frontiers in Psychology, 12*, 676591 | 据え置き型の装置の妥当性、注視の検出は方法に依存、較正の採用基準 | — |

## 4. 段階1の3本を引くときの注意（PDF で確認）

**Furukado & Hagiwara (2023)**
- 対象は VALORANT（アンレート）と LoL。FPS は熟練群6名（アセンダント1〜イモータル3）と準熟練群6名（**未ランク〜シルバー2**）（p.108、Table 1）。この研究は未ランク者を除外する。
- 装置は Gazepoint GP3 HD（150 Hz）、**顎台あり**（p.108）。この研究は顎台なし。
- 一部のエージェントを使用禁止にしている（p.108）。この研究は制限していない。
- 生存中のデータだけを使っている（p.109）。
- 検定は対応のない t 検定（p.109）。多重比較の補正についての記載は無い。AOI1 は p = .005、d = −2.05（準熟練群の方が多い）。AOI6 は p = .007。AOI5 は p = .063（p.109、Table 2）。
- **Table 2 の見出しは「%」だが、値は 0.668 のような割合の形**。数値を引くときは単位を確かめる。
- 指標は「1試合あたりの注視時間の割合」で、分母の定義は書かれていない（p.109）。この研究の Gaze % と同じ計算とは言えない。
- AOI1 の差を要素ごと（弾数・HP）に解釈しているが、AOI は分けていない（p.110）。

**Jeong et al. (2023)**
- **要旨と本文で相関の向きが食い違う**。要旨は正の相関、結果は Information 1 と KDA に負の相関（ρ = −0.608、p = .002、p.5、Fig. 7E）。引くのは結果の節。
- ROI の割合と視線分布に、中位群と上位群の有意差は無い（p.6、Fig. 8）。
- 装置は Tobii 4C（90 Hz）、視距離は約 100 cm、録画に焼き込んだ視線の点を画像処理で読み取る方式（p.2–3）。

**Luo et al. (2025)**
- 含まれた7研究のタイトルは Dota 2、Gran Turismo、FIFA 19、StarCraft、LoL、FIFA 21、Assetto Corsa Competizione（p.4、Table 1）。「FPS が含まれない」はこの表から言える。
- 検索は英語の査読論文だけ、2024-07-20 まで（p.2–3）。「eスポーツ全体で研究が無い」とは言えない。
- 注視回数の信頼区間の表記（要旨と p.5 の「−0.55, −0.99」）は、p = 0.58 と合わない。**注視回数の信頼区間は引かない**。
- AOI の結果は統合されておらず、記述だけ（p.6）。

## 5. 段階3（書誌・要約だけ）

**Project の `claude/追加文献調査_理論背景の補強.md` の 28 本（A1〜D6）と予備候補 12 本**
- 要約と使いどころはあるが、原文の引用とページが無い。メモ自身も多くを「一部未確認」「内容要確認」としている。使う文献は、書誌を確認してからこの台帳に1行ずつ移す。
- B2（Furukado & Hagiwara）と B1（Luo et al.）だけは PDF があり、段階1。
- Velichkovsky et al. (2019) を「熟練者ほど注視が短い」と引かない（平均・中央値に群差は無い、とメモにある）。
- このメモは注視検出を I-VT の前提で書いている箇所がある。この研究は I-DT なので、メモの文案をそのまま使わない。

**その他**

| 文献 | 出てくる場所 | 備考 |
|---|---|---|
| Dunn et al. (2024), *Behavior Research Methods, 56*(5), 4351–4357 | 追加文献調査 D1 | 報告の基準。撤回された 2023 年の論文の代わり |
| Brunner & Munzel (2000)、Neubert & Brunner (2007) | 10/22 議事録 | 旧の主検定。経緯を書く場合だけ |
| 芳賀・水上 (1996)、NASA-TLX 日本語版 | 研究経緯 10/26 | 事後アンケートを卒論で扱う場合 |
| Jeong et al. (2022, *PLOS ONE*)、Jeong et al. (2024, *PLOS ONE*)、Holm et al. (2021, *JEMR*)、Cazzato et al. (2020)、Montolio-Vila et al. (2024)、Yang et al. (2025) | 研究ノート | 読書メモだけ。ページつきの引用なし |
| AOI の最小サイズについての引用（PMC12913287） | 研究ノート 8/27 | 著者・誌名・ページが控えられていない |

`中間発表.pptx` の参考文献は確認していない。

## 6. 新規性の主張の範囲

- 「下部HUD（HP・アビリティ・弾数）を分けて熟練度との関係を見た研究は、**手元の文献では**見つかっていない」。調べた範囲はこの台帳の文献だけ。
- 未調査：CS:GO・Apex・Overwatch などの研究、日本語の論文、arXiv:2607.17151（機能別 AOI と遷移）。
- 卒論では、調べた範囲を限界として書く。
