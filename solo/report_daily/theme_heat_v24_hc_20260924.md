══════════════════════════════════════════════════════════════
V2.4-HC 主题人工确认
交易日：20260924
══════════════════════════════════════════════════════════════
* 本层不重算 Heat、不改权重、不改机器排名；只做成分股验证 + 广度验证 + 样本可靠性验证。
* 四分类：CORE / RELATED / WEAK / POLLUTION；纯度分级：PURE / ACCEPTABLE / CONTAMINATED。
* 小样本门槛 N<20 → LOW_SAMPLE_REVIEW；允许输出「暂无经人工确认的最强主题」。

【主题定义自检（Q1）】
  本交易日机器覆盖主题 32 个；其中纯度 PURE 10、ACCEPTABLE 2、CONTAMINATED 20。
  主题定义本身不清晰 / 多产业混合者，按 §六 判 INVALID，列入文末「需要人工进一步确认」。

【TODAY】
机器第一：银行
人工确认：暂无经人工确认的明确最强主题（§十八 允许无结果）
  原因：机器前 6 名均存在纯度不足 / N<20 / 扩散不足（NARROW、DIVERGENCE）中至少一项。
第二：商业航天（未通过人工确认）
  状态：INVALID_THEME
第三：煤炭（未通过人工确认）
  状态：HOT_BUT_NARROW

【WEEK】
机器第一：创新药
人工确认：暂无经人工确认的明确最强主题（§十八 允许无结果）
  原因：机器前 6 名均存在纯度不足 / N<20 / 扩散不足（NARROW、DIVERGENCE）中至少一项。
第二：医疗器械（未通过人工确认）
  状态：WEEK_INVALID
第三：半导体（未通过人工确认）
  状态：WEEK_NARROW

【MONTH】
机器第一：传媒
人工确认：银行
  原因：成分纯度 98%（POLLUTION 0%）；MONTH 广度 32.3%（MONTH_LEADER_DRIVEN）。
第二：传媒（未通过人工确认）
  状态：MONTH_INVALID
第三：AI算力（未通过人工确认）
  状态：MONTH_INVALID

【成分股质量检查】
──────────────────────────────────────────────────────────────
主题：银行　N（映射成员）：42
CORE：97.6%　RELATED：0.0%　WEAK：2.4%　POLLUTION：0.0%
结论：PURE（VALID 97.6%）
机器错误标记：ERROR2_低Breadth第一
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
需要人工进一步确认（WEAK 1 只，取前 3）：
  1. 苏州银行　dc_industry_board｜行业「银行」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    重庆银行   601963.SH　CORE      当日 2.04%　人工确认名单（core_company）
    南京银行   601009.SH　CORE      当日 1.88%　人工确认名单（core_company）
    江苏银行   600919.SH　CORE      当日 1.57%　人工确认名单（core_company）
    渝农商行   601077.SH　CORE      当日 1.53%　dc_industry_board｜主营命中核心词 金融
    无锡银行   600908.SH　CORE      当日 1.39%　dc_industry_board｜主营命中核心词 金融
  [随机5]
    青岛银行   002948.SZ　CORE      当日 0.33%　dc_industry_board｜主营命中核心词 金融
    建设银行   601939.SH　CORE      当日 1.39%　人工确认名单（core_company）
    上海银行   601229.SH　CORE      当日 0.42%　dc_industry_board｜主营命中核心词 金融
    瑞丰银行   601528.SH　CORE      当日 -0.60%　人工确认名单（core_company）
    平安银行   000001.SZ　CORE      当日 -0.44%　人工确认名单（core_company）
  [边缘5]
    苏州银行   002966.SZ　WEAK      当日 1.21%　dc_industry_board｜行业「银行」属主题特征行业，但主营无直接证据
──────────────────────────────────────────────────────────────
主题：创新药　N（映射成员）：220
CORE：23.2%　RELATED：7.3%　WEAK：40.9%　POLLUTION：28.6%
结论：CONTAMINATED（VALID 30.5%）
机器错误标记：ERROR3_概念污染｜ERROR4_行业代替主题｜ERROR5_名称误导(华神科技、汉邦科技)
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 华神科技 000790.SZ　当日 10.12%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
  2. 贝瑞基因 000710.SZ　当日 9.99%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
  3. 诺禾致源 688315.SH　当日 5.21%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
需要人工进一步确认（WEAK 90 只，取前 3）：
  1. 通化金马　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
  2. 药石科技　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
  3. 德源药业　无主营文本（dc_industry_board）（数据缺口，需人工确认）
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    华神科技   000790.SZ　POLLUTION 当日 10.12%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    贝瑞基因   000710.SZ　POLLUTION 当日 9.99%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
    诺禾致源   688315.SH　POLLUTION 当日 5.21%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
    三元基因   920344.BJ　WEAK      当日 3.31%　无主营文本（dc_industry_board）（数据缺口，需人工确认）
    华大基因   300676.SZ　CORE      当日 2.50%　人工确认名单（leader_company）
  [随机5]
    万泽股份   000534.SZ　WEAK      当日 -1.22%　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
    天士力     600535.SH　POLLUTION 当日 -1.51%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    常山药业   300255.SZ　WEAK      当日 -2.20%　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
    海南海药   000566.SZ　POLLUTION 当日 -2.29%　dc_industry_board｜命中排除词 原料药
    义翘神州   301047.SZ　WEAK      当日 -2.09%　dc_industry_board｜行业「生物制药」属主题特征行业，但主营无直接证据
  [边缘5]
    甘李药业   603087.SH　POLLUTION 当日 -1.50%　dc_industry_board｜命中排除词 原料药
    红日药业   300026.SZ　POLLUTION 当日 -2.15%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    诺禾致源   688315.SH　POLLUTION 当日 5.21%　dc_industry_board｜仅因宽口径行业「医疗保健」被纳入（行业代替主题）
    康恩贝     600572.SH　POLLUTION 当日 -0.94%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    佐力药业   300181.SZ　POLLUTION 当日 -1.52%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
──────────────────────────────────────────────────────────────
主题：传媒　N（映射成员）：103
CORE：48.5%　RELATED：16.5%　WEAK：8.7%　POLLUTION：26.2%
结论：CONTAMINATED（VALID 65.0%）
机器错误标记：ERROR3_概念污染｜ERROR4_行业代替主题｜ERROR5_名称误导(掌阅科技、佳云科技、旗天科技)
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 新华都 002264.SZ　当日 10.01%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
  2. 国旅联合 600358.SH　当日 10.00%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）
  3. 文投控股 600715.SH　当日 2.42%　dc_industry_board｜命中排除词 游戏
需要人工进一步确认（WEAK 9 只，取前 3）：
  1. 中原传媒　dc_industry_board｜行业「出版业」属主题特征行业，但主营无直接证据
  2. 流金科技　无主营文本（dc_industry_board）（数据缺口，需人工确认）
  3. 新媒股份　dc_industry_board｜行业「影视音像」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    新华传媒   600825.SH　CORE      当日 10.06%　dc_industry_board｜主营命中核心词 传媒
    天威视讯   002238.SZ　RELATED   当日 10.05%　dc_industry_board｜主营命中 广播电视
    新华都     002264.SZ　POLLUTION 当日 10.01%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    内蒙新华   603230.SH　CORE      当日 10.01%　dc_industry_board｜主营命中核心词 出版
    新华文轩   601811.SH　CORE      当日 10.00%　dc_industry_board｜主营命中核心词 出版
  [随机5]
    天下秀     600556.SH　POLLUTION 当日 -1.30%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    果麦文化   301052.SZ　RELATED   当日 0.00%　dc_industry_board｜主营命中 图书
    慈文传媒   002343.SZ　CORE      当日 -1.38%　dc_industry_board｜主营命中核心词 影视
    宣亚国际   300612.SZ　POLLUTION 当日 -0.41%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）
    北京文化   000802.SZ　WEAK      当日 0.51%　dc_industry_board｜行业「影视音像」属主题特征行业，但主营无直接证据
  [边缘5]
    川网传媒   300987.SZ　POLLUTION 当日 -1.51%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    蓝色光标   300058.SZ　POLLUTION 当日 -2.74%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）
    天娱数科   002354.SZ　POLLUTION 当日 -0.66%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    掌阅科技   603533.SH　POLLUTION 当日 -0.80%　dc_industry_board｜仅因宽口径行业「互联网」被纳入（行业代替主题）
    因赛集团   300781.SZ　POLLUTION 当日 -2.41%　dc_industry_board｜仅因宽口径行业「广告包装」被纳入（行业代替主题）

【最终人工确认表】
主题           今日Heat    周Heat    月Heat      N 纯度            今日状态         周状态            月状态              人工结论
银行                 94        20        67     41 PURE            NARROW           NEUTRAL           NEUTRAL             VALID
商业航天             84        44        83     17 CONTAMINATED    NARROW           NEUTRAL           NARROW              LOW_SAMPLE_REVIEW(INVALID（成分污染）)
煤炭                 82        32        23     25 PURE            NARROW           NEUTRAL           NEUTRAL             VALID
建筑装饰             81        45        44     99 PURE            NARROW           NEUTRAL           NEUTRAL             VALID
传媒                 80        72       100     67 CONTAMINATED    NARROW           NEUTRAL           NARROW              INVALID（成分污染）
电力                 78        26        29    193 CONTAMINATED    NARROW           NEUTRAL           NEUTRAL             INVALID（成分污染）
节能环保             74        55        61     92 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
机器人               73        77        40     68 CONTAMINATED    NEUTRAL          NARROW            NEUTRAL             INVALID（成分污染）
消费                 72        36        77     55 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
智能驾驶             68        59        37     19 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(INVALID（成分污染）)
钢铁                 65        16        26     36 ACCEPTABLE      NEUTRAL          NEUTRAL           NEUTRAL             VALID
医疗器械             64        95        53    109 CONTAMINATED    NEUTRAL          BROAD             NEUTRAL             INVALID（成分污染）
低空经济             55        66        60     12 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(INVALID（成分污染）)
化工                 50        51        57    190 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
高端材料             49        60        49     41 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
军工                 46        38        73     54 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
新能源车             45        45        27    112 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
游戏                 45        27        59     21 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
可控核聚变           40        39        71      9 CONTAMINATED    NEUTRAL          NEUTRAL           DIVERGENCE          LOW_SAMPLE_REVIEW(INVALID（成分污染）)
地产链               39        90        83     80 PURE            NEUTRAL          NARROW            NARROW              VALID
脑机接口             38        73        36      6 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(INVALID（成分污染）)
信创                 35        86        62     50 CONTAMINATED    NEUTRAL          NARROW            NEUTRAL             INVALID（成分污染）
量子计算             35        41        42      6 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
半导体               34        90        77    202 ACCEPTABLE      NEUTRAL          NARROW            NARROW              VALID
战略与小金属         28        20         8     40 CONTAMINATED    NEUTRAL          NEUTRAL           NEUTRAL             INVALID（成分污染）
AI算力               27        71        87     39 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
消费电子             25        68        79     52 CONTAMINATED    NEUTRAL          NEUTRAL           NARROW              INVALID（成分污染）
证券                 25        33        23     46 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
能源金属             21        15        14     15 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
工业金属             20         9        15     62 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
创新药               18       100        61     66 CONTAMINATED    NEUTRAL          BROAD             NEUTRAL             INVALID（成分污染）
黄金                 15        12        16     11 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)

【最终人工确认】
TODAY：暂无经人工确认的主题
WEEK：暂无经人工确认的主题
MONTH：银行

最重要观察：
  机器 TODAY TOP10 中有 7 个主题成分为 CONTAMINATED：商业航天、传媒、电力、节能环保、机器人、消费、智能驾驶。
  全部 32 个主题中 CONTAMINATED 20 个：映射池被宽口径行业板块稀释是系统性现象，不是个别错误（§十五 行业≠主题）。
  样本不足 20 的主题：量子计算、智能驾驶、低空经济、商业航天、脑机接口、可控核聚变、能源金属、黄金，其 Heat 高不代表形成广泛主题行情（§七）。
机器排名与人工判断存在明显偏差：
  TODAY：机器第一 银行（Heat 94.1）未通过人工确认 → 扩散不足（HOT_BUT_NARROW）；人工确认第一为 暂无。
  WEEK：机器第一 创新药（Heat 100.0）未通过人工确认 → 成分为 CONTAMINATED；人工确认第一为 暂无。
  MONTH：机器第一 传媒（Heat 100.0）未通过人工确认 → 成分为 CONTAMINATED；人工确认第一为 银行。
══════════════════════════════════════════════════════════════
