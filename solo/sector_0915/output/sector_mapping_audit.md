# Sector Mapping Audit Report

- 生成时间：2026-09-16 22:00:53
- 数据基准日 effective_date：20260916
- Canonical 真源：`sector_master.json`（version=2.0，只读）
- 术语对齐：需求文本的 `theme_*` 对应本项目的 `sector_*`
- 旧配置（theme_config.json / subtheme_map.json / theme.json）：**仅报告存在性，未读取**

## 0. 扫描到的数据资产

| 资产 | 路径 |
|---|---|
| sector_master | D:\mystock\solo\sector_0915\sector_master.json |
| 申万 L1 分类 | D:\mystock\solo\sli\cache\classify_SW2021_L1.parquet |
| 申万 L2 分类 | D:\mystock\solo\sli\cache\classify_SW2021_L2.parquet |
| 申万 L3 分类 | D:\mystock\solo\sli\cache\classify_SW2021_L3.parquet |
| 申万成分快照（本地，仅覆盖部分 L3） | D:\mystock\solo\sli\cache\members_20260907.parquet |
| 申万行业归属全量表 | D:\mystock\solo\sector_0915\cache\sw_industry_all_*.parquet（index_member_all，股票维度 L1/L2/L3 全量） |
| stock_basic | D:\mystock\solo\sli\cache\stock_basic.parquet |
| trade_cal | D:\mystock\solo\sli\cache\trade_cal_20240101_20260916.parquet |
| 同花顺概念清单 | D:\mystock\cache_daily\parquet\ths_concepts_list.parquet |
| 同花顺概念成分 | D:\mystock\cache_daily\parquet\ths_concepts_members.parquet |
| 东财板块缓存 | D:\mystock\solo\sector_0915\cache\dc_boards_20260915.parquet |
| 东财成分缓存 | D:\mystock\solo\sector_0915\cache\dc_members_20260915.parquet |

旧配置文件发现：['theme.json', 'theme_config.json', 'bak0615\\theme.json', 'multi_factor_picker\\cache\\theme.json', 'theme_kg_v3\\theme_kg_v3\\config\\subtheme_map.json', 'theme_kg_v3\\theme_kg_v3\\config\\theme_config.json']

**数据说明 / 缺口**

- 申万(SW2021) L3 共 346 个行业，其中 12 个无成分股（多为「其他XX」类长尾行业，不进入固定层）：其他食品、多品类奢侈品、其他饰品、互联网药店、本地生活服务Ⅲ、博彩、其他银行Ⅲ、其他多元金融、音频媒体、社交Ⅲ、其他出版、医美服务

## 1. Canonical Sector

```
sector_master sectors = 50
rotation_groups      = 8
mappable rows        = 1881
```

## 2. Raw Boards

```
INDUSTRY_L1 = 62
INDUSTRY_L2 = 262
INDUSTRY_L3 = 683
CONCEPT = 894
Total = 1901
按来源 = {'EASTMONEY': 1000, 'TUSHARE': 901}
```

本阶段映射范围：`INDUSTRY_L3` + `INDUSTRY_L2` + `CONCEPT`（需求§六，其中 L2 为申万粗口径行业，仅用于补齐只划分到二级的核心产业），合计 1839 个；INDUSTRY_L1 仅作为层级信息（不参与映射）。成分来源：本地申万 L3 快照 + `index_member_all` 全量表补全（股票维度 L1/L2/L3）。

注：`INDUSTRY_*` 计数同时包含 TUSHARE/SW2021 与 EASTMONEY/DC_INDEX 两套行业口径（同名不同代码），故 683/262/62 并非单一体系行业数；申万侧分类快照实际为 L1 31 / L2 134 / L3 346，`index_member_all` 归属表覆盖其中 31/131/337。

## 3. Mapping

```
AUTO_ACCEPT = 611
REVIEW      = 178
OBSERVATION = 79
REJECT      = 0
UNMAPPED    = 1013
mapping rows（含多对多） = 1881
实际建立映射的 Raw Board = 826
```

映射方法分布：

| mapping_method | 条数 |
|---|---|
| UNMAPPED | 1013 |
| INDUSTRY_CONCEPT | 425 |
| DIRECT_INDUSTRY | 223 |
| INDUSTRY_PRODUCT | 206 |
| CONCEPT | 9 |
| KEYWORD | 5 |

| 匹配等级 | 条数 |
|---|---|
| UNMAPPED | 1002 |
| CONTAINS_NAME | 225 |
| EXACT_INDUSTRY | 172 |
| EXACT_PRODUCT | 106 |
| CONTAINS_PRODUCT | 103 |
| CONTAINS_INDUSTRY | 97 |
| EXACT_ALIAS | 58 |
| EXACT_NAME | 42 |
| EXACT_CONCEPT | 34 |
| CONTAINS_CONCEPT | 19 |
| CONTAINS_KEYWORD | 12 |
| EXACT_KEYWORD | 11 |

## 4. Membership

```
Stocks       = 4995
Memberships  = 23908
CORE         = 979
PRIMARY      = 2481
SECONDARY    = 890
THEMATIC     = 19558
OBSERVATION  = 0
覆盖Raw股票池比例 = 99.6%（分子=有任一 Sector 归属的股票，分母=过滤后的 Raw Member 股票池 5015）
```

## 5. Quality

```
TIER_1_CORE        = 33
TIER_2_ROTATION    = 8
TIER_3_OBSERVATION = 9
RAW_ONLY           = 0
```

纯度(sector_purity)与分层门槛只使用「固定层」成员（行业板块直接归属 + 核心公司），概念板块来源的 THEMATIC 成员属动态层，不计入纯度分母；TIER 门槛按固定层成员数判定（≥15/≥10/≥5），故 members 与 tier 可能不同步。

| sector | 名称 | tier | members | 固定层成员 | purity | industry_consistency | 质量分 |
|---|---|---|---|---|---|---|---|
| T32 | 纺织服饰 | TIER_1_CORE | 109 | 109 | 0.989 | 0.986 | 98.2 |
| T44 | 公用事业 | TIER_1_CORE | 129 | 129 | 0.985 | 0.963 | 98.9 |
| T41 | 医药商业 | TIER_1_CORE | 32 | 32 | 0.981 | 0.960 | 98.8 |
| T40 | 中药 | TIER_1_CORE | 132 | 60 | 0.944 | 1.000 | 90.0 |
| T19 | 钢铁 | TIER_1_CORE | 46 | 34 | 0.939 | 0.932 | 86.1 |
| T27 | 房地产 | TIER_1_CORE | 194 | 103 | 0.926 | 0.922 | 87.7 |
| T42 | 生物制品 | TIER_1_CORE | 112 | 53 | 0.915 | 0.955 | 88.4 |
| T47 | 建材 | TIER_1_CORE | 167 | 69 | 0.915 | 0.950 | 88.6 |
| T14 | 高端装备 | TIER_1_CORE | 534 | 218 | 0.915 | 0.952 | 89.0 |
| T22 | 农业周期 | TIER_1_CORE | 213 | 86 | 0.912 | 0.956 | 89.0 |
| T24 | 券商 | TIER_1_CORE | 211 | 50 | 0.907 | 1.000 | 90.0 |
| T34 | 旅游酒店餐饮 | TIER_1_CORE | 144 | 32 | 0.899 | 1.000 | 89.6 |
| T45 | 交通运输 | TIER_1_CORE | 494 | 123 | 0.898 | 0.977 | 89.3 |
| T36 | 创新药 | TIER_1_CORE | 327 | 169 | 0.897 | 0.951 | 89.8 |
| T37 | 医疗器械 | TIER_1_CORE | 434 | 131 | 0.897 | 0.962 | 88.5 |
| T28 | 地产链 | TIER_1_CORE | 334 | 86 | 0.896 | 0.971 | 89.4 |
| T39 | 医疗服务 | TIER_1_CORE | 149 | 52 | 0.886 | 0.914 | 87.0 |
| T05 | 消费电子 | TIER_1_CORE | 635 | 89 | 0.884 | 1.000 | 90.0 |
| T13 | 军工 | TIER_1_CORE | 614 | 122 | 0.882 | 0.992 | 88.8 |
| T15 | 汽车及零部件 | TIER_1_CORE | 825 | 261 | 0.881 | 0.961 | 88.3 |
| T16 | 有色金属 | TIER_1_CORE | 274 | 107 | 0.878 | 0.960 | 86.6 |
| T23 | 银行 | TIER_1_CORE | 316 | 42 | 0.878 | 1.000 | 88.7 |
| T03 | 通信 | TIER_1_CORE | 490 | 117 | 0.871 | 0.903 | 87.4 |
| T30 | 家电 | TIER_1_CORE | 189 | 90 | 0.866 | 0.924 | 85.4 |
| T25 | 保险 | TIER_3_OBSERVATION | 100 | 5 | 0.864 | 1.000 | 90.0 |
| T17 | 煤炭 | TIER_1_CORE | 171 | 42 | 0.857 | 0.987 | 85.2 |
| T31 | 消费零售 | TIER_1_CORE | 772 | 89 | 0.857 | 0.953 | 87.7 |
| T35 | 消费服务 | TIER_1_CORE | 338 | 29 | 0.852 | 0.981 | 88.1 |
| T04 | 软件与信创 | TIER_1_CORE | 488 | 239 | 0.848 | 0.995 | 82.3 |
| T43 | 电力 | TIER_1_CORE | 866 | 113 | 0.845 | 0.893 | 87.2 |
| T33 | 美容护理 | TIER_1_CORE | 159 | 29 | 0.811 | 0.874 | 84.5 |
| T48 | 城市更新 | TIER_1_CORE | 495 | 52 | 0.793 | 1.000 | 83.8 |
| T01 | 半导体 | TIER_1_CORE | 1031 | 241 | 0.771 | 0.830 | 82.5 |
| T29 | 食品饮料 | TIER_1_CORE | 256 | 124 | 0.770 | 0.829 | 82.3 |
| T09 | 光伏产业链 | TIER_2_ROTATION | 769 | 172 | 0.732 | 0.921 | 80.2 |
| T20 | 基础化工 | TIER_2_ROTATION | 407 | 299 | 0.716 | 0.831 | 77.3 |
| T21 | 化工新材料 | TIER_2_ROTATION | 557 | 265 | 0.708 | 0.882 | 78.5 |
| T12 | 机器人与自动化 | TIER_2_ROTATION | 1432 | 229 | 0.702 | 0.902 | 82.5 |
| T46 | 建筑装饰 | TIER_2_ROTATION | 198 | 39 | 0.689 | 1.000 | 76.5 |
| T02 | AI算力 | TIER_2_ROTATION | 1190 | 159 | 0.684 | 0.878 | 81.5 |
| T49 | 基础设施 | TIER_2_ROTATION | 391 | 38 | 0.673 | 0.873 | 78.9 |
| T08 | 锂电产业链 | TIER_2_ROTATION | 927 | 104 | 0.651 | 0.919 | 78.1 |
| T26 | 金融科技 | TIER_3_OBSERVATION | 327 | 219 | 0.547 | 0.818 | 73.0 |
| T18 | 石油石化 | TIER_3_OBSERVATION | 174 | 46 | 0.502 | 0.630 | 70.1 |
| T10 | 风电产业链 | TIER_3_OBSERVATION | 561 | 159 | 0.492 | 0.760 | 83.6 |
| T07 | 新能源汽车 | TIER_3_OBSERVATION | 1336 | 172 | 0.356 | 0.721 | 69.1 |
| T38 | CXO | TIER_3_OBSERVATION | 176 | 51 | 0.352 | 0.678 | 67.1 |
| T11 | 储能与电力设备 | TIER_3_OBSERVATION | 1002 | 130 | 0.332 | 0.691 | 67.2 |
| T06 | AI应用 | TIER_3_OBSERVATION | 1236 | 126 | 0.325 | 0.700 | 67.5 |
| T50 | 央国企基建 | TIER_3_OBSERVATION | 1445 | 148 | 0.320 | 0.681 | 67.0 |

## 6. Pollution

```
DYNAMIC_ONLY           = 123
CONCEPT_POLLUTION      = 1
INDUSTRY_CONFLICT      = 1
总计 = 125
```

## 7. Overlap（Weighted Jaccard Top 20）

| A | B | jaccard | weighted_jaccard | containment | class | note |
|---|---|---|---|---|---|---|
| T04 软件与信创 | T26 金融科技 | 0.518 | 0.415 | 0.850 | CROSS_THEME |  |
| T07 新能源汽车 | T15 汽车及零部件 | 0.418 | 0.397 | 0.772 | CROSS_THEME | 同一rotation_group内重叠，属正常轮动关联 |
| T09 光伏产业链 | T11 储能与电力设备 | 0.345 | 0.329 | 0.590 | CROSS_THEME | 同一rotation_group内重叠，属正常轮动关联 |
| T07 新能源汽车 | T12 机器人与自动化 | 0.343 | 0.316 | 0.529 | CROSS_THEME | 同一rotation_group内重叠，属正常轮动关联 |
| T07 新能源汽车 | T11 储能与电力设备 | 0.314 | 0.304 | 0.557 | CROSS_THEME | 同一rotation_group内重叠，属正常轮动关联 |
| T10 风电产业链 | T11 储能与电力设备 | 0.313 | 0.290 | 0.665 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T04 软件与信创 | T06 AI应用 | 0.306 | 0.274 | 0.828 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T08 锂电产业链 | T09 光伏产业链 | 0.284 | 0.269 | 0.488 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T08 锂电产业链 | T11 储能与电力设备 | 0.281 | 0.268 | 0.456 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T10 风电产业链 | T43 电力 | 0.312 | 0.267 | 0.604 | KEEP_SEPARATE |  |
| T01 半导体 | T02 AI算力 | 0.293 | 0.260 | 0.488 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T02 AI算力 | T11 储能与电力设备 | 0.264 | 0.255 | 0.457 | KEEP_SEPARATE |  |
| T11 储能与电力设备 | T43 电力 | 0.269 | 0.254 | 0.457 | KEEP_SEPARATE |  |
| T07 新能源汽车 | T08 锂电产业链 | 0.267 | 0.249 | 0.515 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T02 AI算力 | T03 通信 | 0.276 | 0.248 | 0.741 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T02 AI算力 | T06 AI应用 | 0.253 | 0.244 | 0.411 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T02 AI算力 | T12 机器人与自动化 | 0.265 | 0.243 | 0.462 | KEEP_SEPARATE |  |
| T12 机器人与自动化 | T15 汽车及零部件 | 0.277 | 0.235 | 0.594 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |
| T02 AI算力 | T07 新能源汽车 | 0.245 | 0.231 | 0.418 | KEEP_SEPARATE |  |
| T07 新能源汽车 | T09 光伏产业链 | 0.247 | 0.231 | 0.542 | KEEP_SEPARATE | 同一rotation_group内重叠，属正常轮动关联 |

## 8. Review（最需要人工检查的 50 条 Mapping）

| raw_board | source | type | best_sector | conf | method | members | action |
|---|---|---|---|---|---|---|---|
| 融资融券 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 3523 | UNMAPPED |
| 融资融券 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 3518 | UNMAPPED |
| 深股通 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 1881 | UNMAPPED |
| 深股通 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1879 | UNMAPPED |
| 沪股通 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 1643 | UNMAPPED |
| 沪股通 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1643 | UNMAPPED |
| 创业板综 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1360 | UNMAPPED |
| 富时罗素 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1246 | UNMAPPED |
| 专精特新 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1056 | UNMAPPED |
| 标准普尔 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1021 | UNMAPPED |
| 机构重仓 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 1016 | UNMAPPED |
| 专精特新 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 1006 | UNMAPPED |
| 小盘股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 995 | UNMAPPED |
| 华为概念 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 948 | UNMAPPED |
| 破增发价股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 803 | UNMAPPED |
| QFII重仓 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 775 | UNMAPPED |
| 一带一路 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 729 | UNMAPPED |
| DeepSeek概念 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 721 | UNMAPPED |
| 华为概念 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 712 | UNMAPPED |
| 破发股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 685 | UNMAPPED |
| 比亚迪概念 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 641 | UNMAPPED |
| 西部大开发 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 566 | UNMAPPED |
| MSCI中国 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 563 | UNMAPPED |
| 2026中报预增 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 561 | UNMAPPED |
| 人民币贬值受益 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 548 | UNMAPPED |
| 西部大开发 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 547 | UNMAPPED |
| 中证500 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 500 | UNMAPPED |
| 深成500 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 500 | UNMAPPED |
| 2026中报预增 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 498 | UNMAPPED |
| 中盘股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 498 | UNMAPPED |
| 一带一路 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 485 | UNMAPPED |
| 商业航天 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 469 | UNMAPPED |
| 数字经济 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 465 | UNMAPPED |
| 低空经济 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 465 | UNMAPPED |
| 粤港澳大湾区 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 463 | UNMAPPED |
| 创投 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 445 | UNMAPPED |
| 股权转让(并购重组) | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 437 | UNMAPPED |
| 无人机 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 436 | UNMAPPED |
| 破净股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 430 | UNMAPPED |
| 物联网 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 428 | UNMAPPED |
| 5G | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 426 | UNMAPPED |
| 工业互联网 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 411 | UNMAPPED |
| 微盘股 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 400 | UNMAPPED |
| 长江三角 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 390 | UNMAPPED |
| 乡村振兴 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 387 | UNMAPPED |
| 上证380 | EASTMONEY | CONCEPT |  | 0.00 | UNMAPPED | 380 | UNMAPPED |
| 智慧城市 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 376 | UNMAPPED |
| 氢能源 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 375 | UNMAPPED |
| 无人驾驶 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 374 | UNMAPPED |
| 数据要素 | TUSHARE | CONCEPT |  | 0.00 | UNMAPPED | 362 | UNMAPPED |

## 9. 人工抽样验证

### T01 半导体（CORE_ROTATION / R01）

- member_count = 1031
- purity = 0.771；industry_consistency = 0.830；tier = TIER_1_CORE；质量分 = 82.5
- CORE examples = ['康强电子', '北方华创', '雅克科技', '中晶科技', '华亚智能', '长川科技', '江丰电子', '阿石创', '富乐德', '联动科技']
- PRIMARY examples = ['德明利', '紫光国微', '大港股份', '通富微电', '华天科技', '大为股份', '台基股份', '航宇微', '国民技术', '君正股份']
- Top raw boards = ['半导体[TUSHARE|DIRECT_INDUSTRY|1.00]', '半导体材料[TUSHARE|INDUSTRY_PRODUCT|1.00]', '半导体设备[TUSHARE|INDUSTRY_PRODUCT|1.00]', '半导体[EASTMONEY|DIRECT_INDUSTRY|1.00]', '半导体设备[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '半导体材料[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '集成电路封测[TUSHARE|INDUSTRY_CONCEPT|0.95]', '模拟芯片设计[EASTMONEY|INDUSTRY_CONCEPT|0.95]']
- mapping confidence 均值 = 0.904，最高 = 1.000
- pollution = 无

### T02 AI算力（CORE_ROTATION / R01）

- member_count = 1190
- purity = 0.684；industry_consistency = 0.878；tier = TIER_2_ROTATION；质量分 = 81.5
- CORE examples = （无）
- PRIMARY examples = ['中国长城', '浪潮信息', '新大陆', '魅视科技', '智微智能', '广电运通', '奔图科技', '证通电子', '大华股份', '电科网安']
- Top raw boards = ['计算机设备[TUSHARE|DIRECT_INDUSTRY|1.00]', '通信设备[TUSHARE|DIRECT_INDUSTRY|1.00]', '其他计算机设备[TUSHARE|INDUSTRY_PRODUCT|1.00]', '其他计算机设备[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '计算机设备[EASTMONEY|DIRECT_INDUSTRY|1.00]', '通信设备[EASTMONEY|DIRECT_INDUSTRY|1.00]', '铜缆高速连接[TUSHARE|INDUSTRY_CONCEPT|0.80]', '算力概念[EASTMONEY|INDUSTRY_CONCEPT|0.75]']
- mapping confidence 均值 = 0.797，最高 = 1.000
- pollution = {'DYNAMIC_ONLY': 72}

### T03 通信（CORE_ROTATION / R01）

- member_count = 490
- purity = 0.871；industry_consistency = 0.903；tier = TIER_1_CORE；质量分 = 87.4
- CORE examples = （无）
- PRIMARY examples = ['中兴通讯', '特发信息', '汇源通信', '国安股份', '中嘉博创', '汇绿生态', '东信和平', '恒宝股份', '三维通信', '梦网科技']
- Top raw boards = ['通信服务[TUSHARE|DIRECT_INDUSTRY|1.00]', '通信设备[TUSHARE|DIRECT_INDUSTRY|1.00]', '通信服务[EASTMONEY|DIRECT_INDUSTRY|1.00]', '通信设备[EASTMONEY|DIRECT_INDUSTRY|1.00]', '通信线缆及配套[TUSHARE|INDUSTRY_CONCEPT|0.95]', '通信网络设备及器件[TUSHARE|INDUSTRY_CONCEPT|0.95]', '通信工程及服务[TUSHARE|INDUSTRY_CONCEPT|0.95]', '通信应用增值服务[TUSHARE|INDUSTRY_CONCEPT|0.95]']
- mapping confidence 均值 = 0.912，最高 = 1.000
- pollution = 无

### T07 新能源汽车（CORE_ROTATION / R02）

- member_count = 1336
- purity = 0.356；industry_consistency = 0.721；tier = TIER_3_OBSERVATION；质量分 = 69.1
- CORE examples = （无）
- PRIMARY examples = （无）
- Top raw boards = ['汽车零部件[TUSHARE|DIRECT_INDUSTRY|1.00]', '汽车零部件[EASTMONEY|DIRECT_INDUSTRY|1.00]', '新能源汽车[TUSHARE|INDUSTRY_CONCEPT|0.92]', '乘用车[TUSHARE|DIRECT_INDUSTRY|0.90]', '电动乘用车[TUSHARE|INDUSTRY_PRODUCT|0.90]', '商用车[TUSHARE|DIRECT_INDUSTRY|0.90]', '综合乘用车[TUSHARE|INDUSTRY_PRODUCT|0.90]', '综合乘用车[EASTMONEY|INDUSTRY_PRODUCT|0.90]']
- mapping confidence 均值 = 0.899，最高 = 1.000
- pollution = 无

### T08 锂电产业链（CORE_ROTATION / R02）

- member_count = 927
- purity = 0.651；industry_consistency = 0.919；tier = TIER_2_ROTATION；质量分 = 78.1
- CORE examples = ['德赛电池', '豪鹏科技', '国轩高科', '蔚蓝锂芯', '科达利', '亿纬锂能', '欣旺达', '鹏辉能源', '领湃科技', '宁德时代']
- PRIMARY examples = （无）
- Top raw boards = ['锂电池[TUSHARE|DIRECT_INDUSTRY|1.00]', '锂电池[EASTMONEY|DIRECT_INDUSTRY|1.00]', '能源金属[TUSHARE|DIRECT_INDUSTRY|0.90]', '电池[TUSHARE|DIRECT_INDUSTRY|0.90]', '电池化学品[TUSHARE|INDUSTRY_PRODUCT|0.90]', '蓄电池及其他电池[TUSHARE|INDUSTRY_PRODUCT|0.90]', '燃料电池[TUSHARE|INDUSTRY_PRODUCT|0.90]', '蓄电池及其他电池[EASTMONEY|INDUSTRY_PRODUCT|0.90]']
- mapping confidence 均值 = 0.808，最高 = 1.000
- pollution = 无

### T10 风电产业链（CORE_ROTATION / R02）

- member_count = 561
- purity = 0.492；industry_consistency = 0.760；tier = TIER_3_OBSERVATION；质量分 = 83.6
- CORE examples = ['和展能源', '金风科技', '大金重工', '天顺风能', '泰胜风能', '通裕重工', '金雷股份', '天能重工', '双一科技', '运达股份']
- PRIMARY examples = （无）
- Top raw boards = ['风电设备[TUSHARE|DIRECT_INDUSTRY|1.00]', '风电整机[TUSHARE|INDUSTRY_PRODUCT|1.00]', '风电设备[EASTMONEY|DIRECT_INDUSTRY|1.00]', '风电零部件[TUSHARE|INDUSTRY_PRODUCT|1.00]', '风电零部件[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '风电整机[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '电网设备[TUSHARE|DIRECT_INDUSTRY|0.90]', '电网设备[EASTMONEY|DIRECT_INDUSTRY|0.90]']
- mapping confidence 均值 = 0.937，最高 = 1.000
- pollution = {'CONCEPT_POLLUTION': 1, 'INDUSTRY_CONFLICT': 1}

### T23 银行（CORE_ROTATION / R04）

- member_count = 316
- purity = 0.878；industry_consistency = 1.000；tier = TIER_1_CORE；质量分 = 88.7
- CORE examples = ['兰州银行', '宁波银行', '江阴银行', '张家港行', '郑州银行', '青岛银行', '青农商行', '苏州银行', '无锡银行', '江苏银行']
- PRIMARY examples = ['平安银行', '浦发银行', '华夏银行', '民生银行', '招商银行', '兴业银行', '农业银行', '交通银行', '工商银行', '邮储银行']
- Top raw boards = ['城商行Ⅱ[TUSHARE|INDUSTRY_PRODUCT|1.00]', '城商行Ⅲ[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '农商行Ⅱ[TUSHARE|INDUSTRY_PRODUCT|1.00]', '城商行Ⅲ[TUSHARE|INDUSTRY_PRODUCT|1.00]', '农商行Ⅲ[TUSHARE|INDUSTRY_PRODUCT|1.00]', '农商行Ⅲ[EASTMONEY|INDUSTRY_PRODUCT|1.00]', '银行Ⅱ[EASTMONEY|DIRECT_INDUSTRY|1.00]', '国有大型银行Ⅱ[TUSHARE|INDUSTRY_CONCEPT|0.95]']
- mapping confidence 均值 = 0.932，最高 = 1.000
- pollution = 无

### T24 券商（CORE_ROTATION / R04）

- member_count = 211
- purity = 0.907；industry_consistency = 1.000；tier = TIER_1_CORE；质量分 = 90.0
- CORE examples = ['申万宏源', '东北证券', '锦龙股份', '国元证券', '国海证券', '广发证券', '长江证券', '山西证券', '国盛证券', '西部证券']
- PRIMARY examples = （无）
- Top raw boards = ['证券Ⅱ[TUSHARE|DIRECT_INDUSTRY|1.00]', '证券Ⅲ[TUSHARE|DIRECT_INDUSTRY|1.00]', '证券Ⅱ[EASTMONEY|DIRECT_INDUSTRY|1.00]', '证券Ⅲ[EASTMONEY|DIRECT_INDUSTRY|1.00]', '券商概念[EASTMONEY|INDUSTRY_CONCEPT|0.97]', '参股券商[TUSHARE|INDUSTRY_CONCEPT|0.67]', '券商金股[EASTMONEY|INDUSTRY_CONCEPT|0.67]', '参股券商[EASTMONEY|INDUSTRY_CONCEPT|0.67]']
- mapping confidence 均值 = 0.872，最高 = 1.000
- pollution = 无

### T29 食品饮料（CORE_ROTATION / R05）

- member_count = 256
- purity = 0.770；industry_consistency = 0.829；tier = TIER_1_CORE；质量分 = 82.3
- CORE examples = ['泸州老窖', '古井贡酒', '酒鬼酒', '五粮液', '顺鑫农业', '皇台酒业', '洋河股份', '天佑德酒', '伊力特', '金种子酒']
- PRIMARY examples = ['广弘控股', '双汇发展', '千味央厨', '金字火腿', '得利斯', '三全食品', '金达威', '克明食品', '华统股份', '海欣食品']
- Top raw boards = ['食品加工[TUSHARE|DIRECT_INDUSTRY|1.00]', '白酒Ⅱ[TUSHARE|DIRECT_INDUSTRY|1.00]', '白酒Ⅲ[TUSHARE|DIRECT_INDUSTRY|1.00]', '白酒Ⅱ[EASTMONEY|DIRECT_INDUSTRY|1.00]', '食品加工[EASTMONEY|DIRECT_INDUSTRY|1.00]', '白酒Ⅲ[EASTMONEY|DIRECT_INDUSTRY|1.00]', '烘焙食品[TUSHARE|INDUSTRY_CONCEPT|0.95]', '食品及饲料添加剂[TUSHARE|INDUSTRY_CONCEPT|0.95]']
- mapping confidence 均值 = 0.928，最高 = 1.000
- pollution = 无

### T36 创新药（CORE_ROTATION / R06）

- member_count = 327
- purity = 0.897；industry_consistency = 0.951；tier = TIER_1_CORE；质量分 = 89.8
- CORE examples = ['丰原药业', '丽珠集团', '海南海药', '东北制药', '通化金马', '北大医药', '德展健康', '石药景峰', '华特达因', '金陵药业']
- PRIMARY examples = ['普洛药业', '新华制药', '广济药业', '中哲精化', '海森药业', '海翔药业', '仙琚制药', '永安药业', '黄山胶囊', '尔康制药']
- Top raw boards = ['化学制药[TUSHARE|DIRECT_INDUSTRY|1.00]', '生物制品[TUSHARE|DIRECT_INDUSTRY|1.00]', '化学制剂[TUSHARE|DIRECT_INDUSTRY|1.00]', '化学制剂[EASTMONEY|DIRECT_INDUSTRY|1.00]', '生物制品[EASTMONEY|DIRECT_INDUSTRY|1.00]', '化学制药[EASTMONEY|DIRECT_INDUSTRY|1.00]', '创新药[EASTMONEY|INDUSTRY_CONCEPT|0.92]', '创新药[TUSHARE|INDUSTRY_CONCEPT|0.92]']
- mapping confidence 均值 = 0.964，最高 = 1.000
- pollution = 无

### T37 医疗器械（CORE_ROTATION / R06）

- member_count = 434
- purity = 0.897；industry_consistency = 0.962；tier = TIER_1_CORE；质量分 = 88.5
- CORE examples = ['鱼跃医疗', '蓝帆医疗', '尚荣医疗', '大博医疗', '奥美医疗', '乐普医疗', '阳普医疗', '福瑞医科', '东富龙', '理邦仪器']
- PRIMARY examples = ['科华生物', '达安基因', '九安医疗', '利德曼', '九强生物', '美康生物', '迈克生物', '万孚生物', '凯普生物', '透景生命']
- Top raw boards = ['医疗器械[TUSHARE|DIRECT_INDUSTRY|1.00]', '医疗设备[TUSHARE|DIRECT_INDUSTRY|1.00]', '医疗耗材[TUSHARE|DIRECT_INDUSTRY|1.00]', '医疗器械[EASTMONEY|DIRECT_INDUSTRY|1.00]', '医疗耗材[EASTMONEY|DIRECT_INDUSTRY|1.00]', '医疗设备[EASTMONEY|DIRECT_INDUSTRY|1.00]', '体外诊断[TUSHARE|INDUSTRY_CONCEPT|0.97]', '体外诊断[EASTMONEY|INDUSTRY_CONCEPT|0.97]']
- mapping confidence 均值 = 0.895，最高 = 1.000
- pollution = 无

### T43 电力（CORE_ROTATION / R07）

- member_count = 866
- purity = 0.845；industry_consistency = 0.893；tier = TIER_1_CORE；质量分 = 87.2
- CORE examples = （无）
- PRIMARY examples = ['深圳能源', '深南电A', '川能动力', '珠海港', '穗恒运A', '绿发电力', '粤电力A', '皖能电力', '太阳能', '新能股份']
- Top raw boards = ['电力[TUSHARE|DIRECT_INDUSTRY|1.00]', '电力[EASTMONEY|DIRECT_INDUSTRY|1.00]', '火力发电[TUSHARE|INDUSTRY_CONCEPT|0.95]', '风力发电[TUSHARE|INDUSTRY_CONCEPT|0.95]', '核力发电[TUSHARE|INDUSTRY_CONCEPT|0.95]', '其他能源发电[TUSHARE|INDUSTRY_CONCEPT|0.95]', '风力发电[EASTMONEY|INDUSTRY_CONCEPT|0.95]', '核力发电[EASTMONEY|INDUSTRY_CONCEPT|0.95]']
- mapping confidence 均值 = 0.849，最高 = 1.000
- pollution = 无

### T50 央国企基建（POLICY / R08）

- member_count = 1445
- purity = 0.320；industry_consistency = 0.681；tier = TIER_3_OBSERVATION；质量分 = 67.0
- CORE examples = （无）
- PRIMARY examples = （无）
- Top raw boards = ['电力[TUSHARE|DIRECT_INDUSTRY|1.00]', '电力[EASTMONEY|DIRECT_INDUSTRY|1.00]', '基础建设[TUSHARE|DIRECT_INDUSTRY|0.90]', '基础建设[EASTMONEY|DIRECT_INDUSTRY|0.90]', '国企改革[TUSHARE|INDUSTRY_CONCEPT|0.75]', '中特估[EASTMONEY|INDUSTRY_CONCEPT|0.75]', '上海国企改革[TUSHARE|INDUSTRY_CONCEPT|0.65]', '同花顺中特估100[TUSHARE|INDUSTRY_CONCEPT|0.65]']
- mapping confidence 均值 = 0.777，最高 = 1.000
- pollution = 无


## 10. Validation（CHECK 01-10）

| check | status | detail |
|---|---|---|
| CHECK 01 | PASS | sector_master.json 合法，version=2.0，sectors=50 |
| CHECK 02 | PASS | 非法 sector_id：无 |
| CHECK 03 | PASS | membership 中不存在的 Sector：无 |
| CHECK 04 | PASS | (ts_code, sector_id, effective_date) 重复行数=0 |
| CHECK 05 | PASS | confidence 缺失数=0 |
| CHECK 06 | PASS | membership_type 缺失=0，非法取值=无 |
| CHECK 07 | PASS | 未来 effective_date 行数=0（构建日期=20260916） |
| CHECK 08 | PASS | membership/mapping 生成链路未使用任何价格/涨停/热度字段（命中列：无） |
| CHECK 09 | PASS | 旧配置仅报告存在性，未读取：['theme.json', 'theme_config.json', 'bak0615\\theme.json', 'multi_factor_picker\\cache\\theme.json', 'theme_kg_v3\\theme_kg_v3\\config\\subtheme_map.json', 'theme_kg_v3\\theme_kg_v3\\config\\theme_config.json'] |
| CHECK 10 | PASS | 可映射 Raw Board=1839，UNMAPPED=1013（55.1%）。UNMAPPED 主因：板块名称与本体系关键词/行业/概念无实质交集，按需求§三十七不降低阈值硬塞，保留为 UNMAPPED/OBSERVATION/REVIEW。 |

## 11. 本阶段边界

本次仅完成 `sector_master -> raw board mapping -> stock membership`。未实现 SEOS / 板块启动 / 板块强弱 / 板块生命周期 / 板块轮动 / HVT / IGE / F120 / Execution / BUY / NO TRADE。

映射规则要点：

- 优先级：行业逻辑 > 产业链逻辑 > 产品逻辑 > 公司主营 > 概念关系 > 关键词。
- 概念板块来源的成员最高只能到 SECONDARY，且仅当个股申万主营行业与 Sector 行业定义一致；其余一律 THEMATIC，并标记为动态层。
- CORE 仅来自：行业板块直接归属（申万/东财三级行业板块 + 主营行业一致）、Theme Master 声明的核心公司（core_companies）。**core_companies 为显式声明，不受所在板块 mapping_action 封顶，也不依赖其是否出现在板块成分表中**（否则主业不在该板块的核心公司会被降级或丢失，如风电产业链的东方电缆/中天科技）。
- 个股行业证据分级（申万口径）：L3 精确/关键词命中 = L3_EXACT/L3_KEY；L2 精确命中且该二级行业只被本 Sector 认领 = L2_EXCLUSIVE；L2 精确命中但被多个 Sector 共享 = L2_SHARED。行业板块成员上限：L3_EXACT/L3_KEY/L2_EXCLUSIVE → PRIMARY，L2_SHARED → SECONDARY。L2 板块（申万粗口径）来源最高只到 PRIMARY，不给 CORE（唯一例外：core_companies 显式声明的核心公司，与板块口径无关）。
- 同一 (ts_code, sector_id) 去重优先序：membership_type > 板块来源（行业板块优先于概念板块，需求§十八）> confidence。
- 通用词（设备/材料/系统/服务/智能…）禁止包含匹配，必须完全相等，用于阻断 Keyword Count Inflation（例：锂电的 subsector「设备」曾把 照明设备/制冷空调设备 吸入 T08，已修复）。
- 未映射 Raw Board 保留为 UNMAPPED，不降低阈值硬塞。

## 12. 已知限制（本阶段不改）

- **申万成分数据完整性**：本地 `members_*.parquet` 快照仅覆盖 258/337 个申万 L3，曾导致 88 个三级行业（集成电路制造/电动乘用车/硅料硅片/逆变器/风电整机/核力发电…）成员数为 0、26/50 个 Sector 固定层偏薄。现已改用 `index_member_all` 全量表（股票维度 L1/L2/L3），覆盖 337 个申万 L3，仅剩 12 个「其他XX」类长尾行业无成分。
- **未采用同花顺概念数据补数**：同花顺概念板块属需求§十八最低优先级（概念关系），且其板块口径为市场炒作概念而非行业分类，接入会直接冲击「禁止概念污染」与「不要过度追求覆盖率」两条硬约束；本阶段数据源限定申万（行业）+ 东财（行业/概念）两套，不引入第三套。
- **L2 共享导致的固定层上限**：部分 Sector 的行业定义落在申万二级（乘用车/软件开发/基础建设/化学制品等），而该二级行业被多个 Sector 共同认领时只能给 SECONDARY，使 T07 新能源汽车、T26 金融科技、T50 央国企基建等出现 fixed 成员多但 PRIMARY=0、purity 偏低（0.32-0.55）的情况。这是「大类行业不得被单一 Sector 独占」的设计结果，不是数据缺失，本阶段不做特殊放宽（避免概念污染）。
- **CORE 覆盖面取决于 subsectors 命名是否与申万 L3 同名**：CORE 需「申万 L3 名 == Sector 的 sector_name/aliases/eastmoney_industry_keywords/subsectors」，故只有名字完全对得上的子行业能整体成 CORE。实例：T01 半导体只有「半导体材料/半导体设备」两个子行业成的 CORE（「封测」对应申万 L3「集成电路封测」、名字不同，落 PRIMARY）；T23 银行 CORE 仅城商行/农商行，国有大行与股份行落 PRIMARY。此为保守设计，宁缺毋滥；如需扩大 CORE 需在 `sector_master.json` 的 subsectors 中补齐申万标准三级行业名（该文件为只读真源，本阶段不改）。
- **个别 subsector 语义宽于 Sector 本意**：T36 创新药把整个申万 L3「化学制剂」（103 只）算作 CORE，会纳入仿制药/普药企业，不等同于严格意义的创新药。这源自 `sector_master.json` 的 subsectors 配置，程序忠实执行，未做二次过滤。
