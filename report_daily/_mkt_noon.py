# -*- coding: utf-8 -*-
import subprocess, json, os, sys, datetime
import pandas as pd
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'
today = '20260713'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'noon.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

now = datetime.datetime.now()
print("=== 午盘收盘分析  %s ===\n" % now.strftime('%Y-%m-%d %H:%M'))

# ── 1. 四大指数快讯快查 ──
print("--- 今日午盘快讯 ---")
resp_news = mcp_call('wenda_news_query', bdate=today, edate=today)
all_news = []
if resp_news:
    items = resp_news.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            src = item[3] if len(item) > 3 else ''
            summary = item[4] if len(item) > 4 else ''
            if title and len(title) > 5:
                all_news.append((t_str, title, summary, src))

# 按时间排
all_news.sort(key=lambda x: x[0], reverse=True)

# 11点后的快讯
late_news = [(t, ti, su) for t, ti, su, sr in all_news if t.startswith('2026-07-13 11') or t.startswith('2026-07-13 10')]
print("  午盘快讯数: %d条" % len(late_news))

# 重要快讯
print("\n  午盘重要快讯:")
for t, title, summary in late_news[:10]:
    bull_kw = ['涨停', '上涨', '反弹', '爆发', '突破', '超跌', '净流入', '资金流入', '大涨', '飙升']
    bear_kw = ['跌停', '下跌', '暴跌', '恐慌', '杀跌', '砸盘', '净流出', '资金流出', '大跌', '跳水']
    s = title + summary
    is_bull = any(k in s for k in bull_kw)
    is_bear = any(k in s for k in bear_kw)
    if is_bull: tag = "BULL"
    elif is_bear: tag = "BEAR"
    else: tag = "INFO"
    print("  [%s][%s] %s" % (tag, t[11:16], title[:70]))
    if summary: print("         %s" % summary[:100])

# ── 2. 用Tushare拉最新日K（2026-07-10收盘数据做基准，补充今日预估）──
print("\n--- 指数基准（上周五收盘）---")
idx_data = {}
idx_codes = [
    ('000001.SH', '上证指数'),
    ('399001.SZ', '深证成指'),
    ('399006.SZ', '创业板指'),
    ('399300.SZ', '沪深300'),
    ('932000.CSI', '中证2000'),
    ('000852.SH', '中证1000'),
]

for code, name in idx_codes:
    try:
        df = pro.index_daily(ts_code=code, trade_date='20260710')
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            idx_data[name] = {
                'close_y': float(row['close']),
                'pct_y': float(row['pct_chg']),
            }
            pct = float(row['pct_chg'])
            pct_str = '+%.2f' % pct if pct >= 0 else '%.2f' % pct
            print("  %s: %.2f  %s%%" % (name, float(row['close']), pct_str))
    except Exception as e:
        print("  %s: %s" % (name, e))

# ── 3. 今日指数快讯涨跌（从快讯提取）──
print("\n--- 指数今日涨跌（快讯提取）---")
idx_today_pct = {}
for t, title, summary, src in all_news:
    s = title + summary
    # 上证
    if '沪指' in s or '上证' in s or '大盘' in s:
        for kw in ['+', '涨', '上']:
            idx = s.find(kw)
            if idx > 0:
                try:
                    nums = s[idx:idx+5]
                    import re
                    m = re.search(r'[-+]?\d+\.\d+', nums)
                    if m:
                        v = float(m.group())
                        if 3000 < v < 5000:
                            idx_today_pct['上证指数'] = v
                except:
                    pass
    # 创业板
    if '创业板' in s:
        try:
            import re
            m = re.search(r'创业板[-:]?\s*([-+]?\d+\.\d+)%?', s)
            if m:
                idx_today_pct['创业板指'] = float(m.group(1))
        except:
            pass

# ── 4. 午盘主题机会快讯 ──
print("\n--- 午盘主题机会分析 ---")
theme_news = {}
bull_news = []
bear_news = []

for t, title, summary, src in all_news:
    s = title + summary
    
    # 主题识别
    themes = []
    if any(k in s for k in ['中药', '中成药', '基药', '医药']):
        themes.append('中药/医药')
    if any(k in s for k in ['银行', '保险', '券商', '红利']):
        themes.append('金融/银行')
    if any(k in s for k in ['AI', '大模型', 'DeepSeek', '具身', '算力']):
        themes.append('AI/算力')
    if any(k in s for k in ['半导体', '芯片', '光刻']):
        themes.append('半导体')
    if any(k in s for k in ['机器人', '工业母机', '人形']):
        themes.append('机器人')
    if any(k in s for k in ['军工', '国防', '航天', '商业航天']):
        themes.append('军工/航天')
    if any(k in s for k in ['新能源', '锂电', '储能', '光伏']):
        themes.append('新能源')
    if any(k in s for k in ['消费', '食品', '零售']):
        themes.append('消费')
    if any(k in s for k in ['电力', '电网', '能源']):
        themes.append('电力')
    
    # 情绪
    bull_kw = ['涨停', '上涨', '爆发', '反弹', '突破', '超跌', '净流入', '资金流入', '大涨', '拉升', '拉升']
    bear_kw = ['跌停', '下跌', '暴跌', '恐慌', '杀跌', '砸盘', '净流出', '资金流出', '大跌', '跳水', '领跌']
    
    is_bull = any(k in s for k in bull_kw)
    is_bear = any(k in s for k in bear_kw)
    
    for theme in themes:
        if theme not in theme_news:
            theme_news[theme] = {'bull': 0, 'bear': 0, 'items': []}
        if is_bull: 
            theme_news[theme]['bull'] += 1
            theme_news[theme]['items'].append(('BULL', t, title))
        if is_bear:
            theme_news[theme]['bear'] += 1
            theme_news[theme]['items'].append(('BEAR', t, title))
    
    if is_bull and is_bear:
        pass
    elif is_bull:
        bull_news.append((t, title, summary))
    elif is_bear:
        bear_news.append((t, title, summary))

# 主题综合分
theme_scores = {}
for theme, data in theme_news.items():
    score = (data['bull'] - data['bear']) * 5 + len(data['items']) * 2
    theme_scores[theme] = score

ranked = sorted(theme_scores.items(), key=lambda x: -x[1])

print("  主题热度排名:")
for i, (theme, score) in enumerate(ranked[:8]):
    data = theme_news.get(theme, {})
    b = data.get('bull', 0)
    be = data.get('bear', 0)
    bar = '█' * min(score // 3, 15)
    print("  %d. %-12s %3d分 (BULL=%d BEAR=%d) %s" % (i+1, theme, score, b, be, bar))

# ── 5. 午盘重要快讯 ──
print("\n--- 午盘重要快讯详情 ---")
print("  [强势]")
for t, title, summary in bull_news[:5]:
    print("  + [%s] %s" % (t[11:16] if len(t)>11 else t, title[:70]))
    if summary: print("    + %s" % summary[:100])

print("\n  [弱势]")
for t, title, summary in bear_news[:5]:
    print("  - [%s] %s" % (t[11:16] if len(t)>11 else t, title[:70]))
    if summary: print("    - %s" % summary[:100])

# ── 6. 关键事件 ──
print("\n--- 今日关键事件影响 ---")
events = [
    ('阶跃终端新品发布', 'AI具身智能', '中性偏多', '事件落地看预期兑现'),
    ('台积电月度销售', '半导体/AI算力', '待揭晓', '若超预期利好芯片，若低预期雪上加霜'),
    ('基药目录更新', '中药/医药', '利好', '政策催化，中药ETF已+3%'),
    ('银行分红潮', '银行/红利', '利好', '防御资金抱团，逆势上涨'),
    ('赛力斯H1亏损15-18亿', '新能源车', '利空', '行业价格战持续，新能源车回避'),
]

for event, theme, impact, note in events:
    imp_str = '+%s+' % impact if impact.startswith('利好') else ('-%s-' % impact if impact.startswith('利空') else '=中性=')
    print("  %-20s %-15s %s  %s" % (event[:18], theme[:13], imp_str, note[:30]))

# ── 7. 综合结论 ──
print("\n--- 午盘综合结论 ---")
bull_cnt = len(bull_news)
bear_cnt = len(bear_news)
mid_news = len(all_news) - bull_cnt - bear_cnt

print("  快讯: 强势=%d  弱势=%d  中性=%d" % (bull_cnt, bear_cnt, mid_news))

# 上证基准
sh_close_y = idx_data.get('上证指数', {}).get('close_y', 0)
csi2k_close_y = idx_data.get('中证2000', {}).get('close_y', 0)

# 从快讯提取今日上证涨跌
sh_today_pct = None
for t, title, summary, src in all_news:
    s = title + summary
    if '沪指' in s or '上证' in s or '大盘' in s:
        import re
        # 找 "涨/跌 X.XX%"
        for kw in ['收涨', '收跌', '涨%', '下跌']:
            m = re.search(r'([涨跌])([\d.]+)%', s)
            if m:
                sign = 1 if m.group(1) == '涨' else -1
                val = float(m.group(2))
                sh_today_pct = sign * val
                break

if sh_today_pct:
    print("  今日上证(估): %+.2f%%" % sh_today_pct)
else:
    print("  今日上证: 数据待收盘确认")
    print("  上周五收盘: %.2f" % sh_close_y)

print("\nDone")
