# Automation Memory — 盘后市场分析微信推送

## 用途
仅记录**执行状态**（是否成功、报错要点、踩坑），**不记录报告正文/行情结论**。
股市定时任务的内容不得进入 `journal/` 与 `.workbuddy/memory/` 记忆体系，
否则会被 22:00「工作日志汇总」邮件误当作「今日记录」推给用户。

## 执行记录

### 2026-11-09 (周一)
- 单次执行既定命令即成功：分析成功 → token 自检通过（入站消息约 3.99 小时前，在 8 小时活跃窗口内，未触发刷新）→ 微信推送 HTTP 200「发送成功 / 已主动推送」。
- 未重跑、未补推；另用 `--dry-run --no-run --date 20261109` 只读校验推送正文（已确认该分支仅打印、不推送、不重跑分析），供回复中概述结论。
- 本次未报配额/token 异常；11-06 记录的「下行 8 条接近上限」提示未复现，继续留意。

### 2026-11-06 (周五)
- 单次执行既定命令即成功：分析成功 → token 自检通过（入站消息约 6.22 小时前，在 8 小时活跃窗口内，未触发刷新）→ 微信推送 HTTP 200「发送成功 / 已主动推送」。
- 未重跑、未补推。
- ⚠️ 推送返回附带提示：本 token 已下行 8 条、接近配额上限，建议换新 token（手机给 bot 发任意消息后调 acquire_token / --acquire 刷新）。本次推送未受影响，后续继续留意。

### 2026-11-05 (周四)
- 单次执行既定命令即成功：分析成功 → token 自检通过（入站消息约 4.24 小时前，在 8 小时活跃窗口内，未触发刷新）→ 微信推送 HTTP 200「发送成功 / 已主动推送」。
- 未重跑、未补推；另用 `--dry-run --no-run --date 20261105` 只读校验推送正文（不推送、不重跑分析）。
- 本次未报配额/token 异常，昨日记录的「下行 10 条接近上限」提示未复现，继续留意。

### 2026-11-04 (周三)
- 用户手动触发「重试」（本日 memory 此前无记录）。单次执行同一条命令即成功：分析成功 → token 自检通过 → 微信推送 HTTP 200「发送成功 / 已主动推送」。
- 未重复执行、未额外补推。另用 `--dry-run --no-run --date 20261104` 只读校验推送正文以确认结论，不产生推送。
- ⚠️ 推送返回附带提示：本 token 已下行 10 条、接近配额上限，建议换新 token（在手机给 bot 发任意消息后调 acquire_token / --acquire 刷新）。本次推送未受影响，但后续几日需留意。

### 2026-11-03 (周二)
- 执行命令同上，单次执行成功：分析成功 → token 自检通过（已缓存约 0.9 天）→ 微信推送 HTTP 200 成功。
- 产物落在 `cache_backbone_tushare/`：`market_regime_v10_20261103.txt`、`market_regime_v101_20261103.txt`、`market_analysis_20261103.txt`、`market_analysis_20261103.csv`、`position_engine_v9_20261103.txt`。
- 脚本一次通过，无需重跑。

### 2026-11-02 (周一)
- 首次执行（此前无 memory 文件，自动化为新建）。
- 执行命令：`cd /d/mystock/solo && "C:/Users/kongx/AppData/Local/Python/bin/python" _daily_push_wechat.py`
- 结果：market_analysis.py 运行成功 → token 自检通过（缓存约 6.0 天，未达 7 天刷新阈值）→ 微信推送 HTTP 200 成功。
- 产物落在 `cache_backbone_tushare/`：`market_regime_v10_20261102.txt`、`market_regime_v101_20261102.txt`、`market_analysis_20261102.txt`、`position_engine_v9_20261102.txt`。
- 注意：`market_regime_v10_*.txt` 是脚本 `resolve_trade_date()` 的日期锚点文件，新增报告后勿删。

## 运维要点
- 脚本自带交易日判断、重试与失败降级告警；**单次失败不要手工重跑**，避免同一交易日重复推送。
- 推送失败且报 token 相关错误时，调用 `mcp__wechat-clawbot-push__resync_credentials` 自愈后重试一次。
- 校验推送正文用 `--dry-run --no-run --date YYYYMMDD`（只打印，不推送、不重跑分析）。
