"""AI 研判报告生成 — 调用 DeepSeek Flash + 双通道微信推送.

用法:
    from theme_engine.score_v3.ai_report import generate_ai_report
    report = await generate_ai_report(engine_result, trade_date)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from theme_engine.score_v3.models import EngineV3Result, ThemeV3Score

logger = logging.getLogger(__name__)

# 加载环境变量
_ENV_PATH = os.getenv("ENV_FILE", "d:/mystock/config/.env")
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
elif os.path.exists("d:/mystock/config/.env"):
    load_dotenv("d:/mystock/config/.env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# 生命周期中文映射
_LIFE_CN = {
    "birth": "萌芽", "growth": "成长", "main_up": "主升",
    "late": "末期", "decline": "衰退",
}
# 迁移方向中文映射
_DIR_CN = {
    "ACCELERATING": "加速上行", "PEAKING": "见顶冲顶",
    "DECELERATING": "动能衰减", "DECLINING": "衰退下行",
    "BOTTOMING": "筑底企稳", "RECOVERING": "复苏回暖",
    "STABLE": "稳定运行", "STALLING": "低位停滞",
}
# 信号中文映射
_SIG_CN = {
    "STRONG_BUY": "强买", "BUY": "买入", "ROTATE_IN": "轮入",
    "PRE_ROTATE": "提前轮动", "WATCH": "观察", "HOLD": "持有",
    "REDUCE": "减仓", "EXIT": "离场",
}
# 防御型和高弹性主题分类（用于提示词）
_DEFENSIVE_SET = {"银行", "电力", "煤炭", "黄金", "公用事业", "红利"}
_HIGH_BETA_SET = {"AI算力", "半导体", "机器人", "低空经济", "智能驾驶",
                  "信创", "游戏", "创新药", "商业航天", "新能源车", "证券"}


# ─────────────────────────────────────────────
#  第1步: 组装 prompt（从 EngineV3Result 提取关键数据）
# ─────────────────────────────────────────────

def _build_prompt(result: EngineV3Result, trade_date: str) -> str:
    """构建发送给 DeepSeek 的完整 prompt."""
    lines: List[str] = []
    sep = "=" * 60

    # ── 指令头部 ──
    lines.append(f"你是一位专业的A股短线交易助理。以下是用TERE V3引擎扫描{trade_date[:4]}年{int(trade_date[4:6])}月{int(trade_date[6:8])}日的数据。")
    lines.append("请输出两份内容：")
    lines.append("")
    lines.append("### 第一部分：极简盯盘版")
    lines.append("要求：")
    lines.append("- 可读性重构，适合微信群/飞书发送")
    lines.append("- 使用Emoji区分防御🛡️/进攻⚔️板块")
    lines.append("- 用带换行的段落格式，每段一个操作，突出「方向、具体个股、仓位、触发条件」")
    lines.append("- 每段都要标注该主题的龙头股（持续N天）和中军股（持续N天）")
    lines.append("- 第一行用一句话点明市场基调（如：Weak 26分 | 仓位≤30% | 防御优先）")
    lines.append("- 字数控制在500字以内")
    lines.append("- 不要用表格，手机阅读不友好")
    lines.append("")
    lines.append("### 第二部分：逻辑压力测试")
    lines.append("要求：")
    lines.append("- 作为交易员，指出本策略在当前弱市缩量环境下，可能存在的3个最大逻辑漏洞或执行风险")
    lines.append("- 每点用一句话概括漏洞+一句话解释为什么是风险")
    lines.append("- 保持客观、专业、直接")
    lines.append("")
    lines.append("总要求：不能编造任何数据，所有结论必须来源于提供的量化数据。")

    # ── 市场状态 ──
    mkt = result.market_info
    if mkt:
        vote = mkt.details.get("vote_results", {})
        lines.append(sep)
        lines.append("【市场状态】")
        lines.append(f"评分: {mkt.market_score:.0f}/100 | 状态: {mkt.market_regime_cn} | 置信度: {mkt.confidence*100:.0f}%")
        lines.append(f"仓位建议: {mkt.recommended_exposure*100:.0f}% | 市场乘数: {mkt.market_multiplier:.2f}")
        lines.append(f"投票: 趋势={vote.get('trend','?')} 宽度={vote.get('breadth','?')} 情绪={vote.get('sentiment','?')} 资金={vote.get('liquidity','?')}")
        lines.append("")

    # ── 排行榜 TOP15 ──
    lines.append(sep)
    lines.append("【主题排行榜 TOP15】")
    lines.append("排名|主题|Intrinsic|Tradable|Forward|迁移分|信号|生命周期|迁移方向|预期收益|风险")
    lines.append("----|----|---------|--------|-------|------|----|--------|--------|--------|----")
    for theme in result.ranking[:15]:
        tr = theme.transition_result
        trans = f"▶{_DIR_CN.get(tr.direction, '')}" if tr and tr.direction != "STABLE" else "—"
        life = _LIFE_CN.get(theme.life_stage, theme.life_stage)
        sig = _SIG_CN.get(theme.signal, theme.signal)
        lines.append(
            f"{theme.rank}|{theme.theme_name}|{theme.intrinsic_score:.0f}|{theme.tradable_score:.0f}|"
            f"{theme.forward_score:.0f}|{theme.migration_priority:.0f}|{sig}|{life}|"
            f"{trans}|{theme.expected_return}|{theme.risk}"
        )
    lines.append("")

    # ── PRE_ROTATE 信号专题 ──
    pre_rotate = [t for t in result.ranking if t.pre_rotate and t.transition_result]
    if pre_rotate:
        lines.append(sep)
        lines.append("【PRE_ROTATE 提前轮动信号】")
        lines.append("主题|迁移方向|强度|置信度|预计天数|当前→目标|距离P|动量M|扩散C|资金$|龙头L|市场R")
        lines.append("----|--------|----|-------|--------|---------|----|----|----|----|----|----")
        for t in pre_rotate:
            tr = t.transition_result
            lines.append(
                f"{t.theme_name}|{tr.direction_cn}|{tr.strength:.0f}|{tr.confidence*100:.0f}%|{tr.days_estimate}d|"
                f"{_LIFE_CN.get(tr.from_stage,tr.from_stage)}→{_LIFE_CN.get(tr.to_stage,tr.to_stage)}|"
                f"{tr.proximity_score:.0f}|{tr.momentum_score:.0f}|{tr.confirmation_score:.0f}|"
                f"{tr.money_resonance_score:.0f}|{tr.leader_health_score:.0f}|{tr.regime_compat_score:.0f}"
            )
        lines.append("")

    # ── 各主题因子分解 ──
    lines.append(sep)
    lines.append("【TOP10 因子分解 + 龙头股 + 中军】")
    for theme in result.ranking[:10]:
        factors = (
            f"ETF趋={theme.etf_trend:.0f} 加速={theme.etf_accel:.0f} 扩散={theme.breadth:.0f} "
            f"龙头={theme.leader:.0f} 龙扩={theme.leader_expand:.0f} 资金={theme.money:.0f} "
            f"排名={theme.rank_momentum:.0f} 生命加={theme.lifecycle_bonus:+.0f} "
            f"共振={theme.resonance_multiplier:.2f}"
        )
        # 龙头（带持续性标签）
        lr = theme.leader_result
        persistent_set = set(lr.persistent_leaders) if lr else set()
        pdays = lr.persistent_days if lr else {}
        leader_tags = []
        for name in (theme.top_leaders or [])[:3]:
            if name in persistent_set:
                leader_tags.append(f"{name}(持续{pdays.get(name, 2)}d)")
            else:
                leader_tags.append(f"{name}(新晋)")
        leaders_str = "、".join(leader_tags)

        # 中军
        zj_str = ""
        if lr and lr.zhongjun:
            zj_names = "、".join(lr.zhongjun[:3])
            zj_days_str = ""
            zd = lr.zhongjun_days
            # 给中军加持续天数标签
            zj_tagged = []
            for n in lr.zhongjun[:3]:
                d = zd.get(n, 1)
                zj_tagged.append(f"{n}(持续{d}d)")
            zj_str = f" | 中军: {'、'.join(zj_tagged)}"

        # 龙头中军合一信号
        overlap = ""
        if lr and lr.zhongjun and theme.top_leaders:
            common = set(lr.zhongjun) & set(theme.top_leaders)
            if common:
                overlap = f" ⚡龙头中军合一:{','.join(common)}"

        lines.append(f"#{theme.rank} {theme.theme_name}: {factors}")
        lines.append(f"  龙头股: {leaders_str}{zj_str}{overlap}")
    lines.append("")

    # ── 龙头持续性专题 ──
    persistent_themes = [t for t in result.ranking if t.leader_result and t.leader_result.persistent_leaders]
    if persistent_themes:
        lines.append(sep)
        lines.append("【持续性龙头专题】")
        for t in persistent_themes[:8]:
            lr = t.leader_result
            p_list = [f"{n}({lr.persistent_days.get(n, 2)}d)" for n in lr.persistent_leaders]
            lines.append(f"{t.theme_name}: 持续龙头 {'、'.join(p_list)}")
        lines.append("")

    # ── Forward/Tradable 差值 Top5 ──
    themes_sorted = sorted(result.ranking, key=lambda t: t.forward_score - t.tradable_score, reverse=True)
    lines.append(sep)
    lines.append("【Forward - Tradable 差值排名 (越大=未来接力潜力越强)】")
    for theme in themes_sorted[:5]:
        gap = theme.forward_score - theme.tradable_score
        lines.append(f"{theme.theme_name}: Forward={theme.forward_score:.0f} Tradable={theme.tradable_score:.0f} 差值=+{gap:.0f}")
    lines.append("")

    # ── 分类统计 ──
    lines.append(sep)
    lines.append("【分类统计】")
    lines.append(f"PRE_ROTATE信号数: {len(pre_rotate)}")
    lines.append(f"防御型主题排名: {'、'.join(t.theme_name for t in result.ranking[:5] if t.theme_name in _DEFENSIVE_SET)}")
    lines.append(f"高弹性主题排名: {'、'.join(t.theme_name for t in result.ranking[:10] if t.theme_name in _HIGH_BETA_SET)}")
    growth_up_count = sum(1 for t in result.ranking if t.life_stage in ("growth", "main_up"))
    lines.append(f"成长/主升阶段主题: {growth_up_count}/{len(result.ranking)}")
    lines.append("")

    # ── 总结指令 ──
    lines.append(sep)
    lines.append("请基于以上真实量化数据，写一份自然语言研判报告。")
    lines.append("格式要求：用Markdown，不含表格，纯自然语言段落叙述。")
    lines.append("每一段用 **加粗** 标出核心结论。")
    lines.append("最后加一段「综合结论与行动建议」。")
    lines.append("不编造任何数据。输出语言：中文。")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  第2步: 调用 DeepSeek Flash API
# ─────────────────────────────────────────────

def _call_deepseek(prompt: str) -> str:
    """调用 DeepSeek Flash 生成 AI 报告."""
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置，跳过 AI 报告生成")
        return ""

    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股顶级短线交易助理，严格基于量化数据给出交易决策。"
                    "绝不编造任何数据、新闻或外部事件。"
                    "输出分两部分：①极简盯盘版（emoji+表格，适合微信群/飞书）"
                    "②逻辑压力测试（3个漏洞）。"
                    "极度精简，每句话都要有交易含义。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    try:
        logger.info("DeepSeek Flash 请求中...")
        r = requests.post(url, headers=headers, json=data, timeout=120)
        if r.status_code != 200:
            logger.error(f"DeepSeek API 返回异常: {r.status_code} {r.text[:200]}")
            return f"AI 报告生成失败（API 返回 {r.status_code}）"
        content = r.json()["choices"][0]["message"]["content"]
        logger.info(f"DeepSeek Flash 生成完成 ({len(content)} 字符)")
        return content
    except requests.exceptions.Timeout:
        logger.error("DeepSeek API 超时（120秒）")
        return "AI 报告生成失败（API 超时）"
    except Exception as e:
        logger.error(f"DeepSeek API 调用异常: {e}")
        return f"AI 报告生成失败（{e}）"


# ─────────────────────────────────────────────
#  第3步: 双通道微信推送
# ─────────────────────────────────────────────

def _send_wechat_serverchan(msg: str, title: str) -> bool:
    """通过 Server酱 推送微信."""
    sckey = os.getenv("WECHAT_SCKEY")
    if not sckey:
        logger.warning("WECHAT_SCKEY 未配置，跳过 Server酱 推送")
        return False

    # 清理 HTML 标签（Server酱 不支持）
    clean = re.sub(r"<[^>]+>", "", msg)
    clean = clean.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    url = f"https://sctapi.ftqq.com/{sckey}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": clean}, timeout=15)
        result = resp.json()
        if result.get("code") == 0:
            logger.info("✅ Server酱 推送成功")
            return True
        logger.warning(f"Server酱 推送失败: {result.get('message', '未知错误')}")
        return False
    except Exception as e:
        logger.error(f"Server酱 异常: {e}")
        return False


def _send_pushplus(msg: str, title: str) -> bool:
    """通过 PushPlus 推送微信（支持 Markdown）. """
    token = os.getenv("PUSHPLUS")
    if not token:
        logger.warning("PUSHPLUS 未配置，跳过 PushPlus 推送")
        return False

    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title,
        "content": msg,
        "template": "markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get("code") == 200:
            logger.info("✅ PushPlus 推送成功")
            return True
        logger.warning(f"PushPlus 推送失败: {result.get('msg', '未知错误')}")
        return False
    except Exception as e:
        logger.error(f"PushPlus 异常: {e}")
        return False


def _send_dual_channel(msg: str, title: str) -> None:
    """双通道推送: Server酱 + PushPlus."""
    ok_sc = _send_wechat_serverchan(msg, title)
    ok_pp = _send_pushplus(msg, title)
    if not ok_sc and not ok_pp:
        logger.warning("双通道推送均失败")


# ─────────────────────────────────────────────
#  第4步: 保存到本地文件
# ─────────────────────────────────────────────

def _save_report(content: str, trade_date: str, output_dir: Optional[str] = None) -> str:
    """保存 AI 报告到本地，返回文件路径. """
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent / "reports")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"ai_report_v3_{trade_date}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"AI 报告已保存: {filepath}")
    return filepath


# ─────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────

def generate_ai_report(
    result: EngineV3Result,
    trade_date: str,
    *,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> str:
    """生成 AI 研判报告并推送微信.

    Args:
        result: V3 引擎完整输出
        trade_date: 交易日 YYYYMMDD
        dry_run: 仅打印不推送
        output_dir: 报告保存目录 (默认: reports/)

    Returns:
        AI 报告文本内容 (空字符串表示失败)
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置，跳过 AI 报告")
        return ""

    # 1. 组装 prompt
    logger.info("组装 AI 研判 prompt...")
    prompt = _build_prompt(result, trade_date)

    # 2. 保存 prompt 用于调试
    try:
        prompt_dir = Path(__file__).resolve().parent / "cache"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        with open(prompt_dir / f"ai_prompt_{trade_date}.txt", "w", encoding="utf-8") as f:
            f.write(prompt)
    except Exception:
        pass

    # 3. 调用 DeepSeek
    logger.info("调用 DeepSeek Flash 生成 AI 研判...")
    report = _call_deepseek(prompt)
    if not report:
        return ""

    # 4. 保存报告
    _save_report(report, trade_date, output_dir)

    # 5. 双通道推送
    title = f"TERE V3 主题轮动研判 — {trade_date}"
    if not dry_run:
        _send_dual_channel(report, title)
    else:
        logger.info("[dry_run] 跳过微信推送")
        print("\n" + "=" * 50)
        print("AI 研判报告 (dry_run 不推送)")
        print("=" * 50)
        print(report)
        print("=" * 50)

    return report
