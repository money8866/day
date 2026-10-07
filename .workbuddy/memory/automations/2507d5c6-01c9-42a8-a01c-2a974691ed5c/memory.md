# 明日日程查询自动化 - 执行记录

## 环境要点
- 必须用系统 Python：`C:\Users\kongx\AppData\Local\Python\bin\python.exe`（其他环境无 requests）
- **主脚本已改为**：`D:\mystock\_push_agenda_mail.py`（2026-09-30 起；内部查日程 + 发邮件）
  - 旧的 `D:\mystock\_tomorrow_agenda.py` 仍保留，只打印不推送，被新脚本 import 复用。
  - `--dry` 参数：只输出 `_agenda_preview.html`，不发邮件，便于调样式。
- **邮件通道**：Agent Mail CLI `C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd`，
  收件人 `stock1975@qq.com`（与 hvt_bull/hve_v1/signal_review 一致）。样式复用 hve_v1 的 22px 卡片式 CSS。
- **坑**：Git Bash 下传 `D:\mystock\_xxx.py` 会被路径转换破坏（变成 `D:\mystock\mystock_xxx.py`），
  导致 "can't open file" 错误。改用 PowerShell 工具执行正常。

## 邮件通道的坑（2026-09-30 实测）
- **CLI OAuth 授权会过期**：报 `{"ok":false,"error":{"type":"auth","message":"authorization required"}}` 时，
  执行 `agently-cli auth login`，会打印一条 `https://agent.qq.com/page/oauth?oauth_type=device&user_code=...` 链接，
  需用户点击授权；成功输出 `OK: 认证成功`。token 有效期约 1 小时（`auth status` 可查 expires_at）。
- **MCP 工具 `agent-mail` 与 CLI 不是同一账号**：CLI 用 `kongxiangpu9703@agent.qq.com`（workspace=workbuddy），
  MCP 读到的是 `kongxp@agent.qq.com`（另一 alias）。用 MCP ListMessages 验证 CLI 发出的邮件会「查不到」，
  别据此误判发送失败。**验证要用 `agently-cli message +list --dir sent --limit 3`**。
- 发送成功返回 `{"ok": true, "data": {"queued": true}}`，只是入队；落盘到 sent 目录有几十秒延迟。

## 执行历史
- 2026-09-29 23:52：查询 2026-09-30（周三）日程，结果为空（明日暂无安排）。执行方式：PowerShell + 系统 Python。
- 2026-09-30 22:05：查询 2026-10-01（周四）日程，结果为空。执行成功（exit 0）。
  - **新坑（编码）**：PowerShell 直接执行时 stdout 不会回显到工具输出，必须重定向到文件再 Read。
    且 PS 5.1 默认用 GBK 解码 Python 的 UTF-8 输出再编码为 UTF-8，会导致双重编码乱码。
    解决：执行前先 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` 并设 `$env:PYTHONIOENCODING="utf-8"`、
    `python -X utf8`，再 `| Out-File -Encoding utf8`。
- 2026-09-30 23:20：**用户要求本任务结果改为邮件推送**。已完成改造并实测：
  - 新建 `_push_agenda_mail.py`（查日程 → 渲染 HTML → Agent Mail CLI 发送），自动化 prompt 已同步更新。
  - 实测链路通：邮件「📅 明日行程提醒 10-01 周四」已进 sent（created_at 2026-09-30T15:23:20Z）。
  - 期间 CLI token 过期，执行过一次 `auth login` 并完成授权。
- 2026-10-01 22:01：查询 2026-10-02（周五）日程，命中 1 项（18:00 聚餐），邮件推送成功。
  exit 0；sent 目录复核确认 subject「📅 明日行程提醒 10-02 周五」created_at 2026-10-01T14:01:23Z。
  token 仍有效（未触发 auth login）。执行方式沿用 PowerShell + 编码三件套 + Out-File utf8 重定向。
- 2026-10-02 22:01：查询 2026-10-03（周六）日程，结果为空，邮件推送成功。
  exit 0；sent 复核 subject「📅 明日行程提醒 10-03 周六」created_at 2026-10-02T14:01:15Z。
  token 仍有效，无需 auth login。流程已稳定，无新坑。
- 2026-10-03 22:00：查询 2026-10-04（周日）日程，命中 1 项（09:20 出发送儿子去机场，家庭日历），
  邮件推送成功。exit 0；sent 复核 subject「📅 明日行程提醒 10-04 周日」created_at 2026-10-03T14:01:11Z、
  收件人 stock1975@qq.com。token 仍有效，无需 auth login，流程无变化。
  注：`message +list` 的 tip 行会被 PS 当 stderr 报 RemoteException，但 JSON 结果仍正常落盘，不影响判断。
- 2026-10-05 22:00：查询 2026-10-06（周二）日程，结果为空（明日暂无安排），邮件推送成功。
  exit 0；sent 复核 subject「📅 明日行程提醒 10-06 周二」created_at 2026-10-05T14:00:59Z、
  收件人 stock1975@qq.com。token 仍有效，无需 auth login，流程无变化。（list 用 2>$null 屏蔽 tip 行，输出更干净。）
- 2026-10-06 22:01：查询 2026-10-07（周三）日程，结果为空（明日暂无安排），邮件推送成功。
  exit 0；sent 复核 subject「📅 明日行程提醒 10-07 周三」created_at 2026-10-06T14:01:24Z、
  收件人 stock1975@qq.com。token 仍有效，无需 auth login，流程无变化。
- 2026-10-04 22:00：查询 2026-10-05（周一）日程，命中 1 项（18:00 永加强海鲜楼晚餐（202包厢），📍龙湾区永中西路1111号），
  邮件推送成功。exit 0；sent 复核 subject「📅 明日行程提醒 10-05 周一」created_at 2026-10-04T14:01:12Z、
  收件人 stock1975@qq.com。token 仍有效，无需 auth login，流程无变化。（加分：list 时用 2>$null 屏蔽 tip 行更干净。）
