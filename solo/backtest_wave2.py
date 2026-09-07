# -*- coding: utf-8 -*-
"""
量价二波 统计验证 —— 基于通达信本地日线(创业板300/301 + 科创板688/689)
三组对照:
  0_all      全样本基准(所有交易日)
  B_diliang  一波上涨后缩量回调中"地量日"收盘低吸(未要求后续突破)
  A_strong   近期一波(≥25%+强阳日)后当日放量大阳追涨(无健康缩量回调前提)
  C_wave2    完整形态: 一波放量上涨→缩量回调(地量)→放量大阳收复前高 = 二波启动
比较各组买入后 +1/+5/+10 交易日的表现(收盘对收盘, 未计费用)
"""
import os, struct, statistics, math

SZ_DIR = r"C:\new_tdx\vipdoc\sz\lday"
SH_DIR = r"C:\new_tdx\vipdoc\sh\lday"
START_DATE = 20250101      # 信号扫描起点
LB = 45                    # 回看天数上限

def parse_day(path):
    buf = open(path, "rb").read()
    n = len(buf) // 32
    rec = []
    for i in range(n):
        d, o, h, l, c, amt, vol, _r = struct.unpack_from("<IIIIIfII", buf, i * 32)
        rec.append((d, o / 100.0, h / 100.0, l / 100.0, c / 100.0, vol))
    return rec

def collect():
    files = []
    for d in (SZ_DIR, SH_DIR):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            code = fn[:6]
            if fn.endswith(".day") and ((code.startswith("sz30") and code[2:4] in ("30", "31")) or code.startswith("sh68")):
                files.append(os.path.join(d, fn))
    return files

def pct_prev(closes, i):
    return (closes[i] / closes[i - 1] - 1.0) * 100 if i > 0 else 0.0

def analyze_one(fn):
    rec = parse_day(fn)
    n = len(rec)
    if n < 300:
        return None
    dates = [r[0] for r in rec]
    if dates[-1] < 20250601:  # 数据太旧, 无法在2025后产生信号
        return None
    oo = [r[1] for r in rec]; hh = [r[2] for r in rec]; ll = [r[3] for r in rec]
    cc = [r[4] for r in rec]; vv = [r[5] for r in rec]
    # 起点
    s0 = next((i for i, d in enumerate(dates) if d >= START_DATE), None)
    if s0 is None:
        return None
    # 每组结果: (f1, f5, f10, meta)
    out = {"all": [], "B": [], "A": [], "C": []}
    # ---- 0_all 全样本基准 ----
    for i in range(s0, n - 10):
        if cc[i] <= 0:
            continue
        out["all"].append(((cc[i + 1] / cc[i] - 1) * 100,
                           (cc[i + 5] / cc[i] - 1) * 100,
                           (cc[i + 10] / cc[i] - 1) * 100, None))
    # ---- B: 地量日低吸(有"一波+回调"前提) ----
    j = max(s0, 30)
    while j < n - 10:
        if vv[j] > 0 and vv[j] <= min(vv[max(0, j - 20):j]) and vv[j] < vv[j - 1]:
            # 找 p: 回看[j-40, j-1]收盘最高, 且满足回调结构
            lo = max(0, j - 40)
            p = max(range(lo, j), key=lambda q: cc[q])
            if p > j - 1 - 25:   # 峰距地量日不算太远(≤25根)
                pb_len = j - 1 - p
                if 3 <= pb_len <= 20:
                    t0 = min(range(lo, p + 1), key=lambda q: cc[q])
                    rise = cc[p] / cc[t0] - 1
                    mx = max((cc[q] / cc[q - 1] - 1) * 100 for q in range(t0 + 1, p + 1))
                    if rise >= 0.25 and mx >= 9.5:
                        dd = (cc[p] - cc[j - 1]) / cc[p]
                        pb_low = min(ll[p + 1:j])
                        if 0.04 <= dd <= 0.30 and pb_low > ll[t0]:
                            pk_vol = max(vv[t0:p + 1])
                            if vv[j] <= 0.6 * pk_vol:
                                out["B"].append(((cc[j + 1] / cc[j] - 1) * 100,
                                                 (cc[j + 5] / cc[j] - 1) * 100,
                                                 (cc[j + 10] / cc[j] - 1) * 100,
                                                 dict(rise=rise, dd=dd)))
                                j += 11
                                continue
        j += 1

    # ---- C / A: 放量大阳触发日 ----
    j = max(s0, LB)
    while j < n - 10:
        pct = pct_prev(cc, j)
        if pct < 9.5 or vv[j] < 1.6 * vv[j - 1]:
            j += 1
            continue
        # 找 p,t0
        lo = max(0, j - 35)
        p = max(range(lo, j), key=lambda q: cc[q])
        if cc[p] < cc[j] * 0.9 or p < lo:
            j += 1
            continue
        t0 = min(range(lo, p + 1), key=lambda q: cc[q])
        rise = cc[p] / cc[t0] - 1
        if rise < 0.25:
            j += 1
            continue
        mx = max((cc[q] / cc[q - 1] - 1) * 100 for q in range(t0 + 1, p + 1))
        if mx < 9.5:
            j += 1
            continue
        pb_len = j - 1 - p
        isC = False
        metaC = dict(rise=rise)
        if 3 <= pb_len <= 20:
            dd = (cc[p] - cc[j - 1]) / cc[p]
            pb_low = min(ll[p + 1:j])
            reclaim = cc[j] >= cc[p]
            if 0.04 <= dd <= 0.30 and pb_low > ll[t0] and reclaim:
                pk_vol = max(vv[t0:p + 1])
                pb_min = min(vv[p + 1:j])
                pb_avg = sum(vv[p + 1:j]) / pb_len
                wave_avg = sum(vv[t0:p + 1]) / (p - t0 + 1)
                if pb_min <= 0.6 * pk_vol and pb_avg < wave_avg * 0.9:
                    isC = True
                    metaC = dict(rise=rise, dd=dd, pb_min_ratio=pb_min / pk_vol)
        grp = "C" if isC else "A"
        metaC["tp"] = pct
        metaC["dt"] = dates[j]
        out[grp].append(((cc[j + 1] / cc[j] - 1) * 100,
                         (cc[j + 5] / cc[j] - 1) * 100,
                         (cc[j + 10] / cc[j] - 1) * 100,
                         metaC))
        j += 11
    return out

def stats(lst, idx):
    a = [x[idx] for x in lst]
    n = len(a)
    if n == 0:
        return dict(n=0)
    m = sum(a) / n
    sd = statistics.pstdev(a)
    med = statistics.median(a)
    wr = sum(1 for x in a if x > 0) / n * 100
    t = (m / sd * math.sqrt(n)) if sd > 0 else 0.0
    return dict(n=n, mean=m, med=med, wr=wr, t=t)

def main():
    files = collect()
    print("universe files:", len(files))
    agg = {"all": [], "B": [], "A": [], "C": []}
    import random
    samples = []
    for k, fn in enumerate(files):
        try:
            r = analyze_one(fn)
        except Exception:
            continue
        if r is None:
            continue
        for g in agg:
            agg[g].extend(r[g])
        if r["C"]:
            for it in r["C"]:
                samples.append((os.path.basename(fn)[:6], it))
    random.seed(1)
    print("=" * 70)
    print("A组=放量大阳追涨(未缩量回调)  B组=缩量地量日低吸  C组=完整二波启动(回调缩量+放量收复前高)")
    print("基准=全样本全部交易日")
    hz = {0: "+1日", 1: "+5日", 2: "+10日"}
    names = {"all": "0_all基准", "B": "B_地量低吸", "A": "A_直接追涨", "C": "C_完整二波"}
    tbl = {}
    for g in ("all", "B", "A", "C"):
        rows = []
        for idx in (0, 1, 2):
            s = stats(agg[g], idx)
            rows.append(s)
        tbl[g] = rows
    base = [tbl["all"][i]["mean"] if tbl["all"][i]["n"] else 0 for i in range(3)]
    print(f"{'组别':<12}{'N':>7} | {'f1均%':>7}{'f5均%':>8}{'f10均%':>8} | {'f5中位%':>8}{'f5胜率%':>8}{'f5 t值':>8} | {'超基准f5%':>8}")
    for g in ("B", "A", "C"):
        s = tbl[g]
        if not s[1]["n"]:
            continue
        ex5 = s[1]["mean"] - base[1]
        print(f"{names[g]:<12}{s[0]['n']:>7} | {s[0]['mean']:>7.2f}{s[1]['mean']:>8.2f}{s[2]['mean']:>8.2f} | "
              f"{s[1]['med']:>8.2f}{s[1]['wr']:>8.1f}{s[1]['t']:>8.2f} | {ex5:>8.2f}")
    s = tbl["all"]
    print(f"{names['all']:<12}{s[0]['n']:>7} | {s[0]['mean']:>7.2f}{s[1]['mean']:>8.2f}{s[2]['mean']:>8.2f} | "
          f"{s[1]['med']:>8.2f}{s[1]['wr']:>8.1f}{s[1]['t']:>8.2f} | {'—':>8}")
    # C组细分: 涨停二波(触发日≥19.5%) vs 大阳二波(9.5~19.5)
    c_up = [x for x in agg["C"] if x[3].get("tp", 0) >= 19.5]
    c_yang = [x for x in agg["C"] if x[3].get("tp", 0) < 19.5]
    for nm, sub in (("C涨停二波(≥19.5%)", c_up), ("C大阳二波(9.5~19.5%)", c_yang)):
        if sub:
            s = stats(sub, 1)
            print(f"{nm:<18}N={s['n']:>5}  f5均={s['mean']:+.2f}%  中位={s['med']:+.2f}%  胜率={s['wr']:.1f}%  t={s['t']:.2f}")
    # 验证: 300364 是否命中 C(应为2026-08-31附近)
    hit = [(x[3]["dt"], x[0], x[1], x[2]) for x in agg["C"] if x[3].get("dt", 0) >= 20260830]
    print("-" * 70)
    print("C组最近样本(验证探测器):")
    for h in hit[-6:]:
        print(f"  dt={h[0]}  f1={h[1]:+.1f}%  f5={h[2]:+.1f}%  f10={h[3]:+.1f}%")

if __name__ == "__main__":
    main()
