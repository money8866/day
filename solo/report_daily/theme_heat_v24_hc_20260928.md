══════════════════════════════════════════════════════════════
V2.4-HC 主题人工确认
交易日：20260928
══════════════════════════════════════════════════════════════
* 本层不重算 Heat、不改权重、不改机器排名；只做成分股验证 + 广度验证 + 样本可靠性验证。
* 四分类：CORE / RELATED / WEAK / POLLUTION；纯度分级：PURE / ACCEPTABLE / CONTAMINATED。
* 小样本门槛 N<20 → LOW_SAMPLE_REVIEW；允许输出「暂无经人工确认的最强主题」。

【主题定义自检（Q1）】
  本交易日机器覆盖主题 36 个；其中纯度 PURE 36、ACCEPTABLE 0、CONTAMINATED 0。
  主题定义本身不清晰 / 多产业混合者，按 §六 判 INVALID，列入文末「需要人工进一步确认」。

【TODAY】
机器第一：地产链
人工确认：电力
  原因：成分纯度 93%（POLLUTION 2%）；TODAY 广度 17.4%（VALID_BUT_NOT_BROAD）。
第二：信创
  状态：VALID_BUT_NOT_BROAD
第三：建筑装饰
  状态：VALID_BUT_NOT_BROAD

【WEEK】
机器第一：创新药
人工确认：消费
  原因：成分纯度 98%（POLLUTION 2%）；WEEK 广度 31.8%（WEEK_NARROW）。
第二：信创
  状态：WEEK_NARROW
第三：智能驾驶
  状态：WEEK_NARROW

【MONTH】
机器第一：传媒
人工确认：消费
  原因：成分纯度 98%（POLLUTION 2%）；MONTH 广度 34.3%（MONTH_LEADER_DRIVEN）。
第二：传媒（未通过人工确认）
  状态：MONTH_LEADER_DRIVEN
第三：创新药（未通过人工确认）
  状态：MONTH_LEADER_DRIVEN

【成分股质量检查】
──────────────────────────────────────────────────────────────
主题：地产链　N（映射成员）：87
CORE：77.0%　RELATED：14.9%　WEAK：4.6%　POLLUTION：3.4%
结论：PURE（VALID 92.0%）
机器错误标记：ERROR2_低Breadth第一
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 特发服务 300917.SZ　当日 1.37%　dc_industry_board｜仅因宽口径行业「房产服务」被纳入（行业代替主题）
  2. 中国国贸 600007.SH　当日 0.66%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
  3. 张江高科 600895.SH　当日 -3.12%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
需要人工进一步确认（WEAK 4 只，取前 3）：
  1. 中国武夷　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
  2. 香江控股　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
  3. 城投控股　dc_industry_board｜行业「区域地产」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    深物业A    000011.SZ　CORE      当日 10.00%　dc_industry_board｜主营命中核心词 房地产
    沙河股份   000014.SZ　CORE      当日 5.26%　dc_industry_board｜主营命中核心词 房地产
    滨江集团   002244.SZ　CORE      当日 3.37%　人工确认名单（core_company）
    招商积余   001914.SZ　RELATED   当日 2.14%　dc_industry_board｜主营命中 物业
    天健集团   000090.SZ　CORE      当日 1.71%　dc_industry_board｜主营命中核心词 房地产
  [随机5]
    金地集团   600383.SH　CORE      当日 -0.41%　人工确认名单（core_company）
    海南高速   000886.SZ　CORE      当日 -1.08%　stock_basic_industry｜主营命中核心词 房地产
    香江控股   600162.SH　WEAK      当日 0.92%　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
    亚通股份   600692.SH　CORE      当日 0.78%　dc_industry_board｜主营命中核心词 房地产
    华侨城A    000069.SZ　CORE      当日 1.09%　人工确认名单（core_company）
  [边缘5]
    中国国贸   600007.SH　POLLUTION 当日 0.66%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
    张江高科   600895.SH　POLLUTION 当日 -3.12%　dc_industry_board｜仅因宽口径行业「园区开发」被纳入（行业代替主题）
    特发服务   300917.SZ　POLLUTION 当日 1.37%　dc_industry_board｜仅因宽口径行业「房产服务」被纳入（行业代替主题）
    中国武夷   000797.SZ　WEAK      当日 -6.12%　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
    香江控股   600162.SH　WEAK      当日 0.92%　dc_industry_board｜行业「全国地产」属主题特征行业，但主营无直接证据
──────────────────────────────────────────────────────────────
主题：电力　N（映射成员）：264
CORE：73.5%　RELATED：19.3%　WEAK：5.7%　POLLUTION：1.5%
结论：PURE（VALID 92.8%）
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 惠天热电 000692.SZ　当日 0.00%　dc_industry_board｜命中排除词 供热
  2. 天壕能源 300332.SZ　当日 -0.41%　dc_industry_board｜命中排除词 天然气
  3. 长青集团 002616.SZ　当日 -0.83%　dc_industry_board｜仅因宽口径行业「环境保护」被纳入（行业代替主题）
需要人工进一步确认（WEAK 15 只，取前 3）：
  1. 凯发电气　dc_industry_board｜行业「电气设备」属主题特征行业，但主营无直接证据
  2. 华润新能源　无主营文本（dc_industry_board）（数据缺口，需人工确认）
  3. 众业达　dc_industry_board｜行业「电气设备」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    浙江新能   600032.SH　CORE      当日 10.07%　dc_industry_board｜主营命中核心词 发电
    吉鑫科技   601218.SH　CORE      当日 10.00%　dc_industry_board｜主营命中核心词 发电
    大连热电   600719.SH　RELATED   当日 6.09%　dc_industry_board｜主营命中 热电联产
    珈伟新能   300317.SZ　CORE      当日 5.28%　dc_industry_board｜主营命中核心词 光伏
    金雷股份   300443.SZ　CORE      当日 4.91%　dc_industry_board｜主营命中核心词 风电
  [随机5]
    东望时代   600052.SH　CORE      当日 1.92%　dc_industry_board｜主营命中核心词 水电
    昱能科技   688348.SH　CORE      当日 -2.30%　dc_industry_board｜主营命中核心词 电力
    川能动力   000155.SZ　CORE      当日 -1.36%　dc_industry_board｜主营命中核心词 发电
    坤博精工   920570.BJ　CORE      当日 -1.65%　ths_concept｜主营命中核心词 发电
    国电电力   600795.SH　CORE      当日 3.05%　core_company｜主营命中核心词 电力
  [边缘5]
    电投水电   600292.SH　POLLUTION 当日 -1.95%　dc_industry_board｜仅因宽口径行业「水力发电」被纳入（行业代替主题）
    长青集团   002616.SZ　POLLUTION 当日 -0.83%　dc_industry_board｜仅因宽口径行业「环境保护」被纳入（行业代替主题）
    惠天热电   000692.SZ　POLLUTION 当日 0.00%　dc_industry_board｜命中排除词 供热
    天壕能源   300332.SZ　POLLUTION 当日 -0.41%　dc_industry_board｜命中排除词 天然气
    凯发电气   300407.SZ　WEAK      当日 -1.70%　dc_industry_board｜行业「电气设备」属主题特征行业，但主营无直接证据
──────────────────────────────────────────────────────────────
主题：创新药　N（映射成员）：102
CORE：40.2%　RELATED：52.0%　WEAK：4.9%　POLLUTION：2.9%
结论：PURE（VALID 92.2%）
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 神奇制药 600613.SH　当日 0.48%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
  2. 西藏药业 600211.SH　当日 -1.43%　dc_industry_board｜命中排除词 医疗器械
  3. 哈药股份 600664.SH　当日 -9.95%　dc_industry_board｜命中排除词 医药商业
需要人工进一步确认（WEAK 5 只，取前 3）：
  1. 泰诺麦博　无主营文本（dc_industry_board）（数据缺口，需人工确认）
  2. 德展健康　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
  3. 北陆药业　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    丽珠集团   000513.SZ　RELATED   当日 10.01%　dc_industry_board｜主营命中 生物制品
    康希诺     688185.SH　RELATED   当日 5.99%　dc_industry_board｜主营命中 疫苗
    泰诺麦博   688806.SH　WEAK      当日 5.33%　无主营文本（dc_industry_board）（数据缺口，需人工确认）
    南模生物   688265.SH　CORE      当日 4.76%　dc_industry_board｜主营命中核心词 创新药
    康弘药业   002773.SZ　RELATED   当日 3.33%　dc_industry_board｜主营命中 生物制品
  [随机5]
    禾元生物   688765.SH　RELATED   当日 -1.83%　dc_industry_board｜主营命中 科研试剂
    昊帆生物   301393.SZ　RELATED   当日 -1.39%　dc_industry_board｜主营命中 多肽
    哈药股份   600664.SH　POLLUTION 当日 -9.95%　dc_industry_board｜命中排除词 医药商业
    科伦药业   002422.SZ　CORE      当日 -0.67%　人工确认名单（core_company）
    阿拉丁     688179.SH　RELATED   当日 -7.61%　stock_basic_industry｜主营命中 科研试剂
  [边缘5]
    西藏药业   600211.SH　POLLUTION 当日 -1.43%　dc_industry_board｜命中排除词 医疗器械
    哈药股份   600664.SH　POLLUTION 当日 -9.95%　dc_industry_board｜命中排除词 医药商业
    神奇制药   600613.SH　POLLUTION 当日 0.48%　dc_industry_board｜仅因宽口径行业「中成药」被纳入（行业代替主题）
    泰诺麦博   688806.SH　WEAK      当日 5.33%　无主营文本（dc_industry_board）（数据缺口，需人工确认）
    德展健康   000813.SZ　WEAK      当日 0.00%　dc_industry_board｜行业「化学制药」属主题特征行业，但主营无直接证据
──────────────────────────────────────────────────────────────
主题：消费　N（映射成员）：300
CORE：3.0%　RELATED：95.3%　WEAK：0.0%　POLLUTION：1.7%
结论：PURE（VALID 98.3%）
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
  1. 永顺泰 001338.SZ　当日 1.87%　dc_industry_board｜仅因宽口径行业「啤酒」被纳入（行业代替主题）
  2. 开创国际 600097.SH　当日 0.33%　dc_industry_board｜仅因宽口径行业「渔业」被纳入（行业代替主题）
  3. 珠免集团 600185.SH　当日 -0.85%　dc_industry_board｜仅因宽口径行业「旅游服务」被纳入（行业代替主题）
需要人工进一步确认（WEAK 0 只，取前 3）：
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    嘉益股份   301004.SZ　RELATED   当日 17.06%　dc_industry_board｜主营命中 食品
    东瑞股份   001201.SZ　RELATED   当日 10.03%　dc_industry_board｜主营命中 生猪
    会稽山     601579.SH　RELATED   当日 9.99%　dc_industry_board｜主营命中 黄酒
    古越龙山   600059.SH　RELATED   当日 6.64%　dc_industry_board｜主营命中 白酒
    宝立食品   603170.SH　RELATED   当日 6.25%　dc_industry_board｜主营命中 食品
  [随机5]
    广弘控股   000529.SZ　RELATED   当日 0.80%　dc_industry_board｜主营命中 食品
    晨光股份   603899.SH　RELATED   当日 -0.50%　dc_industry_board｜主营命中 文具
    美的集团   000333.SZ　CORE      当日 -0.79%　人工确认名单（leader_company）
    农产品     000061.SZ　RELATED   当日 -1.34%　dc_industry_board｜主营命中 连锁
    创业黑马   300688.SZ　RELATED   当日 -7.16%　dc_industry_board｜主营命中 培训
  [边缘5]
    苏美达     600710.SH　POLLUTION 当日 -0.97%　dc_industry_board｜仅因宽口径行业「商贸代理」被纳入（行业代替主题）
    永顺泰     001338.SZ　POLLUTION 当日 1.87%　dc_industry_board｜仅因宽口径行业「啤酒」被纳入（行业代替主题）
    嘉曼服饰   301276.SZ　POLLUTION 当日 -1.37%　dc_industry_board｜仅因宽口径行业「服饰」被纳入（行业代替主题）
    开创国际   600097.SH　POLLUTION 当日 0.33%　dc_industry_board｜仅因宽口径行业「渔业」被纳入（行业代替主题）
    珠免集团   600185.SH　POLLUTION 当日 -0.85%　dc_industry_board｜仅因宽口径行业「旅游服务」被纳入（行业代替主题）
──────────────────────────────────────────────────────────────
主题：传媒　N（映射成员）：79
CORE：64.6%　RELATED：35.4%　WEAK：0.0%　POLLUTION：0.0%
结论：PURE（VALID 100.0%）
主要污染股票（按当日涨幅排序，涨得越多越具误导性）：
需要人工进一步确认（WEAK 0 只，取前 3）：
成分股抽样（§三 每组 5 只）：
  [涨幅贡献Top5]
    新华传媒   600825.SH　CORE      当日 10.04%　dc_industry_board｜主营命中核心词 传媒
    博纳影业   001330.SZ　RELATED   当日 10.03%　dc_industry_board｜主营命中 电影
    中国科传   601858.SH　CORE      当日 3.59%　dc_industry_board｜主营命中核心词 出版
    国脉文化   600640.SH　RELATED   当日 2.63%　stock_basic_industry_alias｜主营命中 视频
    捷成股份   300182.SZ　CORE      当日 1.83%　dc_industry_board｜主营命中核心词 影视
  [随机5]
    易点天下   301171.SZ　CORE      当日 -3.94%　ths_concept｜主营命中核心词 广告
    儒意电影   002739.SZ　CORE      当日 -1.92%　人工确认名单（core_company）
    国脉文化   600640.SH　RELATED   当日 2.63%　stock_basic_industry_alias｜主营命中 视频
    流金科技   920021.BJ　CORE      当日 -2.67%　dc_industry_board｜主营命中核心词 广告
    兆讯传媒   301102.SZ　CORE      当日 -0.63%　stock_basic_industry_alias｜主营命中核心词 广告
  [边缘5]
    百纳千成   300291.SZ　RELATED   当日 -16.44%　dc_industry_board｜主营命中 电影
    内蒙新华   603230.SH　CORE      当日 -10.02%　dc_industry_board｜主营命中核心词 出版
    龙版传媒   605577.SH　CORE      当日 -9.99%　dc_industry_board｜主营命中核心词 出版
    天威视讯   002238.SZ　RELATED   当日 -9.96%　dc_industry_board｜主营命中 广播电视
    粤传媒     002181.SZ　CORE      当日 -9.29%　dc_industry_board｜主营命中核心词 广告

【最终人工确认表】
主题           今日Heat    周Heat    月Heat      N 纯度            今日状态         周状态            月状态              人工结论
地产链               96        91        81     80 PURE            NARROW           NARROW            NARROW              VALID
医疗器械             95        97        83    114 PURE            NARROW           NARROW            NARROW              VALID
创新药               89       100        89     94 PURE            NARROW           NARROW            NARROW              VALID
银行                 89        62        77     41 PURE            NARROW           NEUTRAL           NARROW              VALID
煤炭                 80        77        29     25 PURE            NARROW           NARROW            NEUTRAL             VALID
消费                 79        75        73    294 PURE            NARROW           NEUTRAL           NEUTRAL             VALID
节能环保             77        59        64    124 PURE            NARROW           NEUTRAL           NEUTRAL             VALID
电力                 72        37        49    245 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
信创                 70        73        57    169 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
建筑装饰             63        54        45    106 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
机器人               63        64        43     45 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
钢铁                 62        44        21     49 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
低空经济             60        71        65     14 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
化工                 59        66        49    294 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
传媒                 57        76       100     79 PURE            NEUTRAL          NARROW            NARROW              VALID
新能源车             56        34        30    136 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
军工                 54        40        84     86 PURE            NEUTRAL          NEUTRAL           NARROW              VALID
商业航天             52        41        81     16 PURE            NEUTRAL          NEUTRAL           NARROW              LOW_SAMPLE_REVIEW(VALID)
证券                 51        38        26     46 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
智能驾驶             49        70        41     23 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
游戏                 48        50        57     23 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
脑机接口             47        78        40      9 PURE            NEUTRAL          NARROW            NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
AI应用               44        41        57     16 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
高端材料             37        56        56    102 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
消费电子             36        47        67     94 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
工业金属             32        14         9     65 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
AI服务器             31        26        32      9 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
战略与小金属         30        21         8     51 PURE            NEUTRAL          NEUTRAL           NEUTRAL             VALID
算力运营             30        41        25     15 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
能源金属             28        16        13     16 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
量子计算             27        35        31      5 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
半导体               24        39        77    227 PURE            NEUTRAL          NEUTRAL           NARROW              VALID
可控核聚变           24        41        52     18 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
黄金                 24        13        15     11 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
液冷                 19        22        68     10 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)
CPO                  18        15        45     17 PURE            NEUTRAL          NEUTRAL           NEUTRAL             LOW_SAMPLE_REVIEW(VALID)

【最终人工确认】
TODAY：电力
WEEK：消费
MONTH：消费

最重要观察：
  机器 TODAY TOP10 中有 0 个主题成分为 CONTAMINATED：。
  全部 36 个主题中 CONTAMINATED 0 个：映射池被宽口径行业板块稀释是系统性现象，不是个别错误（§十五 行业≠主题）。
  样本不足 20 的主题：CPO、量子计算、液冷、AI服务器、AI应用、算力运营、商业航天、脑机接口、低空经济、能源金属、黄金、可控核聚变，其 Heat 高不代表形成广泛主题行情（§七）。
机器排名与人工判断存在明显偏差：
  TODAY：机器第一 地产链（Heat 96.3）未通过人工确认 → 扩散不足（HOT_BUT_NARROW）；人工确认第一为 电力。
  WEEK：机器第一 创新药（Heat 99.7）未通过人工确认 → 扩散不足（WEEK_NARROW）；人工确认第一为 消费。
  MONTH：机器第一 传媒（Heat 100.0）未通过人工确认 → 扩散不足（MONTH_LEADER_DRIVEN）；人工确认第一为 消费。
══════════════════════════════════════════════════════════════
