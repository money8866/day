# ZZ2000 每日仓位信号报告 · 自动化执行摘要

> 仅记录高层执行结果供后续运行参考；不放报告正文，不写用户日志/记事体系。

## 2026-11-11（本次）
- 主命令 `python -X utf8 -m zz2000.daily --push` 用 `C:/Users/kongx/AppData/Local/Python/bin/python.exe` 执行成功，exit 0。
- RESULT JSON：`mail_sent=true`（Agent Mail CLI `{"ok":true,"data":{"queued":true}}`），脚本自身投递成功；未重复发信、未走 mcp 兜底。
- 数据时点由 20261109 推进到 **20261110**（今日 20261111 为交易日，17:20 当日收盘数据未落库；报告已标注「本报告是 20261110 的收盘信号，不是 20261111 的行情」）。
- **信号发生变化**（`signal_unchanged=false`）：动作仍 HOLD，但目标敞口 5%→**10%**，delta **+5pp**；底仓 5%、趋势仓 0→5%、波段 0%，基线 `live`，`unknowns=[]`。原因：系统性风险解除（指数20日 -7.29% > -8%）、情绪过滤四项全 PASS（上涨占比 76.7%、涨停90/跌停1、昨涨停次日 +4.91%）、收盘价 1.286 站上 MA20 1.277；因命中「单日金叉/追涨抑制」需次日确认，故不执行 BUY、维持 HOLD。
- ETF `563300.SH` 收盘 1.286 元（+2.72%），成交额 8.68 亿；指数 2804.55（+2.45%）；均线仍空头排列（MA20/60/120 = 1.277/1.380/1.437）。
- 产物：`zz2000/reports/ZZ2000_Daily_20261110.md` / `.html`；HTML 校验通过（20px 基准、无外链 CSS/JS、红涨绿跌、含免责声明与「信号不变」字段）。台账 5 个交易日，未报告超额。

## 2026-11-10（历史）
- 主命令 `python -X utf8 -m zz2000.daily --push` 用 `C:/Users/kongx/AppData/Local/Python/bin/python.exe` 执行成功，exit 0。
- RESULT JSON：`mail_sent=true`（Agent Mail CLI `{"ok":true,"data":{"queued":true}}`），脚本投递成功，未重复发信，未走 mcp 兜底。
- 数据时点由 20261106 推进到 **20261109**（今日 20261110 为交易日，17:20 当日收盘数据未落库；报告已标注「本报告是 20261109 的收盘信号，不是 20261110 的行情」）。
- 信号：HOLD，目标敞口 5%，delta 0.0，`signal_unchanged=true`，基线 `live`，`unknowns=[]`。
- ETF `563300.SH` 收盘 1.252 元（+0.32%），指数 2737.41（+0.68%）；均线空头排列、系统性风险（指数20日 -8.78%<=-8%）触发 → 底仓 5%、趋势/波段 0%；情绪过滤「上涨占比 40.2%<45%」FAIL。
- 产物：`zz2000/reports/ZZ2000_Daily_20261109.md` / `.html`；HTML 校验通过（20px 基准、无外链 CSS/JS、红涨绿跌、含免责声明与「信号不变」）。台账 4 个交易日，未报告超额。

## 2026-11-09（历史）
- 主命令 `python -X utf8 -m zz2000.daily --push` 用 `C:/Users/kongx/AppData/Local/Python/bin/python.exe` 执行成功，exit 0。
- RESULT JSON：`mail_sent=true`（Agent Mail CLI `{"ok":true,"data":{"queued":true}}`），脚本投递成功，未重复发信，未走 mcp 兜底。
- 数据时点由 20261105 推进到 **20261106**（今日 20261109 为交易日，当日收盘数据未落库；报告已标注「本报告是 20261106 的收盘信号，不是 20261109 的行情」）。
- 信号：HOLD，目标敞口 5%，delta 0.0，`signal_unchanged=true`，基线 `live`，`unknowns=[]`。
- ETF `563300.SH` 收盘 1.248 元（-2.50%），指数 2718.86（-2.39%）；均线空头排列，系统性风险（上涨占比 13.6%<20%、指数20日 -11.30%<=-8%）触发 → 底仓 5%、趋势/波段 0%。
- 产物：`zz2000/reports/ZZ2000_Daily_20261106.md` / `.html`；HTML 校验通过（20px 基准、无外链 CSS/JS、红涨绿跌、含免责声明与「信号不变」）。台账 3 个交易日，未报告超额。

## 2026-11-06（历史）
- 主命令 `python -X utf8 -m zz2000.daily --push` 用 `C:/Users/kongx/AppData/Local/Python/bin/python.exe` 执行成功，exit 0。
- RESULT JSON：`mail_sent=true`（Agent Mail CLI `{"ok":true,"data":{"queued":true}}`），脚本投递成功，未重复发信，未走 mcp 兜底。
- 数据时点由上次的 20261104 推进到 **20261105**（今日 20261106 为交易日，当日收盘数据未落库；报告已标注「本报告是 20261105 的收盘信号，不是 20261106 的行情」）。
- 信号：HOLD，目标敞口 5%，delta 0.0，`signal_unchanged=true`，基线 `live`，`unknowns=[]`。
- ETF `563300.SH` 收盘 1.280 元（+2.65%），指数 2785.55（+2.66%）；均线空头排列、系统性风险（指数20日跌幅 -9.09%）触发 → 底仓 5%、趋势/波段 0%。
- 产物：`zz2000/reports/ZZ2000_Daily_20261105.md` / `.html`；HTML 合规校验通过（20px 基准、无外链 CSS/JS、红涨绿跌、含免责声明与「信号不变」）。台账已记录 2 个交易日，未报告超额。

## 2026-11-05（历史）
- 主命令 `python -X utf8 -m zz2000.daily --push` 用 `C:/Users/kongx/AppData/Local/Python/bin/python.exe`（3.14.4）执行成功，exit 0。
- RESULT JSON：`mail_sent=true`（Agent Mail CLI 返回 `{"ok":true,"data":{"queued":true}}`），脚本投递成功，未重复发信。
- 数据时点 `20261104`（今日 `20261105` 为交易日，但当日收盘数据未落库；报告明确标注「本报告是 20261104 的收盘信号，不是 20261105 的行情」）。
- 信号：HOLD，目标敞口 5%，delta 0.0，`signal_unchanged=true`，基线 `rule_replay`，`unknowns=[]`（无数据缺口；fund_div=NO_DIV_RECORD 为口径标注，非 UNKNOWN）。
- ETF `563300.SH` 收盘 1.247 元（+3.57%），指数 2713.36（+3.41%）；系统性风险因指数20日跌幅 -12.68% 触发，底仓压至 5%。
- 产物：`zz2000/reports/ZZ2000_Daily_20261104.md` / `.html`（16:55 重写）；脚本自维护 `zz2000/cache/` 下 db、state.json、benchmark_ledger.csv。
- 注意：数据仍停留在 20261104，若次日数据源补齐 20261105 收盘，报告会自动切到新日期。

## 历史基线（供参考）
- 2026-11-05 08:08 首次运行生成 20261104 报告（HOLD/5%），state.json 中 20261103 为 rule_replay(5%)、20261104 为 live(5%)。台账只有 1 个交易日，未报告超额。
