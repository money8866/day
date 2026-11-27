# -*- coding: utf-8 -*-
"""生成 2026-11-27 午盘分析 HTML（纯 SVG 图，无外链）"""
import json

BASE = r'D:/mystock/solo'
OUT = r'C:/Users/kongx/WorkBuddy/main/Claw/午盘分析-20261127.html'

minute = json.load(open(BASE + r'/_minute_1127.json'))
daily = json.load(open(BASE + r'/_daily_1127.json'))

C_UP='#c0392b'; C_DN='#1e8449'; C_LINE='#2f6fed'; C_GRID='#eef0f2'; C_TXT='#86909c'; C_AXIS='#f0f1f3'


def esc(s):
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')


def minute_svg(code, title, w=900, h=420):
    """分时走势 + 每分钟成交额柱；上价格下量柱"""
    d = minute[code]; pre = d['pre']; pts = d['pts']
    L,R,T,B = 60,886,34,300      # 价格区
    VB = 320                      # 量柱区顶
    VBOT = 388                    # 量柱区底
    pmin = min(p[1] for p in pts); pmax = max(p[1] for p in pts)
    # 以昨收为中心对称扩域
    span = max(abs(pmin-pre), abs(pmax-pre))*1.15 or 1
    lo, hi = pre-span, pre+span
    n = len(pts)
    def X(i): return L + (R-L)*i/(n-1)
    def Y(p): return T + (B-T)*(hi-p)/(hi-lo)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           f'<text x="12" y="20" font-size="13" font-weight="bold" fill="#1f2937">{esc(title)}</text>']
    # 横向网格 + 左轴价格 + 右轴百分比
    for k in range(5):
        p = hi - (hi-lo)*k/4
        y = Y(p); pct = (p/pre-1)*100
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="{C_AXIS}" stroke-width="1"/>')
        out.append(f'<text x="{L-4}" y="{y+3:.1f}" font-size="10" fill="{C_TXT}" text-anchor="end">{p:.0f}</text>')
        out.append(f'<text x="{R+4}" y="{y+3:.1f}" font-size="10" fill="{C_TXT}" text-anchor="start">{pct:+.1f}%</text>')
    # 昨收虚线
    yp = Y(pre)
    out.append(f'<line x1="{L}" y1="{yp:.1f}" x2="{R}" y2="{yp:.1f}" stroke="{C_TXT}" stroke-width="1" stroke-dasharray="5,4"/>')
    out.append(f'<text x="{L+4}" y="{yp-4:.1f}" font-size="10" fill="{C_TXT}">昨收 {pre:.2f}</text>')
    # 价格线 + 面积
    plist = ' '.join(f'{X(i):.1f},{Y(p[1]):.1f}' for i,p in enumerate(pts))
    color = C_UP if pts[-1][1]>=pre else C_DN
    area = f'M{X(0):.1f},{B} ' + ' '.join(f'L{X(i):.1f},{Y(p[1]):.1f}' for i,p in enumerate(pts)) + f' L{X(n-1):.1f},{B} Z'
    out.append(f'<polygon points="{plist} {X(n-1):.1f},{B} {X(0):.1f},{B}" fill="#fdecec" opacity="0.5"/>')
    out.append(f'<polyline points="{plist}" fill="none" stroke="{color}" stroke-width="1.8"/>')
    # 量柱
    vmax = max(p[2] for p in pts) or 1
    bw = (R-L)/n*0.62
    for i,p in enumerate(pts):
        pc = p[1] if i==0 else p[1]-pts[i-1][1]
        vc = C_UP if pc>=0 else C_DN
        vh = (VBOT-VB)*(p[2]/vmax)
        out.append(f'<rect x="{X(i)-bw/2:.1f}" y="{VBOT-vh:.1f}" width="{bw:.1f}" height="{vh:.1f}" fill="{vc}" opacity="0.75"/>')
    # 关键点标注
    imin = min(range(n), key=lambda i: pts[i][1]); imax = max(range(n), key=lambda i: pts[i][1])
    tmin = pts[imin][0]; tmax = pts[imax][0]
    out.append(f'<circle cx="{X(imin):.1f}" cy="{Y(pts[imin][1]):.1f}" r="3.4" fill="{C_DN}"/>')
    out.append(f'<text x="{X(imin):.1f}" y="{Y(pts[imin][1])+16:.1f}" font-size="10.5" fill="{C_DN}" text-anchor="middle">{tmin[:2]}:{tmin[2:]} 低 {pts[imin][1]:.0f}</text>')
    out.append(f'<circle cx="{X(imax):.1f}" cy="{Y(pts[imax][1]):.1f}" r="3.4" fill="{C_UP}"/>')
    out.append(f'<text x="{X(imax):.1f}" y="{Y(pts[imax][1])-8:.1f}" font-size="10.5" fill="{C_UP}" text-anchor="middle">{tmax[:2]}:{tmax[2:]} 高 {pts[imax][1]:.0f}</text>')
    lastp = pts[-1][1]; lastpct=(lastp/pre-1)*100
    out.append(f'<circle cx="{X(n-1):.1f}" cy="{Y(lastp):.1f}" r="3.6" fill="{color}"/>')
    out.append(f'<text x="{R-4}" y="{Y(lastp)-8:.1f}" font-size="11" font-weight="bold" fill="{color}" text-anchor="end">11:30 {lastp:.2f} {lastpct:+.2f}%</text>')
    for t in ['0930','1030','1130']:
        idx = next((i for i,p in enumerate(pts) if p[0]==t),None)
        if idx is not None:
            out.append(f'<text x="{X(idx):.1f}" y="{VBOT+14}" font-size="10" fill="{C_TXT}" text-anchor="middle">{t[:2]}:{t[2:]}</text>')
    out.append(f'<text x="12" y="{h-6}" font-size="10" fill="#9ca3af">上：分时价格（左价格/右涨跌幅）　下：每分钟成交额柱（红涨绿跌）</text>')
    out.append('</svg>')
    return ''.join(out)


def sentiment_svg(w=900, h=400):
    """情绪6日对比：广度线 + 涨停/跌停柱"""
    days = ['11-19','11-20','11-23','11-24','11-25','11-26','11-27*']
    breadth = [11.7,5.6,16.5,35.8,51.8,32.7,84.9]   # 11-27为午盘口径
    zt = [53,45,147,58,53,65,89]
    dt = [72,55,5,4,3,7,0]
    L,R,T,B = 64,842,50,340
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
           f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
           f'<text x="12" y="20" font-size="13.5" font-weight="bold" fill="#1f2937">情绪7日演化：广度冲到84.9%，跌停归零（*11-27为午盘口径）</text>']
    # 广度右轴 0-100
    for k in range(5):
        v = k*25; y = B-(B-T)*v/100
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="{C_GRID}" stroke-width="1"/>')
        out.append(f'<text x="{R+4}" y="{y+3:.1f}" font-size="10" fill="{C_LINE}" text-anchor="start">{v}%</text>')
    n=len(days); slot=(R-L)/n; bw=slot*0.28
    zmax=160
    for i,d in enumerate(days):
        cx = L+slot*(i+0.5)
        # 涨停柱
        zh=(B-T)*zt[i]/zmax
        out.append(f'<rect x="{cx-bw-1:.1f}" y="{B-zh:.1f}" width="{bw:.1f}" height="{zh:.1f}" fill="#e57373" rx="2" opacity="0.85"/>')
        out.append(f'<text x="{cx-bw/2-1:.1f}" y="{B-zh-4:.1f}" font-size="9.5" fill="#c0392b" text-anchor="middle">{zt[i]}</text>')
        # 跌停柱
        dh=(B-T)*dt[i]/zmax
        dc='#1e8449' if dt[i]<15 else '#95a5a6'
        out.append(f'<rect x="{cx+1:.1f}" y="{B-dh:.1f}" width="{bw:.1f}" height="{max(dh,2):.1f}" fill="{dc}" rx="2" opacity="0.85"/>')
        out.append(f'<text x="{cx+bw/2+1:.1f}" y="{B-dh-4:.1f}" font-size="9.5" fill="{dc}" text-anchor="middle">{dt[i]}</text>')
        out.append(f'<text x="{cx:.1f}" y="{B+16}" font-size="10.5" fill="#4e5969" text-anchor="middle">{d}</text>')
    # 广度线
    bpts=' '.join(f'{L+slot*(i+0.5):.1f},{B-(B-T)*b/100:.1f}' for i,b in enumerate(breadth))
    out.append(f'<polyline points="{bpts}" fill="none" stroke="{C_LINE}" stroke-width="2.2"/>')
    for i,b in enumerate(breadth):
        cx=L+slot*(i+0.5); cy=B-(B-T)*b/100
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.4" fill="{C_LINE}"/>')
        out.append(f'<text x="{cx:.1f}" y="{cy-8:.1f}" font-size="10" font-weight="bold" fill="{C_LINE}" text-anchor="middle">{b}%</text>')
    # 45%门槛
    y45=B-(B-T)*45/100
    out.append(f'<line x1="{L}" y1="{y45:.1f}" x2="{R}" y2="{y45:.1f}" stroke="#c0392b" stroke-width="1" stroke-dasharray="5,4"/>')
    out.append(f'<text x="{L+4}" y="{y45-4:.1f}" font-size="10" fill="#c0392b">45% 修复确认线</text>')
    out.append(f'<text x="12" y="{h-6}" font-size="10" fill="#9ca3af">红柱=涨停 · 绿/灰柱=跌停(&lt;15绿) · 蓝线=广度(右轴) · 本轮首次广度站上45%</text>')
    out.append('</svg>')
    return ''.join(out)


def daily_svg(code, title, w=900, h=400):
    """日K线（近30根）+ 收盘折线"""
    rows = daily[code][-30:]
    L,R,T,B = 60,886,34,340
    hi=max(r[2] for r in rows); lo=min(r[3] for r in rows)
    span=hi-lo or 1; hi+=span*0.05; lo-=span*0.05
    n=len(rows); slot=(R-L)/n; bw=slot*0.6
    def Y(p): return T+(B-T)*(hi-p)/(hi-lo)
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
         f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
         f'<text x="12" y="20" font-size="13" font-weight="bold" fill="#1f2937">{esc(title)}</text>']
    for k in range(5):
        p=hi-(hi-lo)*k/4; y=Y(p)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="{C_AXIS}" stroke-width="1"/>')
        out.append(f'<text x="{L-4}" y="{y+3:.1f}" font-size="10" fill="{C_TXT}" text-anchor="end">{p:.0f}</text>')
    for i,r in enumerate(rows):
        d,o,hh,ll,cc=r; cx=L+slot*(i+0.5)
        up=cc>=o; col=C_UP if up else C_DN
        out.append(f'<line x1="{cx:.1f}" y1="{Y(hh):.1f}" x2="{cx:.1f}" y2="{Y(ll):.1f}" stroke="{col}" stroke-width="1"/>')
        y1,y2=Y(max(o,cc)),Y(min(o,cc))
        out.append(f'<rect x="{cx-bw/2:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y2-y1,1.5):.1f}" fill="{col if not up else "#ffffff"}" stroke="{col}" stroke-width="1"/>')
    # 最新标注
    cx=L+slot*(n-0.5); cc=rows[-1][4]
    out.append(f'<circle cx="{cx:.1f}" cy="{Y(cc):.1f}" r="3.4" fill="{C_LINE}"/>')
    out.append(f'<text x="{cx-4:.1f}" y="{Y(cc)-8:.1f}" font-size="10.5" font-weight="bold" fill="{C_LINE}" text-anchor="end">11-26收 {cc:.0f}</text>')
    # x轴日期（每5根）
    for i in range(0,n,5):
        cx=L+slot*(i+0.5); d=rows[i][0][5:]
        out.append(f'<text x="{cx:.1f}" y="{B+16}" font-size="9.5" fill="{C_TXT}" text-anchor="middle">{d}</text>')
    out.append(f'<text x="12" y="{h-6}" font-size="10" fill="#9ca3af">近30交易日日K · 红涨绿跌 · 11-27午盘大涨尚未计入（最新收盘为11-26）</text>')
    out.append('</svg>')
    return ''.join(out)


svg_sh = minute_svg('sh000001','上证指数分时 · 2026-11-27 上午：低开探底 → 尾盘拉升收红')
svg_cy = minute_svg('sz399006','创业板指分时 · 2026-11-27 上午：单边走高，午盘收在 +3.79%')
svg_st = sentiment_svg()
svg_dk = daily_svg('sh000001','上证指数日K · 逼近3500关口，能否收复取决于午后量能')

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>午盘分析——2026-11-27</title>
<style>
body{{margin:0;background:#f4f5f7;color:#1d2129;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.7}}
.wrap{{max-width:860px;margin:0 auto;padding:18px 14px 60px}}
.card{{background:#fff;border-radius:12px;padding:20px 22px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
h1{{font-size:22px;margin:0 0 6px;color:#1d2129}}
h2{{font-size:17px;margin:0 0 12px;color:#1d2129;border-left:4px solid #c0392b;padding-left:10px}}
.sub{{color:#86909c;font-size:13px;margin-bottom:14px}}
.tldr{{background:linear-gradient(135deg,#fff 0%,#fdf2f2 100%)}}
em{{font-style:normal;color:#c0392b;font-weight:700}}
.g{{color:#1e8449;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0}}
th,td{{padding:8px 6px;border-bottom:1px solid #eef0f2;text-align:center}}
th{{background:#f7f8fa;color:#86909c;font-weight:600}}
td:first-child,th:first-child{{text-align:left;color:#4e5969}}
.good{{color:#1e8449;font-weight:600}}.bad{{color:#c0392b;font-weight:600}}.flat{{color:#86909c}}
.note{{font-size:12.5px;color:#86909c;margin-top:10px;padding-top:10px;border-top:1px dashed #e5e6eb}}
ul.brief{{margin:0;padding-left:20px;font-size:13.5px;color:#4e5969}}
ul.brief li{{margin-bottom:9px}}
.feat{{border-left:3px solid #2f6fed;background:#f4f8ff;border-radius:6px;padding:11px 14px;margin-bottom:11px}}
.feat .t{{font-size:14.5px;font-weight:700;color:#1f3a63;margin-bottom:3px}}
.feat .d{{font-size:13.5px;color:#4e5969}}
.feat.warn{{border-left-color:#c0392b;background:#fdf3f3}}.feat.warn .t{{color:#a5281b}}
.feat.grn{{border-left-color:#1e8449;background:#f1faf4}}.feat.grn .t{{color:#14663a}}
.chart{{text-align:center;margin:6px 0}}
.chart svg{{max-width:100%;height:auto}}
.disc{{font-size:11.5px;color:#a3a6ad;background:transparent;box-shadow:none;text-align:center;padding:8px}}
</style>
</head>
<body>
<div class="wrap">

<div class="card tldr">
  <h1>放量普涨，成长领衔——本周最扎实的一次反弹</h1>
  <div class="sub">2026-11-27 周五 午盘收盘 · 数据口径：上午收盘</div>
  <div class="verdict">和 11-24 那次"高开冲高回落"完全不同：这次是<em>低开后一路走高、午盘收在接近全天高点</em>，且放量、赚钱效应连续修复。含金量高一个档次，但仍是<em>午盘早期预警</em>，14:30 才是主决策点。</div>
</div>

<div class="card">
  <h2>一、指数：成长领涨，上证尾盘翻红</h2>
  <table>
    <tr><th>指数</th><th>午盘点位</th><th>涨跌幅</th><th>形态</th></tr>
    <tr><td>上证指数</td><td>3486.76</td><td class="good">+0.28%</td><td>低开探底 → 尾盘拉升翻红</td></tr>
    <tr><td>深证成指</td><td>10301.40</td><td class="good">+2.16%</td><td>单边走高</td></tr>
    <tr><td>创业板指</td><td>2272.72</td><td class="good">+3.79%</td><td>+0.5% 爬升到 +3.9%</td></tr>
    <tr><td>科创50</td><td>1221.79</td><td class="good">+4.43%</td><td>领涨，全程向上</td></tr>
    <tr><td>沪深300</td><td>3909.93</td><td class="good">+0.24%</td><td>权重滞涨</td></tr>
    <tr><td>国证2000</td><td>7571.87</td><td class="good">+3.32%</td><td>小盘活跃</td></tr>
  </table>
  <div class="note">权重（上证+0.28%、沪深300+0.24%）明显滞涨，成长/小盘（科创50+4.4%、创业板+3.8%）领涨——典型的超跌成长反弹结构，而非权重搭台。</div>
</div>

<div class="card">
  <h2>二、分时：低开走高，收在高位</h2>
  <div class="chart">{svg_sh}</div>
  <div class="chart">{svg_cy}</div>
  <div class="note">关键区别：11-24 是开盘即高点、冲高回落；今天上证从 -0.26% 低点一路爬到 +0.41%，创业板从 +0.49% 单边爬到 +3.91% 并收在高位附近。是资金真进场推的，不是情绪跳空。</div>
</div>

<div class="card">
  <h2>三、情绪：广度冲上 84.9%，跌停归零</h2>
  <div class="chart">{svg_st}</div>
  <table>
    <tr><th>指标</th><th>11-27 午盘</th><th>11-26</th><th>变化</th></tr>
    <tr><td>上涨占比（广度）</td><td class="good">84.9%</td><td class="bad">32.7%</td><td class="good">+52.2pp</td></tr>
    <tr><td>涨停 / 跌停</td><td class="good">89 / 0</td><td>65 / 7</td><td class="good">涨停↑ 跌停归零</td></tr>
    <tr><td>半日成交额</td><td>9269 亿</td><td>全天 13260 亿</td><td class="good">预估全日 1.43 万亿</td></tr>
    <tr><td>5日均量 vs 20日均量</td><td class="good">49.9% / 43.2%</td><td>—</td><td class="good">+6.7pp 改善</td></tr>
    <tr><td>昨涨停股今日表现</td><td class="good">+2.76%</td><td>—</td><td class="good">上涨比 60.0% 站回50%线</td></tr>
  </table>
  <div class="note">本轮下跌以来第一次出现"广度站上45%修复线 + 量能正向 + 赚钱效应连续2日修复"三者同现。跌停归零说明恐慌盘基本消化。</div>
</div>

<div class="card">
  <h2>四、日K位置：逼近3500关口</h2>
  <div class="chart">{svg_dk}</div>
  <div class="note">上证在 3455 附近获得支撑后反弹，午盘 3486.76 逼近 3500 整数关。能否收复 3500 并站稳，是这次反弹能否延续的技术关键。</div>
</div>

<div class="card">
  <h2>五、见底进程与操作</h2>
  <div class="feat grn">
    <div class="t">✓ 卖压衰减：已确认</div>
    <div class="d">跌停从 11-19 的 72 家一路降到今天的 0 家，恐慌抛压已消化。这一步 PASS。</div>
  </div>
  <div class="feat grn">
    <div class="t">✓ 修复确认：今日首次达标</div>
    <div class="d">广度 84.9% 远超 45% 门槛，赚钱效应（昨涨停股上涨比 60%）连续 2 日在 50% 线上，量能 +6.7pp 正向。修复确认连续 2 日的纪律正在满足中。</div>
  </div>
  <div class="feat warn">
    <div class="t">✗ 尚未进攻：等收盘确认</div>
    <div class="d">午盘读数与收盘方向一致率仅约五六成，14:30 才是主决策点。且权重滞涨、上证未收复 3500，反弹结构仍偏成长超跌修复，未到全面进攻。</div>
  </div>
  <ul class="brief">
    <li><b>程序判定</b>：状态由 S3 升至 <em>S4</em>，午盘建议仓位 <em>10%</em>（早盘预警口径、已打折）；上一交易日 S4 基准 20%。</li>
    <li><b>纪律</b>：持仓维持防守、不追高；盘中不人工切换仓位，等 14:30 主决策点、收盘交程序确认。</li>
    <li><b>转强信号</b>：14:30 仍在高位 + 量能维持 + 上证收复 3500 → 程序上调仓位；反之若冲高回落重演 11-24，则维持防守。</li>
    <li><b>不做什么</b>：不砍最低点、不追涨停、不在午盘情绪高点加仓。</li>
  </ul>
</div>

<div class="card disc">本报告由数据自动生成，仅供研究参考，不构成任何投资建议。数据来源：新浪财经 / 东方财富 / 腾讯分时交叉验证。</div>

</div>
</body>
</html>
'''

open(OUT,'w',encoding='utf-8').write(html)
print('WROTE', OUT, len(html), 'bytes')
