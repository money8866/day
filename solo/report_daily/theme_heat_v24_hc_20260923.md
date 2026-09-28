══════════════════════════════════════════════════════════════
V2.4-HC 主题人工确认
交易日：20260923
══════════════════════════════════════════════════════════════
* 本层不重算 Heat、不改权重、不改机器排名；只做成分股验证 + 广度验证 + 样本可靠性验证。
* 四分类：CORE / RELATED / WEAK / POLLUTION；纯度分级：PURE / ACCEPTABLE / CONTAMINATED。
* 小样本门槛 N<20 → LOW_SAMPLE_REVIEW；允许输出「暂无经人工确认的最强主题」。

【主题定义自检（Q1）】
  本交易日机器覆盖主题 32 个；其中纯度 PURE 10、ACCEPTABLE 2、CONTAMINATED 20。
  主题定义本身不清晰 / 多产业混合者，按 §六 判 INVALID，列入文末「需要人工进一步确认」。

【TODAY】
机器第一：半导体
人工确认：半导体
  原因：成分纯度 ACCEPTABLE（VALID 70%）；TODAY 广度 50.2%（CONFIRMED_TODAY）。
第二：地产链
  状态：VALID_BUT_NOT_BROAD
第三：创新药（未通过人工确认）
  状态：INVALID_THEME

【WEEK】
机器第一：创新药
人工确认：地产链
  原因：成分纯度 91%（POLLUTION 4%）；WEEK 广度 53.5%（WEEK_CONFIRMED）。
第二：半导体
  状态：WEEK_CONFIRMED
第三：创新药（未通过人工确认）
  状态：WEEK_INVALID

【MONTH】
机器第一：传媒
人工确认：暂无经人工确认的明确最强主题（§十八 允许无结果）
  原因：机器前 6 名均存在纯度不足 / N<20 / 扩散不足（NARROW、DIVERGENCE）中至少一项。
第二：半导体（未通过人工确认）
  状态：MONTH_LEADER_DRIVEN
第三：AI算力（未通过人工确认）
  状态：MONTH_INVALID

【成分股质量检查】
──────────────────────────────────────────────────────────────
主题：半导体　N（映射成员）：287
CORE：63.8%　RELATED：6.6%　WEAK：10.1%　POLLUTION：19.5%
结论：ACCEPTABLE（VALID 70.4%）
机器错误标记：ERROR4_行业代替主题｜ERROR5_名称误导(奥海科技、中英科技、星星科技)
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 天承科技 688603.SH　当日 6.76%　dc_industry_board｜仅因宽口径行业「化工原料」被纳入（行业代替主题）
  2. 光智科技 300489.SZ　当日 6.03%　dc_industry_board｜仅因宽口径行业「专用机械」被纳入（行业代替主题）
  3. 莱宝高科 002106.SZ　当日 5.06%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
需要人工进一步确认（WEAK 29 只，取前 3）：
  1. 清溢光电　dc_industry_board｜行业「半导体」属主题特征行业，但主营无直接证据
  2. 赛英电子　无主营文本（dc_industry_board）（数据缺口，需人工确认）
  3. 华岭股份　无主营文本（dc_industry_board）（数据缺口，需人工确认）
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    慧智微     688512.SH　CORE      当日 20.03%　dc_industry_board｜主营命中核心词 芯片
    富信科技   688662.SH　CORE      当日 16.21%　dc_industry_board｜主营命中核心词 半导体
    新恒汇     301678.SZ　CORE      当日 14.32%　dc_industry_board｜主营命中核心词 芯片
    联动科技   301369.SZ　CORE      当日 13.38%　dc_industry_board｜主营命中核心词 半导体
    康强电子   002119.SZ　CORE      当日 10.01%　dc_industry_board｜主营命中核心词 半导体
  [随机5]
    强力新材   300429.SZ　RELATED   当日 0.52%　dc_industry_board｜主营命中 光刻胶
    红板科技   603459.SH　POLLUTION 当日 -1.14%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
    源杰科技   688498.SH　CORE      当日 -1.03%　dc_industry_board｜主营命中核心词 芯片
    德邦科技   688035.SH　RELATED   当日 2.32%　dc_industry_board｜主营命中 封装
    盛美上海   688082.SH　CORE      当日 0.94%　dc_industry_board｜主营命中核心词 半导体
  [边缘5]
    京泉华     002885.SZ　POLLUTION 当日 1.05%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
    茂硕电源   002660.SZ　POLLUTION 当日 -1.37%　dc_industry_board｜仅因宽口径行业「电气设备」被纳入（行业代替主题）
    奥海科技   002993.SZ　POLLUTION 当日 -1.61%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
    中英科技   300936.SZ　POLLUTION 当日 2.69%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
    达利凯普   301566.SZ　POLLUTION 当日 1.63%　dc_industry_board｜仅因宽口径行业「元器件」被纳入（行业代替主题）
──────────────────────────────────────────────────────────────
主题：创新药　N（映射成员）：259
CORE：19.7%　RELATED：6.2%　WEAK：34.7%　POLLUTION：39.4%
结论：CONTAMINATED（VALID 25.9%）
机器错误标记：ERROR3_概念污染｜ERROR4_行业代替主题｜ERROR5_名称误导(华神科技、汉邦科技)
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 昊帆生物 301393.SZ　当日 8.86%　dc_industry_board｜仅因宽口径行业「化工原料」被纳入（行业代替主题）
  2. 甘李药业 603087.SH　当日 5.47%　dc_industry_board｜命中排除词 原料药
  3. 汉邦科技 688755.SH　当日 5.18%　sw_industry｜命中排除词 耗材
需要人工进一步确认（WEAK 90 只，取前 3）：
  1. 京新药业　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
  2. 舒泰神　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
  3. 辰欣药业　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    益诺思     688710.SH　CORE      当日 14.42%　dc_industry_board｜主营命中核心词 生物医药
    欧林生物   688319.SH　RELATED   当日 12.99%　dc_industry_board｜主营命中 疫苗
    奥浦迈     688293.SH　WEAK      当日 12.69%　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
    和元生物   688238.SH　CORE      当日 10.71%　dc_industry_board｜主营命中核心词 CRO
    义翘神州   301047.SZ　WEAK      当日 10.12%　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
  [随机5]
    翰宇药业   300199.SZ　WEAK      当日 1.84%　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
    益盛药业   002566.SZ　POLLUTION 当日 -1.82%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    灵康药业   603669.SH　WEAK      当日 0.17%　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
    康恩贝     600572.SH　POLLUTION 当日 -1.62%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    陇神戎发   300534.SZ　POLLUTION 当日 -4.78%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
  [边缘5]
    太龙药业   600222.SH　POLLUTION 当日 -1.11%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    金域医学   603882.SH　POLLUTION 当日 0.03%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
    苑东生物   688513.SH　POLLUTION 当日 1.46%　dc_industry_board｜命中排除词 原料药
    千金药业   600479.SH　POLLUTION 当日 -2.80%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    华神科技   000790.SZ　POLLUTION 当日 -3.41%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
──────────────────────────────────────────────────────────────
主题：地产链　N（映射成员）：89
CORE：75.3%　RELATED：15.7%　WEAK：4.5%　POLLUTION：4.5%
结论：PURE（VALID 91.0%）
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 特发服务 300917.SZ　当日 3.72%　dc_industry_board｜仅因宽口径行业「房产服务」被纳入（行业代替主题）
  2. 中国国贸 600007.SH　当日 1.02%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
  3. 南山控股 002314.SZ　当日 0.86%　dc_industry_board｜命中排除词 租赁
需要人工进一步确认（WEAK 4 只，取前 3）：
  1. 香江控股　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
  2. 中国武夷　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
  3. 城投控股　dc_industry_board｜行业「区域地产」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    华远控股   600743.SH　CORE      当日 10.16%　dc_industry_board｜主营命中核心词 房地产
    华丽家族   600503.SH　CORE      当日 10.11%　dc_industry_board｜主营命中核心词 房地产
    我爱我家   000560.SZ　CORE      当日 9.97%　dc_industry_board｜主营命中核心词 房地产
    特发服务   300917.SZ　POLLUTION 当日 3.72%　dc_industry_board｜仅因宽口径行业「房产服务」被纳入（行业代替主题）
    中华企业   600675.SH　CORE      当日 3.72%　dc_industry_board｜主营命中核心词 住宅
  [随机5]
    津投城开   600322.SH　CORE      当日 -1.08%　dc_industry_board｜主营命中核心词 房地产
    城投控股   600649.SH　WEAK      当日 0.98%　dc_industry_board｜行业「区域地产」属主题特征行业，但主营无直接证据
    金融街     000402.SZ　RELATED   当日 -2.88%　人工复核修正（§十四 Level 1）｜年报主营含「土地开发、房产开发」，属地产链，排除词「租赁」不适用
    城建发展   600266.SH　CORE      当日 -1.34%　dc_industry_board｜主营命中核心词 房地产
    凤凰股份   600716.SH　CORE      当日 -0.80%　dc_industry_board｜主营命中核心词 房地产
  [边缘5]
    张江高科   600895.SH　POLLUTION 当日 -1.68%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
    中国国贸   600007.SH　POLLUTION 当日 1.02%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
    南山控股   002314.SZ　POLLUTION 当日 0.86%　dc_industry_board｜命中排除词 租赁
    特发服务   300917.SZ　POLLUTION 当日 3.72%　dc_industry_board｜仅因宽口径行业「房产服务」被纳入（行业代替主题）
    香江控股   600162.SH　WEAK      当日 -6.13%　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
──────────────────────────────────────────────────────────────
主题：传媒　N（映射成员）：103
CORE：48.5%　RELATED：16.5%　WEAK：8.7%　POLLUTION：26.2%
结论：CONTAMINATED（VALID 65.0%）
机器错误标记：ERROR3_概念污染｜ERROR4_行业代替主题｜ERROR5_名称误导(掌阅科技、旗天科技、佳云科技)
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 国旅联合 600358.SH　当日 1.17%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）
  2. 凡拓数创 301313.SZ　当日 -0.25%　dc_industry_board｜仅因宽口径行业「文教休闲」被纳入（行业代替主题）
  3. 旗天科技 300061.SZ　当日 -1.41%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
需要人工进一步确认（WEAK 9 只，取前 3）：
  1. 中原传媒　dc_industry_board｜行业「出版业」属主题特征行业，但主营无直接证据
  2. 新媒股份　dc_industry_board｜行业「影视音像」属主题特征行业，但主营无直接证据
  3. 海看股份　dc_industry_board｜行业「影视音像」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    天威视讯   002238.SZ　RELATED   当日 10.06%　dc_industry_board｜主营命中 广播电视
    新华文轩   601811.SH　CORE      当日 9.99%　dc_industry_board｜主营命中核心词 出版
    新华传媒   600825.SH　CORE      当日 9.97%　dc_industry_board｜主营命中核心词 传媒
    博纳影业   001330.SZ　RELATED   当日 4.17%　dc_industry_board｜主营命中 电影
    内蒙新华   603230.SH　CORE      当日 2.94%　dc_industry_board｜主营命中核心词 出版
  [随机5]
    天龙集团   300063.SZ　POLLUTION 当日 -4.97%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    读客文化   301025.SZ　RELATED   当日 -4.66%　dc_industry_board｜主营命中 图书
    慈文传媒   002343.SZ　CORE      当日 -1.02%　dc_industry_board｜主营命中核心词 影视
    新华都     002264.SZ　POLLUTION 当日 -6.65%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    蓝色光标   300058.SZ　POLLUTION 当日 -3.77%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）
  [边缘5]
    川网传媒   300987.SZ　POLLUTION 当日 -4.34%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    掌阅科技   603533.SH　POLLUTION 当日 -4.85%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    浙文互联   600986.SH　POLLUTION 当日 -4.47%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    天娱数科   002354.SZ　POLLUTION 当日 -7.15%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    因赛集团   300781.SZ　POLLUTION 当日 -3.42%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）

【最终人工确认表】
主题           今日Heat    周Heat    月Heat      N 纯度            今日状态         周状态            月状态              人工结论
半导体               97        82        91    202 ACCEPTABLE      BROAD            BROAD             NARROW              VALID
创新药               93       100        71     66 CONTAMINATED    NARROW           BROAD             DIVERGENCE          INVALID（成分污染）
机器人               84        75        52     62 CONTAMINATED    NARROW           BROAD             DIVERGENCE          INVALID（成分污染）
量子计算             83        68        57      6 PURE            BROAD            DIVERGENCE        DIVERGENCE          LOW_SAMPLE_REVIEW(VALID)
医疗器械             83        92        46    108 CONTAMINATED    NARROW           BROAD             NEUTRAL             INVALID（成分污染）
新能源车             76        46        30    112 CONTAMINATED    NARROW           DIVERGENCE        NEUTRAL             INVALID（成分污染）
可控核聚变           75        37        71      9 CONTAMINATED    BROAD            NEUTRAL           DIVERGENCE          LOW_SAMPLE_REVIEW(INVALID（成分污染）)
地产链               73        94        65     80 PURE            NEUTRAL          BROAD             DIVERGENCE          VALID
高端材料             72        53        62     41 CONTAMINATED    NEUTRAL          DIVERGENCE        DIVERGENCE          INVALID（成分污染）
智能驾驶             69        63        34     19 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(INVALID（成分污染）)
建筑装饰             65        50        33     99 PURE            NEUTRAL          DIVERGENCE        NEUTRAL             VALID
化工                 63        43        64    189 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
节能环保             59        44        48     92 CONTAMINATED    NEUTRAL          DIVERGENCE        NEUTRAL             INVALID（成分污染）
消费电子             56        75        87     52 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
低空经济             51        60        65     12 CONTAMINATED    NEUTRAL          NEUTRAL           DIVERGENCE          LOW_SAMPLE_REVIEW(INVALID（成分污染）)
黄金                 51        13        19     11 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
钢铁                 50        14        18     36 ACCEPTABLE      NEUTRAL          NEUTRAL           NEUTRAL             VALID
AI算力               39        73        89     39 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
银行                 37         6        35     41 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
消费                 37        55        52     46 CONTAMINATED    NEUTRAL          DIVERGENCE        NEUTRAL             INVALID（成分污染）
电力                 36        20        23    191 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
工业金属             34        21        17     62 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
军工                 33        35        82     52 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
脑机接口             33        75        44      6 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(INVALID（成分污染）)
商业航天             32        24        73     17 CONTAMINATED    NEUTRAL          NEUTRAL           DIVERGENCE          LOW_SAMPLE_REVIEW(INVALID（成分污染）)
证券                 25        32        30     45 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
战略与小金属         22        22        10     40 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
能源金属             21        34        12     15 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
传媒                 20        74        96     67 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
信创                 18        77        64     50 CONTAMINATED    NEUTRAL          BROAD             DIVERGENCE          INVALID（成分污染）
游戏                 15        37        54     21 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
煤炭                 10        16        27     25 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID

【最终人工确认】
TODAY：半导体
WEEK：地产链
MONTH：暂无经人工确认的主题

最重要观察：
  机器 TODAY TOP10 中有 7 个主题成分为 CONTAMINATED：创新药、机器人、医疗器械、新能源车、可控核聚变、高端材料、智能驾驶。
  全部 32 个主题中 CONTAMINATED 20 个：映射池被宽口径行业板块稀释是系统性现象，不是个别错误（§十五 行业≠主题）。
  样本不足 20 的主题：量子计算、低空经济、智能驾驶、商业航天、脑机接口、可控核聚变、能源金属、黄金，其 Heat 高不代表形成广泛主题行情（§七）。
机器排名与人工判断存在明显偏差：
  WEEK：机器第一 创新药（Heat 100.0）未通过人工确认 → 成分为 CONTAMINATED；人工确认第一为 地产链。
  MONTH：机器第一 传媒（Heat 96.1）未通过人工确认 → 成分为 CONTAMINATED；人工确认第一为 暂无。
══════════════════════════════════════════════════════════════
