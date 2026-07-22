import json, os, re, sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
sys.path.insert(0, BASE_DIR)
import config

# ============================================================
# 1. 读取大盘状态
# ============================================================
def _load_market_regime(trade_date):
    """从 market_analysis 报告文本读取市场状态"""
    ma_cache_dir = os.path.join(config.BASE_DIR, '..', 'cache_backbone_tushare')
    txt_path = os.path.join(ma_cache_dir, f"market_analysis_{trade_date}.txt")
    if not os.path.exists(txt_path):
        return "震荡轮动期", 50.0, "无大盘分析报告"

    with open(txt_path, encoding='utf-8') as f:
        txt = f.read()

    # 提取总趋势分
    m_ts = re.search(r'总趋势分.*?:\s*(\d+\.?\d*)', txt)
    ts = float(m_ts.group(1)) if m_ts else 50.0
    # 提取市场状态
    m_ms = re.search(r'市场状态:\s*(.+)', txt)
    ms = m_ms.group(1).strip() if m_ms else "震荡轮动期"
    return ms, ts, txt[:200]


# ============================================================
# 2. 加载 V6 和 V8 数据
# ============================================================
def load_v6_v8(trade_date):
    v6_path = os.path.join(CACHE_DIR, f'theme_alpha_v6_result_{trade_date}.json')
    v8_path = os.path.join(CACHE_DIR, f'theme_alpha_v6_result_v8_{trade_date}.json')
    if not os.path.exists(v6_path) or not os.path.exists(v8_path):
        print(f"[ERROR] 缓存文件不存在")
        return None, None
    with open(v6_path, encoding='utf-8') as f:
        v6 = json.load(f)
    with open(v8_path, encoding='utf-8') as f:
        v8 = json.load(f)
    # 构建 v8 索引
    v8_idx = {}
    for t in v8:
        name = t.get('主题') or t.get('theme') or ''
        v8_idx[name] = t
    return v6, v8_idx


# ============================================================
# 3. 融合评分
# ============================================================
def compute_fused(v6_data, v8_idx, market_regime, trend_score):
    """
    融合 V6(前瞻动量) + V8(结构健康)
    
    市场模式:
      - 主升模式 (trend >= 60 + 状态含主升): V6主导，V8防风险
      - 震荡模式 (其他): V8主导，优先避坑
    """
    is_bull = (trend_score >= 60 and '主升' in market_regime) or trend_score >= 75
    
    if is_bull:
        w_v6, w_v8 = 0.65, 0.35
        mode_label = "🟢 主升模式"
    else:
        w_v6, w_v8 = 0.35, 0.65
        mode_label = "🟡 震荡模式"
    
    results = []
    for v6_item in v6_data:
        name = v6_item.get('theme') or v6_item.get('主题') or ''
        v6_score = v6_item.get('composite_score', 0) or 0
        v6_signal = v6_item.get('trade_signal', '') or ''
        v6_fa = v6_item.get('forward_alpha', 0) or 0
        v6_stage = v6_item.get('stage', '') or ''
        
        v8_item = v8_idx.get(name, {})
        v8_score = v8_item.get('V7综合得分', 0) or 0
        v8_dstage = v8_item.get('D阶段', '') or ''
        v8_action = v8_item.get('策略动作', '') or ''
        v8_penalty = v8_item.get('惩罚项说明', '') or ''
        v8_trend = v8_item.get('趋势分', 0) or 0
        v8_capital = v8_item.get('资金分', 0) or 0
        v8_echelon = v8_item.get('梯队分', 0) or 0
        v8_backbone_break = v8_item.get('梯队_中军破位比例', 0) or 0
        
        # ---- 核融合分 ----
        fused = round(w_v6 * v6_score + w_v8 * v8_score, 1)
        
        # ---- 共振/冲突奖惩 ----
        v6_bullish = v6_score >= 65 or v6_signal in ('强买', '强烈看多')
        v8_healthy = ('D8' not in v8_dstage and v8_backbone_break < 30
                      and '哑铃' not in v8_penalty and '假大阳' not in v8_penalty)
        v8_danger = 'D8' in v8_dstage or v8_backbone_break > 50
        v8_penalty_heavy = '哑铃' in v8_penalty or '假大阳' in v8_penalty
        
        bonus = 0
        conflict = ""
        
        if v6_bullish and v8_healthy:
            bonus = 10
            conflict = "✅ 共振"
        elif v6_bullish and v8_danger:
            bonus = -8
            conflict = "⚠️ 冲突(动量↑结构↓)"
        elif v6_bullish and v8_penalty_heavy:
            bonus = -5
            conflict = "⚠️ 谨慎(龙头单飞)"
        elif not v6_bullish and v8_danger:
            bonus = 5
            conflict = "✅ 双弱确认"
        elif v6_bullish and not v8_danger:
            bonus = 0
            conflict = "🔶 动量偏多"
        elif v8_healthy and v6_score < 50:
            bonus = 3
            conflict = "🔶 结构偏多"
        else:
            bonus = 0
            conflict = "⚪ 中性"
        
        fused_final = round(max(0, min(100, fused + bonus)), 1)
        
        # 动作建议
        if is_bull:
            # 主升模式：积极一些
            if fused_final >= 70 and v8_healthy:
                action = "重仓参与"
            elif fused_final >= 60:
                action = "顺势加仓"
            elif fused_final >= 50 and not v8_danger:
                action = "持有观察"
            elif v8_danger:
                action = "减仓避险"
            else:
                action = "观望等待"
        else:
            # 震荡模式：保守一些
            if fused_final >= 70 and v8_healthy:
                action = "核心仓位"
            elif fused_final >= 60 and not v8_danger:
                action = "轻仓参与"
            elif v8_healthy and v6_score >= 50:
                action = "试探建仓"
            elif v8_danger or v8_penalty_heavy:
                action = "回避/清仓"
            else:
                action = "观望等待"
        
        results.append({
            "主题": name,
            "融合分": fused_final,
            "V6分": v6_score,
            "V8分": v8_score,
            "奖惩": bonus,
            "信号": conflict,
            "V6信号": v6_signal,
            "V8阶段": v8_dstage,
            "V8动作": v8_action,
            "V8惩罚": v8_penalty[:40] if v8_penalty else "",
            "操作建议": action,
            "梯队分": v8_echelon,
            "趋势分": v8_trend,
            "资金分": v8_capital,
        })
    
    # 排序
    results.sort(key=lambda x: x["融合分"], reverse=True)
    for i, r in enumerate(results):
        r["排名"] = i + 1
    
    return results, mode_label


# ============================================================
# 4. 打印报告
# ============================================================
def print_report(results, mode_label, trade_date, market_regime):
    print(f"\n{'='*90}")
    print(f"  FUSION RANK - 主题融合排名 ({trade_date})")
    print(f"  {mode_label} | 大盘状态: {market_regime}")
    print(f"{'='*90}")
    print(f"  {'#':<3} {'主题':<16} {'融合分':<6} {'V6':<6} {'V8':<6} {'奖惩':<5} {'信号':<20} {'操作建议':<12}")
    print(f"  {'-'*80}")
    
    top_n = 30
    c1, c2, c3 = 0, 0, 0
    for r in results[:top_n]:
        row = (f"  {r['排名']:<3} {r['主题']:<16} {r['融合分']:<6} {r['V6分']:<6} "
               f"{r['V8分']:<6} {r['奖惩']:<5} {r['信号']:<20} {r['操作建议']:<12}")
        print(row)
        if '共振' in r['信号']:
            c1 += 1
        elif '冲突' in r['信号']:
            c2 += 1
        elif '双弱' in r['信号']:
            c3 += 1
    
    print(f"  {'-'*80}")
    print(f"  共{len(results)}个主题  |  TOP{top_n}: 共振{c1} 冲突{c2} 双弱确认{c3}")
    
    # 打印分类汇总
    print(f"\n  == 分类建议汇总 ==")
    action_groups = {}
    for r in results:
        a = r['操作建议']
        if a not in action_groups:
            action_groups[a] = []
        action_groups[a].append(r)
    
    for action, items in sorted(action_groups.items()):
        names = '、'.join([f"{i['主题']}({i['融合分']})" for i in items[:5]])
        if len(items) > 5:
            names += f"...等{len(items)}个"
        print(f"  [{action}] {names}")
    
    print()


# ============================================================
# 5. 对外接口 — 供 main.py / tushare_quant.py 调用
# ============================================================
def build_fusion_rank(trade_date, market_regime=None, trend_score=None, quiet=False):
    """
    生成融合排名（V6前瞻动量 + V8结构健康）
    
    Parameters
    ----------
    trade_date : str
        交易日 YYYYMMDD
    market_regime : str, optional
        大盘状态，None时自动从market_analysis读取
    trend_score : float, optional
        趋势分，None时自动读取
    quiet : bool
        静默模式（被main.py调用时不重复打印）
    
    Returns
    -------
    list : 融合排名结果列表
    """
    # 读取大盘状态（允许外部传入，方便main.py阶段直接传递）
    if market_regime is None or trend_score is None:
        market_regime, trend_score, _ = _load_market_regime(trade_date)
    
    if not quiet:
        print(f"  [Fusion] 大盘={market_regime}  趋势分={trend_score}")
    
    # 加载 V6/V8
    v6, v8_idx = load_v6_v8(trade_date)
    if v6 is None:
        print("  [Fusion] ERROR: V6/V8缓存不可用")
        return []
    
    # 融合计算
    results, mode_label = compute_fused(v6, v8_idx, market_regime, trend_score)
    
    if not quiet:
        print(f"  [Fusion] {mode_label} | {len(results)}个主题")
    
    # 保存JSON
    out_path = os.path.join(CACHE_DIR, f'theme_fusion_rank_{trade_date}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"meta": {"trade_date": trade_date, "大盘状态": market_regime, "趋势分": trend_score, "模式": mode_label}, "data": results}, f, ensure_ascii=False, indent=2)
    
    if not quiet:
        print(f"  [Fusion] 已保存: {out_path}")
    
    return results, mode_label


# ============================================================
# Main（命令行独立运行）
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fusion Rank - V6+V8 融合排名")
    parser.add_argument("--date", type=str, default=None, help="交易日(YYYYMMDD)")
    args = parser.parse_args()
    
    trade_date = args.date or "20260721"
    
    # 生成融合排名（会同时保存JSON）
    results, mode_label = build_fusion_rank(trade_date)
    
    if not results:
        sys.exit(1)
    
    # 输出报告
    market_regime, trend_score, _ = _load_market_regime(trade_date)
    print_report(results, mode_label, trade_date, market_regime)
