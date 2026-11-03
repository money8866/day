# 自动化执行记录 · 盘中动态分析并推送微信

## 2026-11-03 13:35 (首次执行)
- 命令：`C:\Users\kongx\AppData\Local\Python\bin\python.exe -u _intraday_monitor.py --slot 1330`（工作目录 D:\mystock\solo）
- 执行结果：脚本正常跑通，输出分析结果，无报错。
- 数据落盘：`_bt_out\realtime_20261103.csv`（5571 只，停牌 2）、`_bt_out\etf_realtime_20261103.csv`（70 只）
- 推送：经 wechat-clawbot-push MCP 发送成功（HTTP 200）。推送前先调 `bridge_status` 自检：token 有效（age 0.8 天，near_expiry=false）。
- 结论要点：广度 31.8%（1695↑/3643↓）、涨停 46/跌停 10、半日成交 12221 亿（预估全日 18924 亿）、5日均 44.9% vs 20日均 41.0%（+3.9pp 改善）；市场广度通过、短线赚钱效应未过、主线食品饮料；状态 S4、建议仓位 15%。

### 可复用经验
- 消息模板中「模块判定」除市场广度外，脚本还会输出「短线赚钱效应」判定，本轮一并保留进推送内容，信息更完整。
- 推送前务必先 `bridge_status` 自检 token，避免 `push_wechat_message` 因 token 失效失败。
- 记忆目录首次运行时不存在，需先 mkdir。
