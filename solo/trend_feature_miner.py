"""
趋势走强特征挖掘器 —— 基于历史K线+技术因子的AI共性分析

功能：
  1. 从 SQLite 缓存读取60天K线+技术因子专业版数据
  2. 把数据转换为结构化文本
  3. 用 AI 分析多只股票的走势和指标共性，找出确定性走强的技术特征
  4. 统计"特征日"次日买入后的阶段涨幅（5日/10日/20日）

使用方式：
  python trend_feature_miner.py                 # 使用 STOCK_LIST 中的默认股票
  python trend_feature_miner.py 002602 688525   # 指定股票代码（自动补全后缀）
"""

import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

# =========================
# 路径与配置
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
CACHE_DIR = r"D:\mystock\cache_daily"
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "trend_feature_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================
# 待分析股票列表（可通过命令行覆盖）
# 格式："002602.SZ"、"688525.SH" 或纯6位代码（自动识别后缀）
# =========================
STOCK_LIST = [
    "002602.SZ",   # 世纪华通
    "688525.SH",   # 佰维存储
    "002709.SZ",   # 天赐材料
    "603256.SH",   # 宏和科技
    "300476.SZ",   # 胜宏科技
]

# 分析参数
LOOKBACK_DAYS = 60       # 回溯天数
FUTURE_DAYS = [5, 10, 20]  # 统计次日买入后的阶段涨幅


# =========================
# 工具函数
# =========================

def normalize_ts_code(code: str) -> str:
    """将股票代码标准化为 ts_code 格式（如 002602 → 002602.SZ）"""
    code = code.strip().upper()
    if '.' in code:
        return code
    if len(code) != 6:
        return code
    if code.startswith(('60', '68', '51', '11', '13', '90')):
        return f"{code}.SH"
    if code.startswith(('00', '30', '15', '16', '20', '30')):
        return f"{code}.SZ"
    if code.startswith(('43', '83', '87', '92')):
        return f"{code}.BJ"
    return f"{code}.SZ"


def deepseek_chat(prompt: str, use_flash: bool = False) -> str:
    """DeepSeek API 调用"""
    if not DEEPSEEK_API_KEY:
        print("[警告] 未配置 DEEPSEEK_API_KEY，跳过 AI 分析")
        return ""
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    model = "deepseek-v4-flash" if use_flash else "deepseek-v4-pro"
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是A股顶级量化分析师和技术面专家，擅长从历史走势中提炼高胜率的技术形态特征。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=60)
        if r.status_code != 200:
            print(f"[DeepSeek 错误] {r.status_code}: {r.text[:200]}")
            return ""
        return r.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"[DeepSeek 异常] {e}")
        return ""


# =========================
# 数据读取层
# =========================

def get_stock_data(ts_code: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
    """
    从 SQLite 缓存读取股票K线+技术因子数据

    Args:
        ts_code: 股票代码（如 002602.SZ）
        lookback_days: 回溯交易日天数

    Returns:
        DataFrame 按 trade_date 升序，包含 OHLCV + 全套技术指标
    """
    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库不存在: {DB_PATH}")
        return None

    conn = sqlite3.connect(DB_PATH)
    try:
        # 先取最近 N 天的数据（用倒序 LIMIT 再正序）
        df = pd.read_sql_query(
            """
            SELECT * FROM stk_factor_pro
            WHERE ts_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            conn,
            params=(ts_code, lookback_days + 30)
        )
    finally:
        conn.close()

    if df.empty:
        print(f"[警告] {ts_code} 无缓存数据")
        return None

    df = df.sort_values('trade_date').reset_index(drop=True)
    return df.tail(lookback_days).reset_index(drop=True)


# =========================
# 数据转文本层
# =========================

def df_to_text_summary(df: pd.DataFrame, ts_code: str) -> str:
    """
    将60天K线+技术因子数据转换为AI易读的结构化文本

    策略：
    - 整体概况：区间涨跌幅、最大回撤、成交量变化
    - 关键节点：每周OHLCV汇总 + 关键技术指标快照
    - 最新20天：逐日明细（价格、成交量、主要指标）
    - 指标对比：均线排列、MACD状态、RSI位置、BOLL位置
    """
    if df is None or len(df) == 0:
        return f"{ts_code}: 无数据"

    name = ts_code

    # ---- 整体概况 ----
    first_close = df.iloc[0]['close']
    last_close = df.iloc[-1]['close']
    total_return = (last_close / first_close - 1) * 100
    high_max = df['high'].max()
    low_min = df['low'].min()
    max_drawdown = (low_min / high_max - 1) * 100
    vol_ratio = df.iloc[-10:]['vol'].mean() / max(df.iloc[:20]['vol'].mean(), 1)

    lines = [
        f"=== 股票: {name} ===",
        f"区间: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']} ({len(df)}个交易日)",
        f"整体涨跌幅: {total_return:+.2f}%",
        f"区间最高: {high_max:.2f}  最低: {low_min:.2f}  最大回撤: {max_drawdown:.2f}%",
        f"近10日量能/前20日量能: {vol_ratio:.2f}倍",
        "",
    ]

    # ---- 周线汇总（每5天一个节点） ----
    lines.append("【周度价格与量能】")
    step = 5
    for i in range(0, len(df), step):
        week_df = df.iloc[i:i+step]
        if len(week_df) == 0:
            continue
        w_date = week_df.iloc[-1]['trade_date']
        w_open = week_df.iloc[0]['open']
        w_high = week_df['high'].max()
        w_low = week_df['low'].min()
        w_close = week_df.iloc[-1]['close']
        w_vol = week_df['vol'].mean() / 10000  # 万手
        w_chg = (w_close / w_open - 1) * 100
        lines.append(
            f"  {w_date}: O={w_open:.2f} H={w_high:.2f} L={w_low:.2f} C={w_close:.2f} "
            f"涨跌={w_chg:+.2f}% 均量={w_vol:.1f}万手"
        )
    lines.append("")

    # ---- 关键技术指标快照（每10天一个节点） ----
    lines.append("【技术指标快照（每10天）】")
    lines.append(f"  日期        MA5    MA10   MA20   MA60   MACD-DIF  MACD-DEA  MACD柱  RSI6   RSI12  RSI24  KDJ-K  KDJ-D  BOLL上  BOLL中  BOLL下")
    for i in range(0, len(df), 10):
        row = df.iloc[i]
        lines.append(
            f"  {row['trade_date']}  "
            f"{row.get('ma_bfq_5', 0):.2f}  "
            f"{row.get('ma_bfq_10', 0):.2f}  "
            f"{row.get('ma_bfq_20', 0):.2f}  "
            f"{row.get('ma_bfq_60', 0):.2f}  "
            f"{row.get('macd_dif_bfq', 0):+.4f}  "
            f"{row.get('macd_dea_bfq', 0):+.4f}  "
            f"{row.get('macd_bfq', 0):+.4f}  "
            f"{row.get('rsi_bfq_6', 0):.1f}  "
            f"{row.get('rsi_bfq_12', 0):.1f}  "
            f"{row.get('rsi_bfq_24', 0):.1f}  "
            f"{row.get('kdj_k_bfq', 0):.1f}  "
            f"{row.get('kdj_d_bfq', 0):.1f}  "
            f"{row.get('boll_upper_bfq', 0):.2f}  "
            f"{row.get('boll_mid_bfq', 0):.2f}  "
            f"{row.get('boll_lower_bfq', 0):.2f}"
        )
    # 最新一天也输出
    if len(df) % 10 != 0:
        row = df.iloc[-1]
        lines.append(
            f"  {row['trade_date']}  "
            f"{row.get('ma_bfq_5', 0):.2f}  "
            f"{row.get('ma_bfq_10', 0):.2f}  "
            f"{row.get('ma_bfq_20', 0):.2f}  "
            f"{row.get('ma_bfq_60', 0):.2f}  "
            f"{row.get('macd_dif_bfq', 0):+.4f}  "
            f"{row.get('macd_dea_bfq', 0):+.4f}  "
            f"{row.get('macd_bfq', 0):+.4f}  "
            f"{row.get('rsi_bfq_6', 0):.1f}  "
            f"{row.get('rsi_bfq_12', 0):.1f}  "
            f"{row.get('rsi_bfq_24', 0):.1f}  "
            f"{row.get('kdj_k_bfq', 0):.1f}  "
            f"{row.get('kdj_d_bfq', 0):.1f}  "
            f"{row.get('boll_upper_bfq', 0):.2f}  "
            f"{row.get('boll_mid_bfq', 0):.2f}  "
            f"{row.get('boll_lower_bfq', 0):.2f}"
        )
    lines.append("")

    # ---- 最近20天逐日明细 ----
    recent = df.tail(20).reset_index(drop=True)
    lines.append("【最近20个交易日明细】")
    lines.append(f"  日期        开盘    最高    最低    收盘    涨跌幅%   成交量(万)  换手率%  MA5    MA10   MA20   MACD柱  RSI6")
    for _, row in recent.iterrows():
        lines.append(
            f"  {row['trade_date']}  "
            f"{row['open']:.2f}  "
            f"{row['high']:.2f}  "
            f"{row['low']:.2f}  "
            f"{row['close']:.2f}  "
            f"{row.get('pct_chg', 0):+6.2f}  "
            f"{row['vol']/10000:8.1f}  "
            f"{row.get('turnover_rate', 0):5.2f}  "
            f"{row.get('ma_bfq_5', 0):.2f}  "
            f"{row.get('ma_bfq_10', 0):.2f}  "
            f"{row.get('ma_bfq_20', 0):.2f}  "
            f"{row.get('macd_bfq', 0):+.4f}  "
            f"{row.get('rsi_bfq_6', 0):.1f}"
        )
    lines.append("")

    # ---- 均线排列状态 ----
    last = df.iloc[-1]
    ma5 = last.get('ma_bfq_5', 0)
    ma10 = last.get('ma_bfq_10', 0)
    ma20 = last.get('ma_bfq_20', 0)
    ma60 = last.get('ma_bfq_60', 0)
    close = last['close']

    if ma5 > ma10 > ma20 > ma60 and close > ma5:
        ma_arrange = "完美多头排列（收盘价>MA5>MA10>MA20>MA60）"
    elif ma5 > ma10 > ma20 and close > ma10:
        ma_arrange = "多头排列（MA5>MA10>MA20）"
    elif ma5 < ma10 < ma20 < ma60:
        ma_arrange = "空头排列（MA5<MA10<MA20<MA60）"
    else:
        ma_arrange = "均线纠缠"

    lines.append(f"【最新均线状态】 {ma_arrange}")
    lines.append(
        f"  收盘价={close:.2f}  MA5={ma5:.2f}  MA10={ma10:.2f}  "
        f"MA20={ma20:.2f}  MA60={ma60:.2f}"
    )

    # MACD 状态
    dif = last.get('macd_dif_bfq', 0)
    dea = last.get('macd_dea_bfq', 0)
    macd_bar = last.get('macd_bfq', 0)
    if dif > dea and dif > 0:
        macd_status = "MACD金叉且零轴上方（强势）"
    elif dif > dea:
        macd_status = "MACD金叉但零轴下方（弱势反弹）"
    elif dif < dea and dif < 0:
        macd_status = "MACD死叉且零轴下方（弱势）"
    else:
        macd_status = "MACD死叉零轴上方（强势调整）"
    lines.append(f"【最新MACD状态】 {macd_status}  DIF={dif:+.4f}  DEA={dea:+.4f}  柱={macd_bar:+.4f}")

    # RSI 状态
    rsi6 = last.get('rsi_bfq_6', 0)
    if rsi6 > 80:
        rsi_status = "RSI6超买（>80）"
    elif rsi6 > 60:
        rsi_status = "RSI6强势区（60~80）"
    elif rsi6 > 40:
        rsi_status = "RSI6中性区（40~60）"
    elif rsi6 > 20:
        rsi_status = "RSI6弱势区（20~40）"
    else:
        rsi_status = "RSI6超卖（<20）"
    lines.append(f"【最新RSI状态】 {rsi_status}  RSI6={rsi6:.1f}  RSI12={last.get('rsi_bfq_12', 0):.1f}")

    # BOLL 位置
    boll_upper = last.get('boll_upper_bfq', 0)
    boll_mid = last.get('boll_mid_bfq', 0)
    boll_lower = last.get('boll_lower_bfq', 0)
    if close > boll_upper:
        boll_status = "收盘价突破上轨（强势突破）"
    elif close > boll_mid:
        boll_status = "收盘价在中轨与上轨之间（偏强）"
    elif close > boll_lower:
        boll_status = "收盘价在中轨与下轨之间（偏弱）"
    else:
        boll_status = "收盘价跌破下轨（超跌）"
    lines.append(f"【最新BOLL状态】 {boll_status}")

    # 量能状态
    vol_ratio_val = last.get('volume_ratio', 0)
    turnover = last.get('turnover_rate', 0)
    lines.append(f"【最新量能】 量比={vol_ratio_val:.2f}  换手率={turnover:.2f}%")

    lines.append("")
    lines.append("=" * 50)
    lines.append("")

    return "\n".join(lines)


# =========================
# 量化统计层：次日买入后阶段涨幅
# =========================

def calc_future_returns(df: pd.DataFrame, signal_days: List[int],
                        future_windows: List[int] = None) -> Dict:
    """
    计算信号日次日买入后的阶段涨幅

    Args:
        df: 全量K线数据
        signal_days: 信号日的索引列表（df中的位置）
        future_windows: 统计窗口（交易日数），默认 [5, 10, 20]

    Returns:
        {
            window: {
                'avg_return': 平均涨幅,
                'win_rate': 胜率,
                'max_return': 最大涨幅,
                'min_return': 最大跌幅,
                'signals': [{'day': 日期, 'entry': 买入价, 'returns': {窗口: 涨幅}} ...]
            }
        }
    """
    if future_windows is None:
        future_windows = FUTURE_DAYS

    results = {}
    signals_detail = []

    for sig_idx in signal_days:
        if sig_idx + 1 >= len(df):
            continue  # 没有次日数据
        entry_idx = sig_idx + 1
        entry_price = df.iloc[entry_idx]['close']
        signal_date = df.iloc[sig_idx]['trade_date']
        entry_date = df.iloc[entry_idx]['trade_date']

        sig_data = {
            'signal_date': signal_date,
            'entry_date': entry_date,
            'entry_price': entry_price,
            'returns': {}
        }

        for w in future_windows:
            future_idx = entry_idx + w
            if future_idx >= len(df):
                # 数据不足，用最后一天
                future_idx = len(df) - 1
            future_price = df.iloc[future_idx]['close']
            ret = (future_price / entry_price - 1) * 100
            sig_data['returns'][w] = round(ret, 2)

        signals_detail.append(sig_data)

    for w in future_windows:
        rets = [s['returns'].get(w) for s in signals_detail if w in s['returns']]
        if not rets:
            results[w] = {
                'avg_return': 0,
                'win_rate': 0,
                'count': 0,
                'max_return': 0,
                'min_return': 0,
            }
            continue
        wins = sum(1 for r in rets if r > 0)
        results[w] = {
            'avg_return': round(np.mean(rets), 2),
            'win_rate': round(wins / len(rets) * 100, 1),
            'count': len(rets),
            'max_return': round(max(rets), 2),
            'min_return': round(min(rets), 2),
            'returns_list': rets,
        }

    results['signals'] = signals_detail
    return results


# =========================
# AI 分析层
# =========================

def analyze_common_features(stocks_text: Dict[str, str]) -> str:
    """
    让 AI 分析多只股票的走势和指标共性

    Args:
        stocks_text: {ts_code: 文本描述}

    Returns:
        AI 分析结果文本
    """
    prompt = f"""
你是A股顶级量化分析师和技术面形态专家。我将给你提供{len(stocks_text)}只近期趋势走强股票的60天K线和技术指标数据。
请深度分析它们的共同技术特征，找出"确定性走强"的日线级别信号模式。

【分析要求】
1. 找出这些股票在启动上涨前的共同技术形态特征（均线排列、MACD状态、RSI位置、量能变化、BOLL位置等）
2. 识别"确定性走强日"的典型技术指标组合（即那一天的指标特征，使得次日买入大概率获利）
3. 总结出3-5个最核心的共性技术特征，按重要性排序
4. 每个特征请说明：
   - 特征描述（具体指标数值范围）
   - 出现位置（上涨初期/中期/加速期）
   - 对后续涨幅的预测力
5. 如果不同股票的特征差异较大，请分类讨论

【股票数据】
"""

    for code, text in stocks_text.items():
        prompt += f"\n\n{text}\n"

    prompt += """
【输出格式要求】
请用结构化 Markdown 输出，包含以下章节：

## 一、核心共性特征（TOP 5）
按重要性排序，每个特征包含：
- 特征名称
- 技术指标条件（具体数值）
- 形态描述
- 出现频率（几只股票出现）

## 二、"确定性走强日"典型形态
描述那一天的完整技术面画像：
- 价格形态（K线、位置）
- 均线系统
- MACD状态
- 量能特征
- RSI/KDJ位置
- BOLL位置

## 三、分阶段特征演化
从启动→加速→见顶，各阶段的典型指标变化

## 四、操作建议
基于这些共性特征，给出次日买入的具体判断标准

请深度分析，不要泛泛而谈，要有具体的数值条件和可操作的判断标准。
"""

    print(f"\n[AI分析] 正在分析 {len(stocks_text)} 只股票的共性特征...")
    print(f"[AI分析] 提示词长度: {len(prompt)} 字符")

    result = deepseek_chat(prompt, use_flash=False)
    return result


def refine_features_with_statistics(ai_result: str, stock_data: Dict[str, pd.DataFrame]) -> str:
    """
    结合实际量化统计，让 AI 二次迭代优化特征
    （先做特征统计，再把统计结果喂给 AI）
    """
    # 做一些基础统计作为补充
    stats_summary = []
    for code, df in stock_data.items():
        if df is None or len(df) == 0:
            continue
        last = df.iloc[-1]
        stats_summary.append({
            'code': code,
            'close_vs_ma5': (last['close'] / last.get('ma_bfq_5', 1) - 1) * 100,
            'close_vs_ma20': (last['close'] / last.get('ma_bfq_20', 1) - 1) * 100,
            'rsi6': last.get('rsi_bfq_6', 0),
            'macd_bar': last.get('macd_bfq', 0),
            'volume_ratio': last.get('volume_ratio', 0),
            'turnover_rate': last.get('turnover_rate', 0),
        })

    if not stats_summary:
        return ai_result

    stats_df = pd.DataFrame(stats_summary)
    stats_text = f"""
【补充量化统计】（{len(stats_summary)}只股票的最新一天指标分布）

1. 收盘价与MA5的偏离度:
   - 均值: {stats_df['close_vs_ma5'].mean():+.2f}%
   - 中位数: {stats_df['close_vs_ma5'].median():+.2f}%
   - 范围: [{stats_df['close_vs_ma5'].min():+.2f}%, {stats_df['close_vs_ma5'].max():+.2f}%]

2. 收盘价与MA20的偏离度:
   - 均值: {stats_df['close_vs_ma20'].mean():+.2f}%
   - 中位数: {stats_df['close_vs_ma20'].median():+.2f}%
   - 范围: [{stats_df['close_vs_ma20'].min():+.2f}%, {stats_df['close_vs_ma20'].max():+.2f}%]

3. RSI6:
   - 均值: {stats_df['rsi6'].mean():.1f}
   - 中位数: {stats_df['rsi6'].median():.1f}
   - 范围: [{stats_df['rsi6'].min():.1f}, {stats_df['rsi6'].max():.1f}]
   - >70的比例: {(stats_df['rsi6'] > 70).mean()*100:.0f}%

4. MACD柱:
   - 均值: {stats_df['macd_bar'].mean():+.4f}
   - >0的比例: {(stats_df['macd_bar'] > 0).mean()*100:.0f}%

5. 量比:
   - 均值: {stats_df['volume_ratio'].mean():.2f}
   - >1.5的比例: {(stats_df['volume_ratio'] > 1.5).mean()*100:.0f}%

6. 换手率:
   - 均值: {stats_df['turnover_rate'].mean():.2f}%
   - 范围: [{stats_df['turnover_rate'].min():.2f}%, {stats_df['turnover_rate'].max():.2f}%]
"""

    # 二次分析：结合量化统计优化特征
    prompt = f"""
基于以下补充的量化统计数据，请对你之前的分析进行修正和深化，
让"确定性走强日"的特征条件更加精确和可量化。

{stats_text}

【你之前的分析】
{ai_result}

【任务】
1. 结合上面的量化统计，修正你之前的特征条件，给出更精确的数值范围
2. 设计一个"确定性走强评分公式"（0~100分），包含各因子的权重和阈值
3. 评分公式的因子应该包括：均线排列分、MACD状态分、RSI位置分、量能分、BOLL位置分、价格位置分

请在原分析基础上补充：
## 五、确定性走强评分公式
包含完整的计算公式和各因子阈值。
"""

    print("[AI分析] 正在进行二次迭代（结合量化统计优化）...")
    refined = deepseek_chat(prompt, use_flash=False)
    return ai_result + "\n\n---\n\n" + refined


# =========================
# 主流程
# =========================

def main():
    # 解析命令行参数
    if len(sys.argv) > 1:
        stock_codes = [normalize_ts_code(c) for c in sys.argv[1:]]
    else:
        stock_codes = [normalize_ts_code(c) for c in STOCK_LIST]

    print("=" * 60)
    print("趋势走强特征挖掘器")
    print("=" * 60)
    print(f"待分析股票 ({len(stock_codes)}只):")
    for c in stock_codes:
        print(f"  - {c}")
    print(f"回溯天数: {LOOKBACK_DAYS}")
    print(f"输出目录: {OUTPUT_DIR}")
    print()

    # 1. 读取数据
    print("=" * 60)
    print("步骤 1/4: 读取缓存数据")
    print("=" * 60)
    stock_data = {}
    for code in stock_codes:
        print(f"  读取 {code} ...", end=" ")
        df = get_stock_data(code, LOOKBACK_DAYS)
        if df is not None:
            stock_data[code] = df
            print(f"OK ({len(df)}天)")
        else:
            print("失败")

    if not stock_data:
        print("[错误] 没有可用数据")
        return

    # 2. 转换文本
    print("\n" + "=" * 60)
    print("步骤 2/4: 转换为结构化文本")
    print("=" * 60)
    stocks_text = {}
    for code, df in stock_data.items():
        text = df_to_text_summary(df, code)
        stocks_text[code] = text
        print(f"  {code}: {len(text)} 字符")

    # 保存文本数据
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    text_file = os.path.join(OUTPUT_DIR, f"stock_data_text_{timestamp}.md")
    with open(text_file, 'w', encoding='utf-8') as f:
        f.write(f"# 趋势走强股票数据文本 ({len(stocks_text)}只)\n\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")
        for code, text in stocks_text.items():
            f.write(text + "\n")
    print(f"\n  文本数据已保存: {text_file}")

    # 3. AI 分析共性
    print("\n" + "=" * 60)
    print("步骤 3/4: AI 共性特征分析")
    print("=" * 60)

    ai_result = analyze_common_features(stocks_text)

    # 二次迭代优化
    if ai_result:
        ai_result = refine_features_with_statistics(ai_result, stock_data)

    # 保存 AI 分析结果
    ai_file = os.path.join(OUTPUT_DIR, f"ai_analysis_{timestamp}.md")
    with open(ai_file, 'w', encoding='utf-8') as f:
        f.write(f"# 趋势走强共性特征AI分析报告\n\n")
        f.write(f"分析股票: {', '.join(stocks_text.keys())}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")
        f.write(ai_result)
    print(f"\n  AI分析结果已保存: {ai_file}")

    # 4. 简单量化统计（近期涨幅统计）
    print("\n" + "=" * 60)
    print("步骤 4/4: 量化统计")
    print("=" * 60)

    # 计算每只股票各阶段的涨幅
    stats = {}
    for code, df in stock_data.items():
        if len(df) < 20:
            continue
        # 以最新一天为基准，往前统计不同阶段的涨幅
        last_close = df.iloc[-1]['close']
        stats[code] = {
            '5日涨幅': (last_close / df.iloc[-6]['close'] - 1) * 100 if len(df) >= 6 else None,
            '10日涨幅': (last_close / df.iloc[-11]['close'] - 1) * 100 if len(df) >= 11 else None,
            '20日涨幅': (last_close / df.iloc[-21]['close'] - 1) * 100 if len(df) >= 21 else None,
            '60日涨幅': (last_close / df.iloc[0]['close'] - 1) * 100,
            '最大回撤': (df['low'].min() / df['high'].max() - 1) * 100,
        }

    stats_file = os.path.join(OUTPUT_DIR, f"stats_{timestamp}.json")
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # 打印统计
    print("\n各股近期涨幅统计:")
    print(f"{'代码':<12} {'5日%':>8} {'10日%':>8} {'20日%':>8} {'60日%':>8} {'最大回撤%':>10}")
    print("-" * 60)
    for code, s in stats.items():
        print(
            f"{code:<12} "
            f"{s['5日涨幅']:>+8.2f} "
            f"{s['10日涨幅']:>+8.2f} "
            f"{s['20日涨幅']:>+8.2f} "
            f"{s['60日涨幅']:>+8.2f} "
            f"{s['最大回撤']:>+10.2f}"
        )
    print(f"\n  统计数据已保存: {stats_file}")

    # 最终汇总
    print("\n" + "=" * 60)
    print("分析完成！生成文件：")
    print(f"  1. 文本数据: {text_file}")
    print(f"  2. AI分析: {ai_file}")
    print(f"  3. 量化统计: {stats_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
