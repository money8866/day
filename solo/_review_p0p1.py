# -*- coding: utf-8 -*-
"""
A/B 回测：验证 P1(平台紧凑度+上方阻力, 门控) 对"突破后假突破/盘整"的过滤效果；
并检验 P0(大周期趋势硬门槛) 是否适合当前弱市环境。
数据：stock_cache 三窄表缓存派生（daily_cache+daily_basic_cache+adj_factor_cache，qfq 前复权）
事件：2026-06-01 ~ 2026-08-07 间"创20日新高突破"事件(近似今日突破池)，T+1开盘买入持有20日
口径声明：
  1) 近似池≠线上完整 strategy 全条件(无ST名单/主题共振)，仅用于衡量 P0/P1 因子的判别力
  2) 跨除权事件(未来20日 adj_factor 变化)丢弃，规避 qfq 锚点漂移
  3) 已用 total_mv≥80亿 / 未涨停 / 站上MA20 等近似条件对齐正式池门槛
"""
import sqlite3
import numpy as np
import pandas as pd

from stock_cache import cached_stk_factor_compat

DB = r"D:\mystock\cache_daily\stock_data.db"
START, END = "20250801", "20260904"
EV_LO, EV_HI = "20260601", "20260807"
HOLD = 20


def is_a(code):
    return (code.startswith(('600', '601', '603', '605', '000', '001', '002', '003',
                             '300', '301', '688')) and not code.endswith('.BJ'))


def limit_up_ratio(code):
    return 1.198 if code.startswith(('3', '688', '689')) else 1.098


conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
codes = [r[0] for r in conn.execute(
    "SELECT DISTINCT ts_code FROM daily_cache WHERE trade_date >= ?", (EV_LO,))]
codes = [c for c in codes if is_a(c)]
print(f"股票池 {len(codes)} 只")

events = []
excluded_drift = 0
dropped_short = 0

for code in codes:
    df = cached_stk_factor_compat(code, START, END, silent=True)
    if df is None or df.empty:
        continue
    if len(df) < 200:
        continue
    C = df["close_qfq"].values.astype(float)
    O = df["open_qfq"].values.astype(float)
    H = df["high_qfq"].values.astype(float)
    L = df["low_qfq"].values.astype(float)
    V = df["vol"].values.astype(float)
    MV = df["total_mv"].values.astype(float) / 10000.0
    ADJ = df["adj_factor"].values.astype(float)
    dates = df["trade_date"].values
    n = len(df)
    lim = limit_up_ratio(code)

    ma5 = pd.Series(C).rolling(5).mean().values
    ma10 = pd.Series(C).rolling(10).mean().values
    ma20 = pd.Series(C).rolling(20).mean().values
    ma120 = pd.Series(C).rolling(120).mean().values

    is_peak = np.zeros(n, dtype=bool)
    for j in range(3, n):
        s, e = j - 3, min(n, j + 4)
        if H[j] >= H[s:e].max() and H[j] > H[j - 1] * 1.005:
            is_peak[j] = True

    for i in range(160, n - HOLD - 1):
        d = str(dates[i])
        if not (EV_LO <= d <= EV_HI):
            continue
        if MV[i] < 80:
            continue
        if C[i] / C[i - 1] - 1 >= lim - 1e-4:
            continue
        if H[i - 20:i].max() / L[i - 20:i].min() - 1 > 1.8:
            continue
        if not (C[i] > ma5[i] and C[i] > ma10[i] and C[i] > ma20[i]):
            continue
        if C[i] / ma20[i] - 1 > 0.30:
            continue
        if C[i] <= H[i - 20:i].max() * 0.998:
            continue
        v5 = V[i - 5:i].mean()
        if V[i] < v5 * 1.3:
            continue
        if ADJ[i + 1] != ADJ[i + HOLD]:
            excluded_drift += 1
            continue

        # ---- P0 ----
        ma120_slope = (ma120[i] - ma120[i - 10]) / ma120[i] if ma120[i] > 0 else 0.0
        p0_trend_bad = (C[i] <= ma120[i]) and (ma120_slope < 0)
        hh120 = H[i - 119:i + 1].max()
        p0_dd_bad = (C[i] / hh120 - 1) < -0.45
        p0_pass = not (p0_trend_bad or p0_dd_bad)

        # ---- P1（与 calc_unified 门控版一致）----
        peaks = H[(is_peak) & (np.arange(n) >= i - 119) & (np.arange(n) < i - 1)]
        above = peaks[peaks > C[i]]
        res_dist = (above.min() / C[i] - 1) * 100 if above.size else None
        pmax = H[i - 20:i].max()
        pmin = L[i - 20:i].min()
        plat_range = (pmax - pmin) / pmin * 100 if pmin > 0 else 0
        plat_pull = (C[i] / pmin - 1) * 100 if pmin > 0 else 0
        if plat_range < 12 and plat_pull < 15:
            plat_score = 6
        elif plat_range < 18 and plat_pull < 15:
            plat_score = 4
        elif plat_pull >= 18:
            plat_score = -6
        elif plat_range >= 25:
            plat_score = -4
        else:
            plat_score = 0
        plat_ok = plat_score >= 4
        res_bad = (res_dist is not None) and (res_dist < 3.0)
        res_near = (res_dist is not None) and (res_dist < 8.0)
        p1_stage = ("平台不合格" if not plat_ok
                    else ("贴顶" if res_bad else ("空间不足" if res_near else "平台好+空间够")))

        buy = O[i + 1]
        if buy <= 0:
            continue
        fut_c = C[i + 1:i + HOLD + 1]
        fut_h = H[i + 1:i + HOLD + 1]
        fut_l = L[i + 1:i + HOLD + 1]
        if len(fut_c) < HOLD:
            dropped_short += 1
            continue
        ret20 = (fut_c[-1] / buy - 1) * 100
        mfe = (fut_h.max() / buy - 1) * 100
        mae = (fut_l.min() / buy - 1) * 100

        events.append({
            "date": d, "code": code, "ret20": ret20, "mfe": mfe, "mae": mae,
            "p0_trend_bad": p0_trend_bad, "p0_dd_bad": p0_dd_bad, "p0_pass": p0_pass,
            "plat_ok": plat_ok, "res_bad": res_bad, "p1_stage": p1_stage,
        })

conn.close()
print(f"事件数(近似池): {len(events)} | 剔除跨除权 {excluded_drift} | 尾部不足 {dropped_short}")

ev = pd.DataFrame(events)
ev["after_p1"] = ev["plat_ok"]                                   # 正式口径: 仅平台质量为主因子(贴顶保留)
ev["after_p1_p0strong"] = ev["after_p1"] & ev["p0_pass"]         # 若强市开启P0
ev["p1_stage"] = np.where(ev["plat_ok"], "平台合格(贴顶保留)", "平台不合格(U型/大振幅)")

pd.set_option("display.width", 220)
pd.set_option("display.unicode.east_asian_width", True)


def stat(mask, label):
    s = ev[mask]
    if len(s) == 0:
        print(f"{label:32} n=0")
        return
    r = s["ret20"]
    print(f"{label:32} n={len(s):4d} | 均值{r.mean():+6.1f}% 中位{r.median():+6.1f}% "
          f"| 胜率{(r>0).mean()*100:4.0f}% | 盘整(0~5%){(r.between(0,5).mean())*100:4.0f}% "
          f"| 亏损率{(r<0).mean()*100:4.0f}% | 大亏(mae<=-8%){(s['mae']<=-8).mean()*100:4.0f}% "
          f"| MFE{s['mfe'].mean():+5.1f}% MAE{s['mae'].mean():+5.1f}%")


print("\n===== A/B 对比（近似池 = 基线） =====")
stat(ev["ret20"].notna(), "[基线] 近似池全部(含U型反抽)")
stat(ev["after_p1"], "[+P1] 仅保留平台合格(建议口径)")
stat(ev["after_p1_p0strong"], "[+P0+P1] 若强市开启P0")

print("\n===== P1-平台质量 判别力 =====")
stat(ev["p1_stage"] == "平台不合格(U型/大振幅)", "P1-平台不合格(U型深回调/大振幅)")
stat(ev["p1_stage"] == "平台合格(贴顶保留)", "P1-平台合格(横盘蓄势,含贴顶)")

print("\n===== P0 在弱市的判别力（结论参考） =====")
stat(ev["p0_pass"], "P0保留组(高位/趋势向上)")
stat(~ev["p0_pass"], "P0剔除组(下行MA120/深回撤>45%)")
print("注: 若剔除组反而更好 → 当前弱市超跌反弹是主要有效策略，P0 应在弱市关闭")

print("\n===== 中文在线 0831 case（窗口外，仅验证剔除判定） =====")
zd = cached_stk_factor_compat('300364.SZ', START, END, silent=True)
if zd is None or zd.empty:
    raise SystemExit("300364.SZ 数据缺失")
C = zd["close_qfq"].values.astype(float); H = zd["high_qfq"].values.astype(float)
L = zd["low_qfq"].values.astype(float)
i = int(np.where((zd["trade_date"].values == "20260831"))[0][0])
ma120 = pd.Series(C).rolling(120).mean().values
ma120_s = (ma120[i] - ma120[i - 10]) / ma120[i]
hh120 = H[i - 119:i + 1].max()
pk = []
for j in range(max(3, i - 119), i - 1):
    if H[j] >= H[j - 3:min(len(H), j + 4)].max() and H[j] > H[j - 1] * 1.005:
        pk.append(H[j])
above = [p for p in pk if p > C[i]]
res_dist = (min(above) / C[i] - 1) * 100 if above else None
pmax, pmin = H[i - 20:i].max(), L[i - 20:i].min()
plat_range = (pmax - pmin) / pmin * 100
plat_pull = (C[i] / pmin - 1) * 100
print(f"收盘{C[i]:.2f} 距MA120:{(C[i]/ma120[i]-1)*100:+.1f}% MA120斜率{ma120_s:+.3f} | 120日回撤{(C[i]/hh120-1)*100:+.1f}%")
print(f"P0: MA120下行={C[i]<=ma120[i] and ma120_s<0} 深回撤>45%={(C[i]/hh120-1)<-0.45} → "
      f"{'P0剔除' if (C[i]<=ma120[i] and ma120_s<0) or (C[i]/hh120-1)<-0.45 else 'P0保留'}")
print(f"上方最近阻力距当前: {res_dist:.2f}% → {'贴顶(剔除)' if res_dist is not None and res_dist<3 else '阻力项放行'}")
_plat_str = "平台合格(保留)" if (plat_range < 18 and plat_pull < 15) else "平台不合格(剔除)"
print(f"前20日平台振幅{plat_range:.1f}% 自平台低点已涨{plat_pull:.1f}% → {_plat_str}")
p1_bad = (res_dist is not None and res_dist < 3) or not (plat_range < 18 and plat_pull < 15)
print(f"综合: {'P1剔除 → 不再进入今日突破池' if p1_bad else 'P1放行'}")
