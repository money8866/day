# -*- coding: utf-8 -*-
"""
EGPT (Earnings Growth Pullback Timing) v1.3.1 - 中报预增回踩择时·每日微信推送 (PushPlus)
=========================================
读取最新 enhanced_timing_bull_all 报告，推送调整充分的二波潜力股到微信
版本记录:
  v1.0.0 双确认过滤(形态+风控, 修复中欣氟材误推)
  v1.1.0 AI五要素交易计划(触发/仓位/止损/止盈/失效) + A组绿灯信号强制执行方案
  v1.2.0 缓存过期分级(--date重跑修复) + EGPT命名规范
  v1.3.0 分级绿灯: A组严格双确认(次日可买入) + B组条件信号(⚠️观察/次日观察等回踩中
        评级S/A/B+业绩正+无冲击top3, 需触发确认), 保证非绿灯日期也有信号
  v1.3.1 绿灯B过滤首阳日当天(回踩天数=0): 首阳大阳乖离VWAP过大(梅雁吉祥8/13乖离14.3%
        次日一路下跌-9.9%从未回踩确认), 回踩确认买点当天不可执行属伪候选;
        仅保留已进入回踩结构(回踩天数≥1)形态可确认的标的
"""
import os, sys, re
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv('d:/mystock/config/.env')

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'report_daily')
PUSHPLUS_TOKEN = os.getenv('PUSHPLUS')
PUSHPLUS_URL = 'https://www.pushplus.plus/send'
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'


def call_deepseek(prompt: str, use_flash: bool = True, system: str = None) -> str:
    """调用 DeepSeek 对精选标的做二次分析（严格基于给定数据，禁止编造）"""
    if not DEEPSEEK_API_KEY:
        print('⚠️ 未配置 DEEPSEEK_API_KEY，跳过 AI 二次分析')
        return ''
    try:
        model = 'deepseek-v4-flash' if use_flash else 'deepseek-v4-pro'
        if system is None:
            system = (
                '你是A股顶级短线投资分析师，严格基于用户提供的数据进行分析，'
                '绝不编造任何数据、新闻、游资动向或外部事件。'
                '股票名称和代码必须严格引用用户提供的数据，不得自行修改或臆造。'
                '输出要求：精炼务实，从"哪些最值得关注/各自看点/风险与止损纪律"三个角度给出结论，'
                '每只股票不超过2句话，末尾给出一句总操作提示。'
            )
        data = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.2,
        }
        resp = requests.post(DEEPSEEK_URL, headers={
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json',
        }, json=data, timeout=120)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'⚠️ DeepSeek 调用失败: {e}')
        return ''


def find_latest_report() -> str:
    """找最新的 enhanced_timing_bull_all CSV"""
    files = [f for f in os.listdir(REPORT_DIR) if f.startswith('enhanced_timing_bull_all_') and f.endswith('.csv')]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(REPORT_DIR, files[0])


def format_cash(val) -> str:
    """格式化金额显示"""
    try:
        v = float(val)
        if v >= 10000:
            return f'{v/10000:.0f}万亿'
        elif v >= 1000:
            return f'{v:.0f}'
        return f'{v:.2f}'
    except:
        return '-'


EGPT_NAME = 'EGPT'
EGPT_VERSION = 'v1.3.1'
EGPT_FULLNAME = 'EGPT (Earnings Growth Pullback Timing) - 中报预增回踩择时'


def build_wechat_msg(df: pd.DataFrame, trade_date: str) -> str:
    """
    构建微信推送的 Markdown 消息
    - 仅保留精选标的: S/A级 + 洗盘修复分>=80 + 无兑现冲击
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f'# {EGPT_FULLNAME} {EGPT_VERSION} - 洗盘修复专题')
    lines.append(f'报告日期: {trade_date} | 推送时间: {now}')
    lines.append('')
    lines.append('> **说明**: 洗盘修复分=洗盘形态完整度(满分100)，排名仅代表形态标准程度。')
    lines.append('> 综合评级(S/A/B)看的是趋势/动量/量价等7个因子综合得分，才是真正的强弱排序。')
    lines.append('> 所以修复分高但评级低的(如100分A级)，说明刚启动不久、低吸安全；修复分略低但评级高的(如S级)，说明趋势已确立、确定性更强。')
    lines.append('')

    # ─── ✅ 次日可买入 (回踩中形态 × 综合风控双确认，直接回答"明天能不能买") ───
    # 修复记录 20260808: 中欣氟材(业绩-48.5%/T+0阴线放量3.8x/评分清零)曾被误推"次日可买入"。
    # 形态达标(回踩中+分≥60)只是门槛，必须叠加 enhanced 综合风控:
    #   无兑现冲击 + 修正后评分>0(未被冲击/业绩背离清零) + (评级S/A/B 或 中报业绩正增长)
    # 注: 评级门槛会误杀"突破前夜"形态——美迪西/奥浦迈 20260806 评级D(未突破)但业绩
    #     +507%/+229%、无冲击，次日放量突破升 S/A。评级不再一票否决，业绩方向+兑现冲击
    #     才是拦截核心（伪信号如中欣氟材仍被业绩负/冲击/评分清零拦截）。
    if '次日操作' in df.columns:
        def _parse_growth(v):
            s = str(v).replace('%', '').replace('+', '').strip()
            try:
                return float(s)
            except Exception:
                return float('nan')
        growth = (pd.to_numeric(df['中报业绩亮点'].apply(_parse_growth), errors='coerce')
                  if '中报业绩亮点' in df.columns else pd.Series(float('nan'), index=df.index))
        buy_mask = (
            (df['次日操作'] == '✅ 次日可买入') &
            (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
            (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
            (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0)) &
            (df['修正后胜率分级'] != 'E')
        )
        buy = df[buy_mask].sort_values('回踩买点分', ascending=False)
        # v1.3.0 分级绿灯B(条件信号): "⚠️观察/⚠️次日观察等回踩"中评级S/A/B+业绩正+无冲击的top3。
        # 形态尚未确认，非次日直接买点，需等待回踩/放量突破触发；保证非绿灯日期也有信号可跟。
        # 排除"❌仅观察不买入/❌等待首阳"(明确不买)、E级(综合评分过低)与已在绿灯A中的标的。
        cond_mask = df['次日操作'].astype(str).isin(['⚠️ 观察', '⚠️ 次日观察等回踩'])
        # v1.3.1: 过滤首阳日当天(回踩天数=0)——首阳大阳乖离VWAP过大、回踩确认买点当天
        # 不可执行(梅雁吉祥8/13乖离14.3%次日一路-9.9%从未回踩确认)。仅保留已进入回踩结构
        # (回踩天数≥1)形态可确认的标的，避免"首阳追高未回踩"型伪候选。
        pullback_days = (pd.to_numeric(df['回踩天数'], errors='coerce').fillna(0)
                         if '回踩天数' in df.columns else pd.Series(0, index=df.index))
        green_b = df[
            cond_mask &
            (pullback_days >= 1) &
            (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
            (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
            (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0)) &
            (df['修正后胜率分级'] != 'E') &
            (~df.index.isin(buy.index))
        ].sort_values('回踩买点分', ascending=False).head(3)
    else:
        buy = pd.DataFrame()
        green_b = pd.DataFrame()

    if len(buy) > 0:
        lines.append('## ✅ 次日可买入（形态回踩中 × 综合风控双确认）')
        lines.append('')
        lines.append('| 股票 | 评级 | 回踩买点分 | 洗盘修复分 | 主题 | 现价 | 止损 |')
        lines.append('|------|:----:|:--------:|:--------:|------|:---:|:---:|')
        for _, r in buy.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            stop_loss = f"{r['ATR动态止损价']:.2f}" if pd.notna(r.get('ATR动态止损价')) else '-'
            price = f"{r['现价']:.2f}" if pd.notna(r.get('现价')) else '-'
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            pb = f"{r['回踩买点分']:.0f}" if pd.notna(r.get('回踩买点分')) else '-'
            lines.append(f"| {name} | {r['修正后胜率分级']} | {pb} | {r['洗盘修复分']:.0f} | {theme} | {price} | {stop_loss} |")
        lines.append('')
    else:
        lines.append('## ✅ 次日可买入（形态回踩中 × 综合风控双确认）')
        lines.append('')
        lines.append('> 今日无：回踩形态达标者均未通过综合风控（评级/量能/业绩），宁缺毋滥。')
        lines.append('> 强势票见下方 AI 二次分析。')
        lines.append('')

    # ─── 🟡 条件信号（绿灯B·观察转强）: 非次日直接买点，需触发确认 ───
    # v1.3.0 新增: 当无绿灯A时保证每天至少有一个可跟踪信号
    if len(green_b) > 0:
        lines.append('## 🟡 条件信号（观察转强·需触发确认）')
        lines.append('')
        lines.append('> 评级/业绩/风控均已过关，但形态尚未确认，**非次日直接买点**。')
        lines.append('> 需等待回踩VWAP/MA20企稳或放量突破后按 AI 触发方案执行，触发前仅跟踪。')
        lines.append('')
        lines.append('| 股票 | 评级 | 回踩买点分 | 洗盘修复分 | 主题 | 现价 | 止损 | 次日操作 |')
        lines.append('|------|:----:|:--------:|:--------:|------|:---:|:---:|:---:|')
        for _, r in green_b.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            stop_loss = f"{r['ATR动态止损价']:.2f}" if pd.notna(r.get('ATR动态止损价')) else '-'
            price = f"{r['现价']:.2f}" if pd.notna(r.get('现价')) else '-'
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            pb = f"{r['回踩买点分']:.0f}" if pd.notna(r.get('回踩买点分')) else '-'
            op = str(r.get('次日操作', '')) if pd.notna(r.get('次日操作', '')) else ''
            lines.append(f"| {name} | {r['修正后胜率分级']} | {pb} | {r['洗盘修复分']:.0f} | {theme} | {price} | {stop_loss} | {op} |")
        lines.append('')

    # ─── 精选标的 (S/A级且洗盘修复分>=80, 无兑现冲击) ───
    # 排序: 结构增强分(洗盘修复形态+买点质量+动量融合)优先
    sort_col = '结构增强分' if '结构增强分' in df.columns else '洗盘修复分'
    elite = df[
        (df['修正后胜率分级'].isin(['S', 'A'])) &
        (df['洗盘修复分'] >= 80) &
        (df['兑现冲击过滤'].str.contains('✅', na=False))
    ].sort_values(sort_col, ascending=False)

    # ─── DeepSeek 二次分析（放精选表之前，先给结论）───
    # A组=buy(系统绿灯信号,强制给T+1执行方案) B组=elite(精选池严格筛选) C组=green_b(条件信号给触发式计划)
    if len(buy) > 0 or len(elite) > 0 or len(green_b) > 0:
        ai_text = _ai_analyze(buy, elite, green_b)
        if ai_text:
            lines.append('## 🤖 AI 二次分析 (DeepSeek)')
            lines.append('')
            lines.append(ai_text)
            lines.append('')

    if len(elite) > 0:
        # ─── 附: 精选标的评分表（放最后，作为评分原始数据）───
        lines.append('## 附: 精选标的评分表 (S/A + 洗盘修复分≥80 + 无兑现冲击)')
        lines.append('')
        lines.append('| 股票 | 评级 | 洗盘修复分 | 结构增强 | 主题 | 现价 | 止损 | 次日操作 | 决策 |')
        lines.append('|------|:----:|:--------:|:------:|------|:---:|:---:|:---:|------|')
        for _, r in elite.iterrows():
            name = f"{r['名称']}({str(r['代码']).replace('.SZ','').replace('.SH','')})"
            decision = str(r['交易决策'])[:20]
            stop_loss = f"{r['ATR动态止损价']:.2f}" if pd.notna(r.get('ATR动态止损价')) else '-'
            price = f"{r['现价']:.2f}" if pd.notna(r.get('现价')) else '-'
            theme = str(r.get('主题', '')) if pd.notna(r.get('主题')) else '-'
            boost = f"{r['结构增强分']:.0f}" if '结构增强分' in df.columns else '-'
            op = str(r.get('次日操作', '')) if pd.notna(r.get('次日操作', '')) else ''
            lines.append(f"| {name} | {r['修正后胜率分级']} | {r['洗盘修复分']:.0f} | {boost} | {theme} | {price} | {stop_loss} | {op} | {decision} |")
        lines.append('')

    return '\n'.join(lines)


def _fmt_stock_line(r) -> str:
    """单只股票喂给 AI 的超短线数据行（buy/elite 共用格式）"""
    code = str(r['代码']).replace('.SZ', '').replace('.SH', '')
    quant = r.get('量化择时分', 0)
    boost = r.get('结构增强分', 0)
    buy_point = r.get('推荐买点类型', '')
    theme = r.get('主题', '') if pd.notna(r.get('主题', '')) else '-'
    price = r['现价']
    vwap = r.get('VWAP', 0) if pd.notna(r.get('VWAP', 0)) else 0
    ma20 = r.get('MA20', 0) if pd.notna(r.get('MA20', 0)) else 0
    peak = r.get('筹码峰顶', 0) if pd.notna(r.get('筹码峰顶', 0)) else 0
    conc = r.get('筹码集中度%', 0) if pd.notna(r.get('筹码集中度%', 0)) else 0
    pullback = r.get('回踩确认', '') if pd.notna(r.get('回踩确认', '')) else '-'
    op = r.get('次日操作', '') if pd.notna(r.get('次日操作', '')) else ''
    target = r.get('ATR跟踪止盈价', 0) if pd.notna(r.get('ATR跟踪止盈价', 0)) else 0
    stop = r.get('ATR动态止损价', 0) if pd.notna(r.get('ATR动态止损价', 0)) else 0
    market = r.get('大盘状态', '') if pd.notna(r.get('大盘状态', '')) else '-'
    pb_score = r.get('回踩买点分', 0) if pd.notna(r.get('回踩买点分', 0)) else 0
    growth_s = str(r.get('中报业绩亮点', '-')) if pd.notna(r.get('中报业绩亮点', '-')) else '-'
    rise_gap = (price / vwap - 1) * 100 if vwap else 0       # 现价乖离VWAP
    ma20_gap = (price / ma20 - 1) * 100 if ma20 else 0       # 现价乖离MA20
    upside = (target / price - 1) * 100 if price and target else 0  # 至止盈位空间
    stop_gap = (stop / price - 1) * 100 if price and stop else 0    # 至止损位距离
    # 回踩天数/形态阶段: 计划持仓节奏参考（回踩2日≈次日启动概率高于5日）
    pb_days = r.get('回踩天数', '') if pd.notna(r.get('回踩天数', '')) else '-'
    shape = r.get('形态阶段', '') if pd.notna(r.get('形态阶段', '')) else '-'
    decision = str(r['交易决策'])
    # 与 tushare_quant 第7段一致化: 绿灯信号(✅次日可买入)若决策为"低胜率规避"，
    # 是评分层追认滞后(突破前夜)的措辞，与形态信号矛盾，改写为中性描述避免 AI 误读否决
    if op == '✅ 次日可买入' and '低胜率规避' in decision:
        decision = f'回踩完成待放量突破(业绩{growth_s})'
    return (
        f"- {r['名称']}({code}) 评级{r['修正后胜率分级']} 量化{quant:.1f} 修复{r['洗盘修复分']:.0f} "
        f"增强{boost:.1f} 主题[{theme}] 现价{price:.2f} VWAP={vwap:.2f} MA20={ma20:.2f} "
        f"乖离VWAP{rise_gap:+.1f}% 乖离MA20{ma20_gap:+.1f}% "
        f"筹码峰顶{peak:.2f}(集中度{conc:.0f}%) 回踩[{pullback}] 回踩天数{pb_days} 形态[{shape}] "
        f"买点[{buy_point}] 回踩买点分{pb_score:.0f} 业绩[{growth_s}] "
        f"止损{stop:.2f}(距离{stop_gap:+.1f}%) "
        f"止盈{target:.2f}(空间{upside:+.1f}%) 大盘[{market}] 次日操作[{op}] 决策[{decision}]"
    )


def _ai_analyze(buy: pd.DataFrame, elite: pd.DataFrame, green_b: pd.DataFrame = None) -> str:
    """以顶级超短线交易员视角做二次筛选：A组系统绿灯信号强制给执行方案，B组精选池严格筛选，C组条件信号给触发式计划"""
    # A组: 系统"次日可买入"信号(双确认已过) -- 是本策略的核心输出，T+1胜率跟踪80%/平均+11%
    # B组: 精选池(S/A+修复≥80+无冲击) -- 强势票严格二次筛选
    # C组: 条件信号(⚠️观察中评级/业绩/风控合格top3) -- 形态未确认，只给触发式计划
    a_lines = [_fmt_stock_line(r) for _, r in buy.head(5).iterrows()] if buy is not None and len(buy) else []
    b_lines = [_fmt_stock_line(r) for _, r in elite.head(10).iterrows()] if elite is not None and len(elite) else []
    c_lines = [_fmt_stock_line(r) for _, r in green_b.head(3).iterrows()] if green_b is not None and len(green_b) else []

    system = (
        '你是A股顶级超短线交易员（隔日/3-5日波段打法），极其严格、纪律优先、胜率至上。'
        '严格基于用户提供的量化数据做二次筛选，绝不编造数据、新闻或消息面。'
        '股票名称和代码必须严格引用用户数据。'
        '\n\n输出结构（Markdown格式）：'
        '\n## 一、系统绿灯信号·T+1执行方案'
        '\n## 二、精选池二次筛选'
        '\n  1.【核心持仓】最有把握的1-3只。'
        '\n  2.【可参与】次级标的。'
        '\n  3.【剔除/回避】明确剔除哪些，理由必须是数据上的（乖离过大、突破未确认、空间不足等）。'
        '\n\n【A组·系统绿灯信号】是量化系统双确认(回踩形态+综合风控)通过的"次日可买入"信号，'
        '近期跟踪T+1胜率80%、平均+11%、零止损。对A组每只：'
        '\n- 默认必须给出T+1可执行方案（按五要素），禁止以"空仓/观望"整体否决A组；'
        '\n- 仅当出现硬伤才可否决个股，硬伤仅限：兑现冲击预警⚠️、业绩与形态严重背离(业绩负增长)、'
        '止损距离超-15%且无法用收紧止损修复；'
        '\n- 评级低(如D级"回踩完成待放量突破")不构成否决理由--这是突破前夜形态，'
        '历史上D级绿灯信号(美迪西/奥浦迈20260806)次日大涨+17%~+18%；'
        '\n- 系统止损过宽时，自行收紧止损至买入价-5%~-7%以内，而非否决信号；'
        '\n- 方案须区分开盘情形：高开2%以内/高开2-5%/低开，分别给对策（追/等回踩/放弃）；'
        '\n- 仓位与风险预算参考：A组合计≤40%。'
        '\n\n【B组·精选池】严格按超短纪律筛选：'
        '\n- 现价乖离VWAP/MA20过大（>+20%）且无回踩确认的，不构成现价买点，只能给等待价位；'
        '\n- 系统止损价若高于回踩VWAP买点价，指出止损失效矛盾并自行重设止损。'
        '\n\n【C组·条件信号(绿灯B)】评级/业绩/风控已过关但形态未确认(⚠️观察/次日观察等回踩)，'
        '**不是次日直接买点**。必须给出触发式计划：触发价位(如回踩VWAP/MA20企稳位或放量突破价)、'
        '确认形态(缩量企稳/放量站上)、失效条件与时限；禁止写成可直接市价买入，未触发不追。'
        '\n\n每只【核心持仓】【可参与】与A组信号必须给出可执行的完整交易计划，五要素缺一不可：'
        '\n- ①触发条件：精确到价格与形态（如"竞价高开2%-4%且量比>2"或"回踩XX.XX元(VWAP)缩量企稳"，'
        'VWAP/MA20/筹码峰绝对价位已提供，直接引用）；'
        '\n- ②仓位：明确百分比（如"20%仓位"），A组单票≤20%，B组核心≤30%、可参与≤15%；'
        '\n- ③止损：绝对价格+执行方式（盘中触及即卖/收盘跌破次日卖）；'
        '\n- ④止盈：绝对价格+分批方式（如"到XX元减半，剩余看XX元"）；'
        '\n- ⑤失效条件与时限：什么情况下放弃计划，以及最长等待时间（如"当日未触发即作废，不隔日追"）。'
        '\n\n禁止模糊表述：禁止"关注""观望""择机""低吸""适当参与"等无价位无仓位的措辞。'
        '若认为某标的无可执行买点，归入剔除并说明等待的价位。'
        '结论要敢排敢砍，宁缺毋滥。'
    )
    parts = []
    if a_lines:
        parts.append("【A组·系统绿灯信号】（双确认通过，必须给出T+1执行方案）：\n" + "\n".join(a_lines))
    if c_lines:
        parts.append("【C组·条件信号】（评级/业绩/风控合格但形态未确认，给出触发式计划）：\n" + "\n".join(c_lines))
    if b_lines:
        parts.append("【B组·精选池标的】（S/A级、洗盘修复分≥80、无兑现冲击，系统初筛结果）：\n" + "\n".join(b_lines))
    prompt = (
        "\n\n".join(parts)
        + "\n\n请以顶级超短线交易员视角进行二次筛选与排序。"
        "重点考察：乖离VWAP/MA20是否透支、筹码峰是否突破、回踩确认是否成立、"
        "到止盈位空间是否足够（赔率）、大盘状态是否支持、买点类型质量。"
        "每个交易计划必须可直接下单执行：触发价、仓位、止损价、止盈价、失效条件五要素齐全。"
    )
    return call_deepseek(prompt, system=system)


def push_to_wechat(msg: str, title: str = None) -> bool:
    """通过 PushPlus 推送到微信"""
    if not PUSHPLUS_TOKEN:
        print('错误: 未设置 PUSHPLUS 环境变量')
        return False

    if not title:
        title = f'洗盘修复专题 — {datetime.now().strftime("%Y%m%d")}'

    try:
        resp = requests.post(PUSHPLUS_URL, json={
            'token': PUSHPLUS_TOKEN,
            'title': title,
            'content': msg,
            'template': 'markdown',
        }, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            print(f'推送成功: {result.get("msg", "")}')
            return True
        else:
            print(f'推送失败: code={result.get("code")} msg={result.get("msg")}')
            return False
    except Exception as e:
        print(f'推送异常: {e}')
        return False


def main():
    # 支持 --date YYYYMMDD 指定历史交易日（与 enhanced_timing_bull_all.py --date 一致），
    # 不传时自动取最新报告
    date_str = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--date':
        date_str = sys.argv[2]
    if date_str:
        target = os.path.join(REPORT_DIR, f'enhanced_timing_bull_all_{date_str}.csv')
        report_path = target if os.path.exists(target) else None
        if not report_path:
            print(f'未找到 {date_str} 的增强择时报告')
            return
    else:
        report_path = find_latest_report()
    if not report_path:
        print('未找到增强择时报告')
        return

    print(f'读取报告: {report_path}')

    df = pd.read_csv(report_path, encoding='utf-8-sig')

    # 从文件名提取交易日期
    match = re.search(r'enhanced_timing_bull_all_(\d{8})\.csv', os.path.basename(report_path))
    trade_date = match.group(1) if match else '未知'

    # 构建消息
    msg = build_wechat_msg(df, trade_date)
    print(msg[:500] + '...' if len(msg) > 500 else msg)

    # 保存 AI 报告文本（与 etf_alpha_v5_AI报告 同风格落盘，便于回看；即使推送失败也有留档）
    try:
        run_date = datetime.now().strftime('%Y%m%d')
        ai_report_file = os.path.join(REPORT_DIR, f'washout_AI报告_{trade_date}_{run_date}.txt')
        with open(ai_report_file, 'w', encoding='utf-8') as f:
            f.write(msg)
        print(f'✅ AI报告已保存: {ai_report_file}')
    except Exception as e:
        print(f'⚠️ AI报告保存失败: {e}')

    # 推送
    success = push_to_wechat(msg, title=f'EGPT {EGPT_VERSION} 中报预增回踩择时 {trade_date}')
    if success:
        print(f'微信推送完成: {trade_date}')
    else:
        print('微信推送失败')


if __name__ == '__main__':
    main()
