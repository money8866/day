# -*- coding: utf-8 -*-
# 生成静态 SVG 降级图（Chart.js 加载失败时可见），颜色用 CSS 变量类，适配深浅主题
import json

data = json.load(open(r"d:\mystock\solo\tdx_300364.json", encoding="utf-8"))
rows = [r for r in data["rows"] if 20260701 <= r["date"] <= 20260904]
n = len(rows)

W, H = 640, 500
padL, padR, padT, padB = 8, 8, 6, 6

# ---- price plot: top region ----
pyt = padT
ph = 250
pbottom = pyt + ph
cmin = min(r["close"] for r in rows)
cmax = max(r["close"] for r in rows)
px = [padL + i * (W - padL - padR) / (n - 1) for i in range(n)]
def P(r):
    return pbottom - (r["close"] - cmin) / (cmax - cmin) * ph

close_pts = " ".join(f"{px[i]:.1f},{P(rows[i]):.1f}" for i in range(n))
area = f"M{px[0]:.1f},{pbottom} L" + " L".join(f"{px[i]:.1f},{P(rows[i]):.1f}" for i in range(n)) + f" L{px[-1]:.1f},{pbottom} Z"

# ---- volume plot: bottom region ----
vt = pbottom + 10
vh = 200 - 10
vbottom = H - padB
vmax = max(r["volume"] for r in rows)
bw = (W - padL - padR) / n * 0.7
bars = []
for i, r in enumerate(rows):
    hh = max(1.5, r["volume"] / vmax * vh)
    x = px[i] - bw / 2
    y = vbottom - hh
    cls = "fbUp" if (r["pct"] or 0) > 0 else "fbDown"
    bars.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{hh:.1f}"/>')

# 三条虚线相位分隔线: 07-31(idx22 波一起点) / 08-10(回调起点 idx28) / 08-31(二波起点 idx43)
def idx_of(d):
    return next(i for i, r in enumerate(rows) if r["date"] == d)

vline = []
for d in (20260731, 20260810, 20260831):
    x = px[idx_of(d)]
    vline.append(f'<line x1="{x:.1f}" y1="{pyt}" x2="{x:.1f}" y2="{vbottom}" class="fbGuide"/>')

# 涨停点强调
pulse = []
for d in (20260731, 20260831):
    i = idx_of(d)
    pulse.append(f'<circle class="fbPulse" cx="{px[i]:.1f}" cy="{P(rows[i]):.1f}" r="4.5"/>')

svg = f"""<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="中文在线300364量价图">
  <path class="fbArea" d="{area}"/>
  <polyline class="fbLine" points="{close_pts}"/>
  {chr(10)}{chr(32)*2}
  {chr(10)}{chr(32)*2}
  {' '.join(vline)}
  {' '.join(pulse)}
  <g class="fbVol">{' '.join(bars)}</g>
</svg>"""
svg = svg.replace(f"{chr(10)}  \n", "\n")
open(r"d:\mystock\solo\fallback.svg", "w", encoding="utf-8").write(svg)
print("fallback.svg written, len:", len(svg))
