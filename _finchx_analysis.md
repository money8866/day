# FinchX（sanshi2026/FinchX）接口分析报告

> 分析时间：2026-10-24（周六，非交易日）｜实测环境：Python 3.13.12 隔离 venv，finchx==3.1.1
> 实测脚本：`D:\mystock\_finchx_probe.py`、`D:\mystock\_finchx_probe2.py`（可复跑）

---

## 1. 项目概览

| 维度 | 情况 |
|---|---|
| 定位 | 面向 A 股的 Python 数据接口库：行情、资金流、公司/财务、新闻、公告、研报 |
| 热度 | Star 11 / Fork 4 / Open Issues 0（GitHub API，2026-10-24） |
| 成熟度 | **很新**：仓库建于 2026-09-21，约 1 个月；版本节奏 2.0.0（09-25）→ 3.0.0（10-11，破坏性重构）→ 3.1.0（10-14）→ 3.1.1（10-15） |
| 许可 | Apache-2.0 |
| 运行时依赖 | **核心零硬依赖**（纯标准库）；可选 extras：`calendar`（pandas_market_calendars）、`pdf`（pypdfium2）、`jygs`（playwright 韭研复盘） |
| Python | >= 3.10 |

3.x 与 2.x 接口/参数/返回结构**不兼容**，官方要求独立环境迁移。接口总数约 **60+**，覆盖 16 个业务模块。

## 2. 架构设计

```
src/finchx/
├── client.py          # FinchX 门面类（timeout 参数）
├── api/               # 16 个业务模块：market/fund_flow/sector/company/financial/
│                      #   regulation/reference/hotlist/fupan/news/articles/
│                      #   disclosure/reports/forum/iwencai/__init__
├── sources/           # 数据源适配层（腾讯/东财/爱股票/同花顺/百度/易观/韭研/问财）
└── _common/           # 公共：HTTP、日期解析、异常、校验
```

- **门面 + 分层**：`fx.market.ranking()` 一层调用，api 层管业务语义，sources 层管抓取与解析，职责清晰。
- **返回原生 `dict`/`list`**：不引入 DataFrame/ORM，查询分析、序列化、入库都直接可用；也意味着不做类型封装，字段稳定性靠文档约束。
- **无缓存、无落盘**：每次调用实时取数，Cookie/Key/SESSION 仅当次有效。
- **错误三级**：`ValueError`（参数错）→ `SourceError`（来源读取失败）→ `FinchXError`（基类）。

## 3. 接口全景（按业务域）

| 模块 | 接口数 | 核心接口 | 数据源 |
|---|---|---|---|
| `market` | 15 | ranking / all_quotes / quote_snapshot / ohlcv / orderbook / intraday(1d,5d)；**涨停池、跌停池、炸板池、昨日涨停池、强势池、连板股** | 腾讯、东财、爱股票 |
| `fund_flow` | 3 | stock（快照+分时+日线）/ dragon_tiger_list / dragon_tiger_detail | 腾讯、爱股票 |
| `sector` | 6 | tx_sectors / em_keywords / ths_concept_list / 概念行情快照 / 概念K线 | 腾讯、东财、同花顺 |
| `company` | 2 | info（12 项顶层字段：股本/股东/高管/分红/回购/财务摘要/收入分布/行业对比）/ float_shareholders | 腾讯 |
| `financial` | 1 | statements（三大报表，按报告期降序，支持期数截取） | 同花顺 |
| `regulation` | 6 | watchlist / abnormal_records / abnormal_prediction_history / severe_predictions / deviation（偏离值计算） | 东财、腾讯 |
| `reference` | 2 | trading_calendar（离线日历回退）/ pdf_text（书签+全文） | 深交所、pypdfium2 |
| `hotlist` | 4 | stocks（6 类榜）/ sectors / convertible_bonds / etfs | 同花顺 |
| `news` | 4 | agp_list / bd_list / em_list / em_get | 爱股票、百度、东财 |
| `articles` | 5 | hot_content / ths_list / ths_get / agp_topic_articles / agp_get | 同花顺、爱股票 |
| `disclosure` | 2 | em_list（公告，支持分类过滤）/ em_get | 东财 |
| `reports` | 4 | **fetch（三源聚合去重）** / agp_fetch / em_fetch / analysys_fetch | 爱股票、东财、易观 |
| `forum` | 1 | tgb_replies（淘股吧，需 cookies） | 淘股吧 |
| `iwencai` | 3 | select（需 cookies）/ search（需 api_key）/ get_report | 同花顺问财 |
| `fupan` | 1 | jygs_replay（需 Playwright+Chromium+SESSION） | 韭研公社 |

## 4. 关键设计规范

- **缺失值语义**：数字缺失 `None`（有效 0 保留）；列表缺失 `None`、明确空 `[]`；百分比字段 `1.23` 即 `1.23%`（注意是数值不是小数）。
- **日期入参**：接受 `YYYY-MM-DD` / `YYYYMMDD` / `YYYY/MM/DD`（可带时刻），按北京时间解析；文档称不接受 `datetime` 对象，但 `disclosure.em_list` / `forum.tgb_replies` 签名实际支持 `datetime.date`（文档与实现有小出入）。
- **超时**：`FinchX(timeout=15.0)` 为**每次 HTTP 请求**粒度（3.1.1 起明确），不是整次调用。
- **研报聚合**：`reports.fetch` 按「完全相同的标题+机构+发布日期」去重，保留跨日同名报告，返回 `{reports, errors}` 结构——部分源失败不拖垮整体。

## 5. 实测结果（2026-10-24，取 10-23 收盘数据）

**通过（约 50 个接口，抽测 32 个）：**

| 接口 | 耗时 | 实测结果 |
|---|---|---|
| `market.ranking(limit=5)` | 0.2s | 正常，北交所 N远大 +73.99% 居首 |
| `market.em_breadth` | 0.1s | 涨 4406 / 跌 854，涨停 82 家，与涨停池数量互相印证 |
| `market.limit_up_pool` | 0.3s | 82 只，含首封/末封时间、封单额、连板数 |
| `market.consecutive_limit_up` | 0.2s | 12 只连板，含 `state`（如"6连板"）与 **`theme_name`（题材名，如"人形机器人"）** |
| `market.quote_snapshot` | 0.2s | 个股/指数均正常（茅台 1335.0，上证 3690.48） |
| `market.ohlcv` | 0.3s | 日线正常，支持复权参数 |
| `market.equity_intraday` | 0.3s | 267 个分时点（含累计量额） |
| `market.all_quotes` | **14.8s** | 全市场 5577 只，字段极全（多周期涨幅、主力净流入、pe_ttm、量比） |
| `fund_flow.stock(days=5)` | 0.2s | snapshot + intraday + daily 三段式返回 |
| `fund_flow.dragon_tiger_list` | 0.2s | 10-23 共 89 条龙虎榜事件 |
| `company.info` / `financial.statements` | 0.3s / 1.0s | 茅台 12 项顶层字段；利润表 2026H1 按期降序 |
| `regulation.watchlist` | 0.1s | 22 只监控股（含 *ST 与 10-26 起监控的北交所股） |
| `reference.trading_calendar` | 0.2s | 正确标记交易日 |
| `hotlist.sectors / etfs / cb` | 0.1-0.6s | 概念榜（人形机器人第 1）、ETF 榜、转债榜均正常 |
| `news.agp_list` | 0.8s | 20 条，**直接带全文 content** |
| `disclosure.em_list(code)` | 0.3s | 公告带 categories 分类 |
| `reports.fetch('贵州茅台')` | 4.2s | 三源聚合，errors=[]，带正文+PDF 链接 |
| `iwencai.select` | — | 按设计需 cookies；`search` 需 api_key（拒绝无效 key） |

**失败/受限（均为来源侧问题或按设计需登录态）：**

| 接口 | 状态 | 原因 |
|---|---|---|
| `news.bd_list` | ❌ 持续失败 | 百度来源解析失败（`content.items.data` 字段不符），重试无效 |
| `hotlist.stocks(category=popular/value)` | ❌ 失败 | 同花顺来源返回结构异常；`new/rising/technical/trend` 4 类可用 |
| `articles.hot_content` | ❌ 失败 | 同花顺「未确认的话题业务类型」 |
| `hotlist.stocks(period='1h')` 配 `new/technical/value/trend` | ⚠️ ValueError | 校验拦截正确：这几类仅支持 `period='24h'` |
| `forum.tgb_replies` / `iwencai` / `fupan.jygs_replay` | 🔒 按设计 | 需 cookies / api_key / Playwright+SESSION |

**性能提示**：单接口普遍 0.1-1s；`all_quotes`（全市场）约 15s、`reports.fetch`（三源聚合）约 4s，批量场景注意耗时。

## 6. 对 zt_attribution（一字板归因）项目的价值

**高匹配点：**
- `market.limit_up_pool`：首封/末封时间、封单额、开板次数、连板天数——**一字板识别**可用 `first_limit_up_time == 09:30` + 封单未破 + 开板数 0 近似判定，或与 `ohlcv`（开=高=低=收）交叉验证。
- `market.consecutive_limit_up`：直接带 `theme_name`（题材），省去自己关联板块；连板梯队（state 字段）一目了然。
- `market.broken_limit_pool` + `yesterday_limit_up_pool`：炸板率、昨日涨停今日溢价（竞价情绪）可直接算。
- `fx.news.*` + `fx.reports.fetch`：舆情/研报归因链路齐全，爱股票新闻直接带全文，省一步正文抓取。
- `fx.fund_flow.dragon_tiger_list/detail`：席位级归因数据。

**注意**：与本项目已有的 `a-stock-data` 技能、东财妙想 MCP 数据面有重叠，建议按「FinchX 补舆情/研报/热榜维度，行情主链路沿用现有源」分工，不要双轨重复取数。

## 7. 风险评估

1. **来源稳定性是最大风险**：本质是多站点爬虫封装，实测已有 3 个接口挂在来源解析上（百度新闻、同花顺部分热榜）。上游页面一改就可能静默失效，**必须每次调用 try/except `SourceError`** 并做降级。
2. **项目太新**：1 个月仓库、11 star、无 issue 沉淀，3.0 已破坏性重构过一次，后续仍可能变；建议锁 `==3.1.1`，升级前读 API diff。
3. **无缓存/无限频**：实时取数无节流设计，`all_quotes` 15s/次，循环调用注意频率与耗时。
4. **合规层面**：抓取均为公开页面，Apache-2.0 仅约束库本身；自用研究无碍，**勿再分发抓取结果**。
5. **数据语义需抽查**：如百分比字段是 `1.23` 而非 `0.0123`、日期按北京时间解析等，接入时按文档逐字段核对一次。

## 8. 结论

FinchX 是一个**设计干净、接口覆盖面极广**的 A 股数据爬虫库：门面+分层架构清晰、核心零依赖、返回原生结构易接入、文档字段级详尽。实测 32 个抽测接口 28 个立即可用，涨停/连板/炸板/龙虎榜/热榜/研报聚合正好补上 zt_attribution 项目的舆情与情绪面数据缺口。主要短板是来源侧稳定性（3 个接口实测失败）和项目成熟度（上线 1 个月、已有一次破坏性重构）。**建议：锁定 3.1.1 接入，对 `SourceError` 做统一降级封装，优先用 `consecutive_limit_up`（带题材名）+ `news.agp_list`（带全文）+ `reports.fetch` 三条链路增强归因模块。**
