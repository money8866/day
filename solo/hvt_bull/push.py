# -*- coding: utf-8 -*-
"""HVT-BULL AI 自然语言复盘 + 邮件推送

流程：读 report_daily/hvt_bull_report_{date}.md
     → DeepSeek 生成自然语言复盘（移动端友好）
     → Agent Mail CLI 邮件推送（HTML 正文 22px）
     → 落盘 report_daily/hvt_bull_ai_{date}.md
"""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_AI_SYSTEM_PROMPT = """你是资深A股操盘手，正在给普通散户（不懂量化术语）写每日复盘。

HVT-BULL 是一套"历史天量 + 缩量锁筹 + 二次突破"的牛股捕捉系统。
邮件里已经有一张「明日决策速览表」（由代码自动生成，逐只列明 动作/关键价/买区/止损）。
你的任务是围绕这张表写**白话解读**，把术语翻译成人话，**不要重复抄表**。

术语对照（写的时候一律用右边的大白话）：
- 第一梯队 / PRIMARY_BUY = 最可信的买点，数量很少（宁缺毋滥）
- T20_ROCKET_WATCH / BREAKOUT_READY = 有潜力但还没到买点，只能观察
- 突破回踩 GOOD = 突破后缩量回踩不破，是"第二次上车"机会
- RIGHT_TAIL的 HOLD / TRIMMING / EXIT = 已在主升浪里的持仓，分别对应 继续拿 / 分批卖 / 清仓
- FAILED / DISTRIBUTION / EXIT = 结构走坏，只能回避，不要抄底
- WAIT_CONFIRM = 条件差一口气，等确认后再动
- NO_CHASE = 涨太多或离买点太远，追进去不划算，只能等回落
- 触发价（entry_trigger）= 突破确认位：**现价已在它上方时必须说"已突破 X"，绝不能说"过 X 才买"**

请输出（250~550字，**纯 Markdown 列表**，禁止首行缩进，多用短句，适合手机阅读）：
1. **今日一句话**：机会多还是风险多，明天该进攻还是防守
2. **能买的**：速览表里动作是"可买 / 确认后买 / 回踩买点"的股票，逐只说明——为什么买、大概什么价位买、跌到哪里必须止损（必须带完整代码+加粗名称）
3. **手里有的怎么处理**：速览表里"继续持有 / 分批减仓 / 清仓离场"的股票，逐只给一句话动作
4. **要回避的**：风险名单多少只、最该警惕的 2~3 只，提醒不要抄底
5. **明日一句话清单**：3~6 条，每条只讲一个动作（如"上港集团 放量过 5.64 元才买；不破 MA10 就继续拿"）

格式要求：**不要写任何 # 标题**（邮件已有标题），就用上面 1~5 的编号列表；
子条目用短横线缩进列表；不要出现"报告原文""根据报告"这类话；
个股一律写成 **名称**（完整代码），例如 **上港集团**（600018.SH），名称加粗、代码不加粗；
说价格要带单位语义（如"突破 5.64 元""跌破 12.85 元"）；不要堆砌指标（ENTRY/FE/RS20/供给吸收 等一律翻译成人话）。"""


def _load_env():
    """加载 d:\\mystock\\config\\.env（缺省回退 solo/.env）"""
    try:
        from dotenv import load_dotenv
        env_path = r'd:\mystock\config\.env'
        if not os.path.exists(env_path):
            env_path = os.path.join(BASE_DIR, '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except Exception:
        pass


def _report_path(trade_date: str) -> str:
    out_dir = os.path.join(BASE_DIR, 'report_daily')
    return os.path.join(out_dir, f'hvt_bull_report_{trade_date}.md')


def _read_report(trade_date: str) -> str:
    path = _report_path(trade_date)
    if not os.path.exists(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read()


def summarize_with_deepseek(md_text: str, trade_date: str) -> str:
    """DeepSeek 生成自然语言复盘；失败返回空串（由调用方走降级）"""
    import requests
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        print('[HVT-PUSH] 未配置 DEEPSEEK_API_KEY，跳过AI总结')
        return ''
    try:
        url = 'https://api.deepseek.com/v1/chat/completions'
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {api_key}'}
        facts_note = _trigger_facts_note(_load_trigger_facts(trade_date))
        user_content = (f'请复盘 {trade_date} 的 HVT-BULL 报告：\n\n'
                        + (f'{facts_note}\n\n' if facts_note else '')
                        + md_text)
        messages = [
            {'role': 'system', 'content': _AI_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_content},
        ]
        data = {'model': 'deepseek-chat', 'messages': messages,
                'temperature': 0.3, 'max_tokens': 1024}
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        text = result['choices'][0]['message']['content'].strip()
        return text[:3000]
    except Exception as e:
        print(f'[HVT-PUSH] AI总结失败: {e}')
        return ''


# ---- 邮件推送（Agent Mail CLI；20260925 起替代 Server酱微信推送，微信通道已移除） ----
# 注意：npm 生成的 .cmd shim 在本机失效（报 "The system cannot find the path specified."，
# 同族的 lark-cli/mcporter shim 同样失效），故优先以 node 直调包内 run.js，shim 仅作兜底。
MAIL_RUN_JS = (r'C:\Users\kongx\AppData\Roaming\npm\node_modules'
               r'\@tencent-qqmail\agently-cli\scripts\run.js')
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# 邮件 HTML 样式（移动端优先：22px 大字、卡片式、宽表可横向滑动）
_MAIL_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#eef2f7; -webkit-text-size-adjust:100%;
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
       font-size:22px; line-height:1.8; color:#1f2937; }
.wrap { padding:14px; }
.hd { background:#1677ff; border-radius:14px; padding:18px 16px; margin-bottom:14px; color:#fff; }
.hd .t { font-size:26px; font-weight:700; line-height:1.45; }
.hd .s { font-size:18px; opacity:.92; margin-top:6px; }
.card { background:#fff; border-radius:14px; padding:18px 16px 8px; box-shadow:0 1px 4px rgba(15,23,42,.08); }
.ft { text-align:center; color:#94a3b8; font-size:17px; line-height:1.7; padding:14px 8px 4px; }
h1,h2,h3,h4 { margin:22px 0 12px; line-height:1.45; font-weight:700; color:#0f172a; }
h1 { font-size:26px; padding-bottom:10px; border-bottom:3px solid #1677ff; }
h2 { font-size:24px; padding-left:12px; border-left:7px solid #1677ff; }
h3 { font-size:22px; color:#334155; }
h4 { font-size:22px; color:#475569; }
p { margin:0 0 14px; }
strong { color:#c2410c; }
a { color:#1677ff; text-decoration:none; border-bottom:1px solid #bfdbfe; word-break:break-all; }
ul,ol { margin:0 0 14px; padding-left:1.35em; }
li { margin:7px 0; }
hr { border:0; border-top:1px dashed #cbd5e1; margin:22px 0; }
blockquote { margin:0 0 14px; padding:10px 14px; background:#f8fafc; border-left:6px solid #93c5fd;
             border-radius:0 8px 8px 0; color:#475569; }
code { background:#f1f5f9; color:#be123c; border-radius:6px; padding:2px 7px; font-size:21px; word-break:break-all; }
pre { background:#0f172a; color:#e2e8f0; border-radius:10px; padding:14px; font-size:20px; line-height:1.6; overflow-x:auto; }
pre code { background:none; color:inherit; padding:0; font-size:20px; }
.tbl { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:0 0 16px;
       border:1px solid #e2e8f0; border-radius:10px; }
table { border-collapse:collapse; width:100%; font-size:22px; }
th { background:#1677ff; color:#fff; font-weight:600; text-align:left; padding:11px 10px;
     border-right:1px solid #4d94ff; white-space:nowrap; }
th:last-child { border-right:0; }
td { padding:11px 10px; border-top:1px solid #e8eef5; white-space:nowrap; }
tbody tr:nth-child(even) { background:#f8fafc; }
/* 决策速览表：末列（说明/理由）允许换行，避免整表过宽 */
.tbl td:last-child, .tbl th:last-child { white-space:normal; min-width:180px; }
"""


def _md_to_email_html(md_text: str, title: str, subtitle: str = '') -> str:
    """markdown → 邮件 HTML 正文：卡片式排版、正文 22px、移动端优先。

    - 字号写死在 CSS 里，避免客户端剥离样式后回退成小字（老花阅读）
    - 宽表套滚动容器（.tbl），手机端可左右滑动，不压缩列宽
    - 不依赖外部 CSS/图片，QQ 邮箱等客户端直接渲染
    """
    import re
    import markdown2
    from datetime import datetime
    body = markdown2.markdown(
        md_text,
        extras=['tables', 'fenced-code-blocks', 'strike', 'task_list']
    )
    # 每个表格套一层可横向滚动容器
    body = re.sub(r'<table>', '<div class="tbl"><table>', body).replace('</table>', '</table></div>')
    sub = f'<div class="s">{subtitle}</div>' if subtitle else ''
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{title}</title>\n<style>{_MAIL_CSS}</style>\n</head>\n<body>\n'
        '<div class="wrap">\n'
        f'<div class="hd"><div class="t">{title}</div>{sub}</div>\n'
        f'<div class="card">\n{body}\n</div>\n'
        f'<div class="ft">生成时间 {gen_time}<br>本邮件由 HVT-BULL 量化系统自动推送，仅供参考，不构成投资建议</div>\n'
        '</div>\n</body>\n</html>'
    )


# ---- 决策速览表：由报告 md 确定性生成（AI 不参与，保证买卖决策点稳定不漂移） ----
_DECISION_HEAD = ('| 动作 | 代码 | 名称 | 关键价 | 买区/支撑 | 止损 | 说明 |\n'
                  '|---|---|---|---|---|---|---|')
_DECISION_PRI = ('可买', '确认后买', '暂不买', '回踩买点', '继续持有', '分批减仓',
                 '清仓离场', '等突破', '等确认', '不追高', '回避')
_PRI_OF = {a: i for i, a in enumerate(_DECISION_PRI, 1)}
# 每个动作的表格行数上限（统计仍报真实总数）——20261001 与报告展示上限同步放宽（原 6/6/5/6）
_DECISION_CAP = {'等突破': 20, '等确认': 20, '不追高': 20, '回避': 20}


def _split_sections(md_text: str) -> dict:
    """按 ## / ### 标题切段，返回 {标题: [正文行]}"""
    secs, cur = {}, None
    for ln in md_text.splitlines():
        if ln.startswith('## ') or ln.startswith('### '):
            cur = ln.strip()
            secs.setdefault(cur, [])
            continue
        if cur is not None:
            secs[cur].append(ln)
    return secs


def _find_sec(secs: dict, prefix: str):
    for k, v in secs.items():
        if k.startswith(prefix):
            return v
    return []


def _table_rows(lines):
    """抽取 markdown 表格数据行（自动跳过表头与分隔线），返回二维 cells 列表"""
    rows = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith('|') or set(s) <= set('|-: '):
            continue
        rows.append([c.strip() for c in s.strip('|').split('|')])
    return rows


def _cell(cells, i, default='-'):
    if i < len(cells):
        v = cells[i].strip()
        if v and v not in ('-', '—'):
            return v
    return default


def _clip(s, n=32):
    s = (s or '').replace('|', '/').replace('\n', ' ').strip()
    return s if len(s) <= n else s[:n] + '…'


def _tag(prefix, val):
    """给价格加语义前缀；空值原样返回 '-'"""
    return f'{prefix}{val}' if val not in ('-', '—', '') else '-'


def _is_above(close, val) -> bool:
    """现价是否已在 val 上方（任一不可解析 → False）"""
    try:
        return float(close) > float(val)
    except (TypeError, ValueError):
        return False


def _trigger_desc(val: str, close=None, pending: str = '触发') -> str:
    """触发价描述：现价已在触发价上方 → "已突破X"（这道门已过），否则 pending+X。

    entry_trigger 是"突破确认位"，现价站上去后再说"过 X 才买"会与 WAIT_CONFIRM
    的真实卡点（扩张空间/延续性/执行分）自相矛盾，故按现价改写描述。
    """
    if val in ('-', '—', ''):
        return '-'
    return f'已突破{val}' if _is_above(close, val) else f'{pending}{val}'


def _load_trigger_facts(trade_date: str) -> dict:
    """从 hvt_bull_{date}.json 读「现价 / 触发价」，返回 {代码: {name, close, trigger}}。

    报告 md 的 A 表不带现价，无法判断是否已站上触发价，故回 JSON 取数；
    读取失败返回 {}（按 pending 口径降级渲染，不影响推送）。
    """
    import json
    path = os.path.join(BASE_DIR, 'report_daily', f'hvt_bull_{trade_date}.json')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    facts = {}
    for key in ('first_echelon_pool', 'te_buy_pool'):
        for it in data.get(key) or []:
            code = str(it.get('ts_code') or '').strip()
            if not code:
                continue
            try:
                facts[code] = {'name': str(it.get('name') or '').strip(),
                               'close': float(it.get('current_close')),
                               'trigger': float(it.get('entry_trigger'))}
            except (TypeError, ValueError):
                continue
    return facts


def _trigger_facts_note(facts: dict) -> str:
    """把「现价已站上触发价」的标的写成一则事实提示，交给 AI 避免口径矛盾"""
    rows = [f"{v['name']}（{c}）：现价{v['close']:.2f} 已站上触发价{v['trigger']:.2f}"
            for c, v in sorted(facts.items()) if v['close'] > v['trigger']]
    if not rows:
        return ''
    return ('【价格事实（务必遵守）】以下标的现价已在触发价上方，属"已突破X"状态；'
            '凡提到这些标的，必须写出"已突破X"，禁止使用"过X才买""站稳X上方才动"'
            '这类未突破措辞（它们的情况是：价格门已过，还缺其它确认）：\n'
            + '\n'.join(f'- {r}' for r in rows))


def _build_decision_table(md_text: str, facts: dict = None) -> str:
    """从报告 md 生成「明日决策速览表」：每票只留 动作 + 关键价位 + 一句话说明。

    动作口径（散户向）：可买 / 确认后买 / 回踩买点 / 继续持有 / 分批减仓 / 清仓离场
                       / 等突破 / 等确认 / 不追高 / 剔除不买 / 回避
    同一只票只保留优先级最高的一条；每天同一份报告 → 输出完全一致（不随 AI 漂移）。
    """
    secs = _split_sections(md_text)
    import re
    ents = []   # (pri, 动作, 代码, 名称, 关键价, 买区/支撑, 止损, 说明)

    # A. 第一梯队 PRIMARY_BUY（可买 / 确认后买 / 等确认 / 回避）
    for r in _table_rows(_find_sec(secs, '## A.'))[1:]:
        st = _cell(r, 9)
        # 20261001：状态列可能带 action（如 READY_BUY/BUY_ON_CONFIRM）——BUY_ON_CONFIRM
        # 当日不可直接执行（需次日开盘确认），不得标为「可买」，与 TE/FE 落库口径一致
        if 'BUY_ON_CONFIRM' in st:
            act = '确认后买'
        elif st in ('READY_BUY', 'PULLBACK_BUY'):
            act = '可买'
        elif st == 'SKIP':
            act = '回避'
        else:
            act = '等确认'
        f = (facts or {}).get(_cell(r, 2)) or {}
        if act == '回避':
            note = _clip(_cell(r, 15), 26)
        elif _is_above(f.get('close'), _cell(r, 10)):
            # 价格门已过：说清"已突破"后仍卡在扩张空间/延续性，避免被读成买点
            note = f"{_cell(r, 1)}级 / 已站上触发价，缺扩张/延续确认"
        else:
            note = f"{_cell(r, 1)}级 / {st} / FE{_cell(r, 7)}"
        ents.append((_PRI_OF[act], act, _cell(r, 2), _cell(r, 3),
                     _trigger_desc(_cell(r, 10), f.get('close')), _cell(r, 11), _cell(r, 12), note))

    # F. 突破回踩 GOOD（第二次上车买点）
    for r in _table_rows(_find_sec(secs, '## F.'))[1:]:
        if _cell(r, 2) != 'GOOD':
            continue
        ents.append((_PRI_OF['回踩买点'], '回踩买点', _cell(r, 0), _cell(r, 1),
                     f"回踩低点{_cell(r, 4)}", '-', f"跌破{_cell(r, 4)}",
                     f"缩量比{_cell(r, 6)} / 低点较T0高点{_cell(r, 7)}"))

    # ① 次日买入候选
    for r in _table_rows(_find_sec(secs, '### ① '))[1:]:
        act = '可买' if _cell(r, 6) == 'BUY' else '确认后买'
        ents.append((_PRI_OF[act], act, _cell(r, 0), _cell(r, 1),
                     _tag('触发', _cell(r, 7)), _cell(r, 8), _cell(r, 9),
                     f"{_cell(r, 2)} / {_cell(r, 5)} / 分{_cell(r, 3)}"))

    # ② 突破后才买
    for r in _table_rows(_find_sec(secs, '### ② '))[1:][:20]:
        ents.append((_PRI_OF['等突破'], '等突破', _cell(r, 0), _cell(r, 1),
                     _tag('放量破', _cell(r, 7)), _cell(r, 8), _cell(r, 9),
                     f"突破才买 / {_cell(r, 2)} / 分{_cell(r, 3)}"))

    # E. 右侧持有跟踪（继续持有 / 分批减仓 / 清仓离场）
    for r in _table_rows(_find_sec(secs, '## E.'))[1:]:
        act = {'HOLD': '继续持有', 'TRIMMING': '分批减仓',
               'EXIT': '清仓离场'}.get(_cell(r, 2), '继续持有')
        ma10 = _cell(r, 6)
        ents.append((_PRI_OF[act], act, _cell(r, 0), _cell(r, 1),
                     _tag('主升高点', _cell(r, 3)), _tag('MA10 ', ma10),
                     _tag('跌破MA10 ', ma10), _clip(_cell(r, 7), 26)))

    # ④ 等确认
    for r in _table_rows(_find_sec(secs, '### ④ '))[1:]:
        nxt = _cell(r, 11)
        m = re.search(r'收盘≥([\d.]+)', nxt) or re.search(r'TRIGGER ([\d.]+)', nxt)
        cur = _cell(r, 10, '')
        key = _trigger_desc(m.group(1), cur, pending='需收≥') if m else _tag('现价', cur)
        conds = []
        if 'MA20' in nxt:
            conds.append('站上20日均线')
        if '量比≥1.20' in nxt:
            conds.append('明显放量')
        note = '需' + '、'.join(conds) if conds else '等进一步确认'
        ents.append((_PRI_OF['等确认'], '等确认', _cell(r, 0), _cell(r, 1),
                     key, '-', '-', note))

    # ③ 不追高
    for r in _table_rows(_find_sec(secs, '### ③ '))[1:]:
        zone = _cell(r, 8)
        ents.append((_PRI_OF['不追高'], '不追高', _cell(r, 0), _cell(r, 1),
                     _tag('追高上限', _cell(r, 10)), zone, _cell(r, 9),
                     f"已超买点，回踩{zone}再考虑" if zone != '-' else '已超买点，等回落再考虑'))

    # ①R 再入规则剔除（V3.6 R1/R2）
    _REENTRY_WHY = {'R1': '连续第2日入池、今天只给"确认买"→按规则暂不买',
                    'R2': '首日入池的回踩"确认买"→历史易亏，按规则暂不买'}
    for r in _table_rows(_find_sec(secs, '### ①R'))[1:]:
        rule = _cell(r, 6)
        why = _REENTRY_WHY.get(rule[:2], _clip(rule, 26))
        ents.append((_PRI_OF['暂不买'], '暂不买', _cell(r, 0), _cell(r, 1),
                     '-', '-', '-', why))

    # D. 风险名单（FAILED / DISTRIBUTION / EXIT）
    _RISK_WHY = {'DISTRIBUTION': '结构走坏（派发）', 'FAILED': '结构走坏（破位）',
                 'EXIT': '结构走坏（止损）'}
    d_rows = _table_rows(_find_sec(secs, '## D.'))[1:]
    for r in d_rows:
        why = _cell(r, 2).replace('结构状态:', '')
        ents.append((_PRI_OF['回避'], '回避', _cell(r, 0), _cell(r, 1),
                     '-', '-', '-', _clip(_RISK_WHY.get(why, why), 22)))

    # 同一只票只保留优先级最高的一条（sorted 稳定，先入者优先）
    seen, final = set(), []
    for e in sorted(ents, key=lambda x: x[0]):
        if e[2] in seen:
            continue
        seen.add(e[2])
        final.append(e)

    # 真实总数（用于摘要；表格行数可能被 _DECISION_CAP 截断）
    cnt = {}
    for e in final:
        cnt[e[1]] = cnt.get(e[1], 0) + 1
    if d_rows:
        cnt['回避'] = len(d_rows)

    # 按 _DECISION_CAP 截断每个动作的行数
    used, rows = {}, []
    for e in final:
        a = e[1]
        if used.get(a, 0) >= _DECISION_CAP.get(a, 10 ** 9):
            continue
        used[a] = used.get(a, 0) + 1
        rows.append(e)

    parts = []
    for a in _DECISION_PRI:
        n = cnt.get(a, 0)
        if not n:
            continue
        lim = _DECISION_CAP.get(a)
        parts.append(f'{a} {n} 只（列前{lim}）' if lim and n > lim else f'{a} {n} 只')
    head = '；'.join(parts) if parts else '今日无任何决策信号'
    if not cnt.get('可买') and not cnt.get('确认后买'):
        head = '【今日无「立即可买」标的】' + head

    out = [f'**{head}**', '', _DECISION_HEAD]
    for _, act, code, name, p1, p2, p3, note in rows:
        out.append(f'| {act} | {code} | {name} | {p1} | {p2} | {p3} | {note} |')
    return '\n'.join(out)


def _strip_ai_headings(text: str) -> str:
    """把 AI 误加的 # 标题降级为加粗行，避免与邮件自身标题层级打架"""
    out = []
    for ln in text.splitlines():
        if ln.startswith('#'):
            out.append(f'**{ln.lstrip("#").strip()}**')
        else:
            out.append(ln)
    return '\n'.join(out)


def _extract_one_liner(ai_text: str):
    """把 AI 的「明日一句话清单」整段抽出来置顶。

    返回 (标题, 清单条目行, 剩余正文)；识别不到时返回 ('', [], 原文)。
    """
    import re
    lines = ai_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if '明日一句话清单' in ln and re.match(r'^\s*\d+[.、]\s', ln):
            start = i
            break
    if start is None:
        return '', [], ai_text
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^\s*\d+[.、]\s', lines[j]):
            end = j
            break
    block = lines[start:end]
    head = re.sub(r'^\s*\d+[.、]\s*', '', block[0]).replace('**', '').strip().rstrip('：:')
    body = [ln for ln in block[1:] if ln.strip()]
    rest = '\n'.join(lines[:start] + lines[end:])
    return head, body, rest


def _theme_watch_section(md_text: str):
    """抽取报告中的「热点主题新增跟踪」节（HOT_THEME_WATCH），返回 (标题, 正文行)。

    该节列出经 V2.4 热点主题扩池纳入的非龙头标的（如诺唯赞），是报告因扩池
    新增的独立一节；邮件决策速览表按动作分池、且有优先级去重，会漏掉它，故单独接进邮件。
    """
    for k, v in _split_sections(md_text).items():
        if 'HOT_THEME_WATCH' in k:
            body = list(v)
            while body and not body[0].strip():   # 去首尾空行，保留段内空行（表格前需空行）
                body.pop(0)
            while body and not body[-1].strip():
                body.pop()
            return k.lstrip('#').strip(), body
    return '', []


def _right_bottom_section(md_text: str):
    """抽取报告中的「右底低吸信号」节（RIGHT_BOTTOM），返回 (标题, 正文行)。

    该节列出独立扫描命中的右底低吸标的（20261008 起全市场扫描，
    strategy_id=hvt_bull_rb）；邮件决策速览表按 HVT 事件动作分池、会漏掉它，故单独接进邮件。
    """
    for k, v in _split_sections(md_text).items():
        if 'RIGHT_BOTTOM' in k:
            body = list(v)
            while body and not body[0].strip():   # 去首尾空行，保留段内空行（表格前需空行）
                body.pop(0)
            while body and not body[-1].strip():
                body.pop()
            return k.lstrip('#').strip(), body
    return '', []


def _compose_email(md_text: str, ai_text: str, trade_date: str) -> str:
    """邮件正文组装：一句话清单置顶 → 决策速览表 → 热点主题新增跟踪 → 右底低吸 → AI 白话复盘"""
    facts = _load_trigger_facts(trade_date)
    head, body, rest = _extract_one_liner(_strip_ai_headings(ai_text))
    tw_head, tw_body = _theme_watch_section(md_text)
    rb_head, rb_body = _right_bottom_section(md_text)
    parts = [f'# {trade_date} HVT-BULL 天量牛股决策简报', '']
    if body:
        parts += [f'## {head}', ''] + body + ['']
    parts += ['## 明日决策速览（照此执行）', '',
              _build_decision_table(md_text, facts), '']
    if tw_body:
        parts += [f'## {tw_head}', ''] + tw_body + ['']
    if rb_body:
        parts += [f'## {rb_head}', ''] + rb_body + ['']
    parts += ['## 白话复盘', '', rest]
    return '\n'.join(parts)


def send_email(text: str, trade_date: str) -> bool:
    """Agent Mail CLI 发送复盘邮件（HTML 正文 22px）到 MAIL_TO。

    CLI 的 --body-file 必须落在当前工作目录内且 ≤1MB，故正文先写 cwd 下的临时
    html 文件，发送成功即删除（失败则留存并打印路径）。任何异常只打日志不抛出。
    """
    if os.path.exists(MAIL_RUN_JS):
        cli = ['node', MAIL_RUN_JS]
    elif os.path.exists(MAIL_CLI):
        cli = [MAIL_CLI]
    else:
        print(f'[HVT-PUSH] 未找到 Agent Mail CLI（{MAIL_RUN_JS} / {MAIL_CLI}），跳过邮件推送')
        return False

    subject = f'{trade_date} HVT-BULL 天量牛股复盘'
    body_path = os.path.join(os.getcwd(), f'_hvt_mail_{trade_date}.html')
    try:
        html = _md_to_email_html(text, subject, '历史天量 + 二次突破 · 决策简报')
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        print(f'[HVT-PUSH] 邮件正文写入失败: {e}')
        return False

    cmd = cli + ['message', '+send', '--to', MAIL_TO, '--subject', subject,
                 '--body-file', os.path.basename(body_path), '--body-format', 'html',
                 '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print(f'[HVT-PUSH] 邮件推送成功: {MAIL_TO}')
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print(f'[HVT-PUSH] 邮件推送失败: rc={r.returncode} {detail}（正文留存: {body_path}）')
        return False
    except Exception as e:
        print(f'[HVT-PUSH] 邮件推送异常: {e}')
        return False


def _fallback_summary(md_text: str, trade_date: str) -> str:
    """AI 不可用时的降级摘要：只给基础统计 + 风险/超跌计数。

    买卖决策点由「决策速览表」（代码确定性生成）承担，故此处不再重复罗列表格。
    """
    stats, n_risk, n_dip = [], 0, 0
    zone = None
    for ln in md_text.splitlines():
        s = ln.strip()
        if s.startswith('日期') or s.startswith('股票池') or s.startswith('状态分布'):
            stats.append(s)
            continue
        if s.startswith('## D. '):
            zone = 'risk'
            continue
        if s.startswith('DIP_REBOUND_WATCH'):
            zone = 'dip'
            continue
        if s.startswith('## ') or s.startswith('### '):
            zone = None
            continue
        if zone and s.startswith('|') and not set(s) <= set('|-: '):
            if zone == 'risk':
                n_risk += 1
            else:
                n_dip += 1
    out = ['**（AI 解读暂不可用，以下为基础统计；买卖决策以上方速览表为准）**', '']
    out += [f'- {x}' for x in stats]
    out.append(f'- 风险名单（结构走坏，不抄底）：{max(0, n_risk - 1)} 只')
    out.append(f'- 超跌反弹观察池：{max(0, n_dip - 1)} 只（仅观察信号，非买入建议）')
    return '\n'.join(out)


def push_daily_report(trade_date: str = None) -> str:
    """读报告 → 生成决策速览表（确定性）→ AI 白话复盘 → 邮件推送 → 落盘。返回最终推送文本。"""
    _load_env()
    from datetime import datetime
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')
    md_text = _read_report(trade_date)
    if not md_text:
        print(f'[HVT-PUSH] 未找到报告 {_report_path(trade_date)}，跳过')
        return ''
    ai_text = summarize_with_deepseek(md_text, trade_date)
    if not ai_text:
        ai_text = _fallback_summary(md_text, trade_date)
    text = _compose_email(md_text, ai_text, trade_date)
    out_dir = os.path.join(BASE_DIR, 'report_daily')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f'hvt_bull_ai_{trade_date}.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    send_email(text, trade_date)
    return text


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None)
    args = ap.parse_args()
    print(push_daily_report(args.date))
