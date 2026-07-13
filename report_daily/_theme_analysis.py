# -*- coding: utf-8 -*-
import subprocess, json, os, sys, datetime
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'thm.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

today = '20260713'
now = datetime.datetime.now()
print("=== 今日盘中主题分析  %s ===\n" % now.strftime('%H:%M'))

# 1. 今日盘中主题快讯
print("--- 今日盘中快讯主题 ---")
resp = mcp_call('wenda_news_query', bdate=today, edate=today)
news_by_theme = {}
if resp:
    items = resp.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            src = item[3] if len(item) > 3 else ''
            summary = item[4] if len(item) > 4 else ''
            if not title: continue
            
            # 主题识别
            themes = []
            if any(k in title+summary for k in ['AI', '人工智能', '大模型', 'LLM', 'DeepSeek']):
                themes.append('AI大模型')
            if any(k in title+summary for k in ['半导体', '芯片', '光刻', '晶圆', '封装']):
                themes.append('半导体')
            if any(k in title+summary for k in ['机器人', '具身', '人形', '工业母机', '机械']):
                themes.append('机器人/工业母机')
            if any(k in title+summary for k in ['银行', '保险', '券商', '金融']):
                themes.append('金融')
            if any(k in title+summary for k in ['医疗', '医药', '中药', '创新药']):
                themes.append('医药')
            if any(k in title+summary for k in ['新能源', '锂电', '储能', '光伏', '电动车']):
                themes.append('新能源')
            if any(k in title+summary for k in ['军工', '国防', '商业航天']):
                themes.append('军工/航天')
            if any(k in title+summary for k in ['算力', '云计算', '数据中心', 'IDC']):
                themes.append('算力')
            if any(k in title+summary for k in ['消费', '食品', '家电', '零售']):
                themes.append('消费')
            if any(k in title+summary for k in ['电力', '电网', '能源', '煤炭']):
                themes.append('电力/能源')
            if any(k in title+summary for k in ['房地产', '地产', '建材']):
                themes.append('地产')
            if any(k in title+summary for k in ['通信', '5G', '卫星']):
                themes.append('通信')
            if any(k in title+summary for k in ['汽车', '整车']):
                themes.append('汽车')
            
            for theme in themes:
                if theme not in news_by_theme:
                    news_by_theme[theme] = []
                news_by_theme[theme].append((t_str, title, summary))

# 主题排序
theme_counts = [(t, len(items)) for t, items in news_by_theme.items()]
theme_counts.sort(key=lambda x: -x[1])

print("  主题快讯热度:")
for theme, count in theme_counts[:10]:
    print("  [%d] %s" % (count, theme))

# 2. 重要主题快讯详情
print("\n  重要快讯详情:")
all_news = []
if resp:
    items = resp.get('data', [])
    if isinstance(items, list) and len(items) > 1:
        for item in items[1:]:
            if not isinstance(item, list) or len(item) < 4: continue
            title = item[0] if len(item) > 0 else ''
            t_str = item[1] if len(item) > 1 else ''
            summary = item[4] if len(item) > 4 else ''
            if title and len(title) > 8:
                all_news.append((t_str, title, summary))

# 按重要性排序
def news_priority(title, summary):
    score = 0
    high_kw = ['业绩', '中标', '涨停', '合作', '突破', '回购', '增持', '扩产', '订单', '超预期', '扭亏']
    low_kw = ['亏损', '预警', '立案', '处罚', 'ST', '终止', '减持', '风险']
    s = title + summary
    for k in high_kw:
        if k in s: score += 3
    for k in low_kw:
        if k in s: score -= 5
    return score

all_news.sort(key=lambda x: -news_priority(x[1], x[2]))

for t, title, summary in all_news[:15]:
    pri = news_priority(title, summary)
    if pri > 0:
        tag = "[GOOD] "
    elif pri < 0:
        tag = "[BAD]  "
    else:
        tag = "[INFO] "
    print("  %s[%s] %s" % (tag, t[:16], title[:65]))
    if summary:
        print("           %s" % summary[:100])

# 3. 今日板块涨跌（用Tushare概念板块）
print("\n--- 板块异动（今日）---")
try:
    # 获取今日强势板块
    concept = pro.concept()
    if concept is not None and len(concept) > 0:
        print("  概念板块总数: %d" % len(concept))
except Exception as e:
    print("  概念板块: %s" % e)

# 用指数成分近似板块涨跌
# 拉各板块代表性股票
sector_stocks = {
    '银行': [('601166.SH','兴业银行'), ('600036.SH','招商银行'), ('601398.SH','工商银行'), ('600000.SH','浦发银行'), ('宁波银行002142.SZ')],
    '半导体': [('688981.SH','中芯国际'), ('603501.SH','韦尔股份'), ('002371.SZ','北方华创'), ('688008.SH','澜起科技'), ('688256.SH','寒武纪')],
    'AI/算力': [('688787.SH','海光信息'), ('688521.SH','芯原股份'), ('603019.SH','中科曙光'), ('000977.SZ','浪潮信息')],
    '机器人': [('688024.SH','蔚来'), ('300124.SZ','汇川技术'), ('002230.SZ','科大讯飞'), ('688499.SH','利元亨')],
    '医药': [('000538.SZ','云南白药'), ('600276.SH','恒瑞医药'), ('300760.SZ','迈瑞医疗'), ('688180.SH','君实生物')],
    '军工': [('600893.SH','航发动力'), ('600760.SH','中航沈飞'), ('002414.SZ','高德红外'), ('002025.SZ','航天电器')],
    '新能源': [('300750.SZ','宁德时代'), ('002594.SZ','比亚迪'), ('601012.SH','隆基绿能'), ('600438.SH','通威股份')],
}

# 拉这些股票的近期涨跌
print("\n--- 代表股近期涨跌 ---")
sector_perf = {}
for sector, stocks in sector_stocks.items():
    pct_list = []
    for stock in stocks:
        if isinstance(stock, tuple):
            code = stock[0]
        else:
            code = stock
        try:
            df = pro.daily(ts_code=code, trade_date='20260710')
            if df is not None and len(df) > 0:
                pct = float(df.iloc[0]['pct_chg'])
                pct_list.append(pct)
        except:
            pass
    
    if pct_list:
        avg = sum(pct_list) / len(pct_list)
        sector_perf[sector] = avg
        pct_str = '+%.2f' % avg if avg >= 0 else '%.2f' % avg
        bar = '|' * min(int(abs(avg)), 10)
        print("  %s: %s%% %s" % (sector, pct_str, bar))

# 4. 主题综合评分
print("\n--- 主题综合评分 ---")
theme_scores = {}

# 基础分
for theme, _ in theme_counts:
    theme_scores[theme] = 0

# 快讯加分
for theme, count in theme_counts:
    theme_scores[theme] += count * 2

# 板块涨跌加权
sector_to_theme = {
    '银行': '金融', '半导体': '半导体', 'AI/算力': '算力',
    '机器人': '机器人/工业母机', '医药': '医药', '军工': '军工/航天',
    '新能源': '新能源'
}
for sector, perf in sector_perf.items():
    theme = sector_to_theme.get(sector, sector)
    if theme in theme_scores:
        theme_scores[theme] += int(perf * 3)  # 涨跌1%=+3分

# 中证2000相关性加成（今日小盘弱）
csi2k_weak = True
if csi2k_weak:
    if '半导体' in theme_scores: theme_scores['半导体'] += 5  # 国产替代主线
    if '金融' in theme_scores: theme_scores['金融'] += 8       # 防御抱团
    if '机器人/工业母机' in theme_scores: theme_scores['机器人/工业母机'] += 3  # 政策催化

# 排序
ranked = sorted(theme_scores.items(), key=lambda x: -x[1])

print("  主题排名(综合分):")
for i, (theme, score) in enumerate(ranked[:10]):
    bar = '█' * min(score // 5, 15)
    print("  %d. %-15s %3d分 %s" % (i+1, theme, score, bar))

# 5. 今日主题机会与风险
print("\n--- 今日主题机会与风险 ---")
opportunities = []
risks = []

# 机会分析
if any('金融' in t for t,_ in ranked[:5]):
    opportunities.append(('金融/银行', '银行板块逆势上涨+分红催化，防御资金抱团'))
if any('AI大模型' in t for t,_ in ranked[:5]):
    opportunities.append(('AI大模型', '阶跃/蚂蚁灵波催化，AI具身智能新方向'))
if any('机器人' in t for t,_ in ranked[:5]):
    opportunities.append(('机器人/工业母机', '政策持续催化，国产替代逻辑强'))
if any('算力' in t for t,_ in ranked[:5]):
    opportunities.append(('算力', 'AI应用爆发+大模型军备竞赛持续'))

# 风险分析
risks.append(('中证2000/小盘', '均线空头排列，C浪延续，等待止跌信号'))
risks.append(('创业板', '5日跌-4.4%，科技成长领跌，动能极弱'))
risks.append(('新能源车', '阿维塔IPO失败+赛力斯亏损，行业价格战持续'))

print("  [机会]")
for t, desc in opportunities:
    print("  + %s: %s" % (t, desc))

print("  [风险]")
for t, desc in risks:
    print("  - %s: %s" % (t, desc))

print("\nDone")
