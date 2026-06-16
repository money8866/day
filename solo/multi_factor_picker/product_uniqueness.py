# -*- coding: utf-8 -*-
"""
产品唯一性评分系统（Product Uniqueness Score, PUS）

识别A股中具备"不可替代性"的中长期牛股。
核心逻辑：替代成本越高的公司，PUS 越高。

评分框架：
  PUS = 0.30 × CUS (客户唯一性) + 0.25 × OLS (订单锁定性)
       + 0.25 × TIS (技术不可替代性) + 0.20 × SCS (行业供给集中度)

数据来源：Tushare 财务缓存 + 同花顺概念 + LLM 行业推断
"""
import os
import sys
import json
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
from loguru import logger

# ── 项目内部依赖 ──
from data_fetcher import DataFetcher
from chain_mapping import (
    identify_chain_with_cache, load_concept_cache, get_stock_ths_concepts
)

# 尝试导入 LLM（tushare_quant.py 中的 deepseek 函数）
# 在独立运行时通过 sys.path hack 兼容
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
try:
    from tushare_quant import deepseek
    _LLM_AVAILABLE = bool(os.getenv("DEEPSEEK_API_KEY"))
except (ImportError, Exception):
    deepseek = None
    _LLM_AVAILABLE = False


# ──────────────────────────────────────────
# 数据容器
# ──────────────────────────────────────────

@dataclass
class PUSInput:
    """PUS 评分输入"""
    ts_code: str
    name: str
    industry: str
    theme: str = ""
    chain_tag: str = ""

    # 财务数据
    revenue: float = 0.0
    revenue_yoy: float = 0.0
    gross_margin: float = 0.0
    roe: float = 0.0
    rd_ratio: float = 0.0
    profit_yoy: float = 0.0

    # 订单/负债
    contract_liability: float = 0.0
    contract_liability_yoy: float = 0.0
    advance_payment: float = 0.0
    advance_payment_yoy: float = 0.0

    # 概念标签（JSON字符串列表）
    concepts: List[str] = field(default_factory=list)

    # 行业级别推断缓存（由 LLM 填充）
    industry_inference: Dict = field(default_factory=dict)


@dataclass
class PUSResult:
    """PUS 评分结果"""
    ts_code: str
    name: str
    theme: str
    industry: str
    chain_tag: str = ""

    # 四维度评分（0~1）
    cus: float = 0.0   # 客户唯一性
    ols: float = 0.0   # 订单锁定性
    tis: float = 0.0   # 技术不可替代性
    scs: float = 0.0   # 行业供给集中度

    pus_score: float = 0.0
    is_unique_stock: bool = False
    interpretation: str = ""

    # 置信度（LLM 推断部分）
    confidence_score: float = 1.0

    # 子维度详情
    sub_details: Dict = field(default_factory=dict)


# ──────────────────────────────────────────
# 辅助函数：毛利率稳定性
# ──────────────────────────────────────────

def _gm_stability(gm_history: List[float]) -> float:
    """毛利率稳定性评分：波动越小越稳定（0~1）"""
    if len(gm_history) < 3:
        return 0.5
    arr = np.array(gm_history)
    mean_gm = arr.mean()
    if mean_gm <= 0:
        return 0.3
    cv = arr.std() / mean_gm  # 变异系数
    # cv 越小越稳定：cv<=0.05 → 1.0, cv>=0.3 → 0.0
    score = max(0, min(1, 1 - (cv - 0.05) / 0.25))
    return score


# ──────────────────────────────────────────
# 行业级别 LLM 推断缓存
# ──────────────────────────────────────────

_INDUSTRY_INFERENCE_CACHE: Dict[str, Dict] = {}
"""{industry/chain: {cus_inference, scs_inference, tis_inference, confidence}}"""


def _llm_infer_industry(industry: str, concepts: List[str]) -> Dict:
    """对单个行业/产业链发起 LLM 推断，返回结构化结果"""
    if not _LLM_AVAILABLE or deepseek is None:
        return _rule_based_industry_infer(industry, concepts)

    prompt = f"""你是一个A股产业研究员。请对以下行业进行产品唯一性分析，返回JSON格式。

行业名称: {industry}
相关概念标签: {', '.join(concepts[:20])}

请用JSON格式回答（不要用markdown代码块，仅返回纯JSON）：
{{
  "customer_structure": "描述该行业的客户结构：是否进入AI/军工/新能源全球龙头供应链？头部客户集中度如何？",
  "customer_score": "客户唯一性评分(0~1)，基于：进入核心供应链=1.0，行业头部=0.7，一般工业=0.4，无壁垒=0.1",
  "order_lockin": "行业订单锁定程度描述：是否有长期框架协议？订单周期多长？",
  "tech_barrier": "描述该行业的技术壁垒：是工艺壁垒还是资本壁垒？认证周期多长？替代技术路径是否存在？",
  "tech_score": "技术不可替代性评分(0~1)：强壁垒(1.0)，中等(0.7)，一般(0.4)，低壁垒(0.1)",
  "supply_concentration": "描述行业供给集中度：是寡头/双寡头/分散/完全竞争？前三大厂商份额约多少？",
  "concentration_score": "集中度评分(0~1)：寡头(1.0)，中度集中(0.7)，分散(0.4)，完全竞争(0.1)",
  "confidence": "你对以上判断的置信度(0~1)"
}}"""
    try:
        resp = deepseek(prompt)
        if not resp:
            return _rule_based_industry_infer(industry, concepts)
        # 提取 JSON（处理可能的 markdown 包裹）
        json_match = re.search(r'\{.*\}', resp, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            required = ['customer_score', 'tech_score', 'concentration_score', 'confidence']
            if all(k in data for k in required):
                return {
                    'customer_structure': data.get('customer_structure', ''),
                    'customer_score': float(data['customer_score']),
                    'customer_confidence': float(data.get('confidence', 0.5)),
                    'order_lockin': data.get('order_lockin', ''),
                    'tech_barrier': data.get('tech_barrier', ''),
                    'tech_score': float(data['tech_score']),
                    'concentration': data.get('supply_concentration', ''),
                    'concentration_score': float(data['concentration_score']),
                    'confidence': float(data['confidence']),
                }
        # 解析失败则回退
        return _rule_based_industry_infer(industry, concepts)
    except Exception as e:
        logger.warning(f"[LLM] 行业推断失败 {industry}: {e}")
        return _rule_based_industry_infer(industry, concepts)


def _rule_based_industry_infer(industry: str, concepts: List[str]) -> Dict:
    """基于规则链的行业推断（LLM 不可用时的回退）"""
    concat = ' '.join(concepts).lower()
    industry_lower = industry.lower()

    # ── 客户唯一性（CUS）推断 ──
    cus_score = 0.4
    cus_desc = "一般工业客户"
    high_value_keywords = ['ai', '人工智能', '算力', '光模块', '服务器',
                           '新能源车', '锂电池', '光伏', '储能', '军工',
                           '半导体设备', '半导体材料', '航空', '航天']
    mid_keywords = ['汽车', '消费电子', '通信', '电子']

    high_match = sum(1 for k in high_value_keywords if k in concat or k in industry_lower)
    mid_match = sum(1 for k in mid_keywords if k in concat or k in industry_lower)

    if high_match >= 2 or ('ai' in concat and ('芯片' in concat or '光模块' in concat)):
        cus_score, cus_desc = 1.0, "进入AI/军工/新能源全球龙头核心供应链"
    elif high_match >= 1:
        cus_score, cus_desc = 0.7, "进入行业头部客户"
    elif mid_match >= 2:
        cus_score, cus_desc = 0.55, "多家知名客户但非核心"
    elif mid_match >= 1:
        cus_score, cus_desc = 0.4, "一般工业客户"

    # ── 技术壁垒（TIS）推断 ──
    tis_score = 0.4
    tis_desc = "一般制造"
    tech_keywords = ['芯片', '光模块', '半导体', '集成电路', '新材料',
                     '专利', '研发', 'ai算力', 'pcb', '液冷', '高端制造']
    if any(k in concat for k in ['光模块', '芯片', '半导体设备', '半导体材料']):
        tis_score, tis_desc = 1.0, "强技术壁垒：高精度工艺+认证壁垒"
    elif any(k in concat for k in tech_keywords):
        tis_score, tis_desc = 0.7, "中等技术壁垒"
    elif any(k in concat for k in ['化工', '钢铁', '制造']):
        tis_score, tis_desc = 0.4, "一般制造，主要靠规模和工艺"

    # ── 行业集中度（SCS）推断 ──
    scs_score = 0.4
    scs_desc = "分散市场"
    oligopoly_keywords = ['光模块', '芯片', '面板', '存储', 'ai算力', 'pcb载板']
    concentrated = ['半导体', '新材料', '创新药', '锂矿', '氟化工']
    if any(k in concat for k in oligopoly_keywords):
        scs_score, scs_desc = 1.0, "寡头/双寡头市场"
    elif any(k in concat for k in concentrated):
        scs_score, scs_desc = 0.7, "中度集中市场"
    elif any(k in concat for k in ['房地产', '建筑', '零售', '食品']):
        scs_score, scs_desc = 0.1, "完全竞争市场"

    return {
        'customer_structure': cus_desc,
        'customer_score': cus_score,
        'customer_confidence': 0.6,
        'order_lockin': '',
        'tech_barrier': tis_desc,
        'tech_score': tis_score,
        'concentration': scs_desc,
        'concentration_score': scs_score,
        'confidence': 0.6,
    }


# ──────────────────────────────────────────
# 核心评分器
# ──────────────────────────────────────────

class ProductUniquenessScorer:
    """产品唯一性评分器"""

    def __init__(self, config: Dict, fetcher: Optional[DataFetcher] = None):
        self.config = config
        self.fetcher = fetcher
        # 行业推断缓存（避免重复调用 LLM）
        self._industry_cache: Dict[str, Dict] = {}
        # 全市场概念缓存（ts_code -> [concept, ...]）
        self._concept_cache: Dict[str, List[str]] = {}

    def preload_industry_inferences(self, chain_keys: List[str],
                                     max_workers: int = 3):
        """
        并行预取所有产业链/行业的 LLM 推断到缓存

        Args:
            chain_keys: 所有需要推断的产业链或行业名称列表
            max_workers: 并行数（LLM API 通常限制并发，默认3）
        """
        # 过滤已缓存的
        need = [k for k in chain_keys if k not in self._industry_cache]
        if not need:
            return

        logger.info(f"[LLM] 并行预取 {len(need)} 个行业的 LLM 推断 "
                     f"(max_workers={max_workers})...")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_llm_infer_industry, k, []): k
                for k in need
            }
            done = 0
            for future in as_completed(future_map):
                key = future_map[future]
                done += 1
                try:
                    self._industry_cache[key] = future.result()
                    r = self._industry_cache[key]
                    logger.info(f"  [LLM] [{done}/{len(need)}] {key}: "
                                f"CUS={r.get('customer_score',0):.1f} "
                                f"TIS={r.get('tech_score',0):.1f} "
                                f"SCS={r.get('concentration_score',0):.1f} "
                                f"conf={r.get('confidence',0):.1f}")
                except Exception as e:
                    logger.warning(f"  [LLM] {key} 失败: {e}")
                    self._industry_cache[key] = _rule_based_industry_infer(key, [])
        logger.info(f"[LLM] 行业推断预取完成 ({len(need)}/{len(need)})")

    # ─────────────── OLS: 订单锁定性 ───────────────

    def _score_ols(self, data: PUSInput) -> Tuple[float, str, Dict]:
        """
        订单锁定性评分（仅依赖财务数据，不需 LLM）

        计算公式：
          base = contract_liability_yoy × 0.35
               + advance_payment_yoy × 0.20
               + revenue_yoy × 0.25
               + profit_yoy × 0.20

          年化增长率越高 → 订单锁定性越强
          用 sigmoid 映射到 0~1：score = 1 / (1 + exp(-3 * base))
        """
        cl = data.contract_liability_yoy if abs(data.contract_liability_yoy) < 10 else 0
        ap = data.advance_payment_yoy if abs(data.advance_payment_yoy) < 10 else 0
        rev = data.revenue_yoy if abs(data.revenue_yoy) < 5 else 0
        prof = data.profit_yoy if abs(data.profit_yoy) < 5 else 0

        raw = cl * 0.35 + ap * 0.20 + rev * 0.25 + prof * 0.20
        score = 1.0 / (1.0 + np.exp(-3.0 * raw))

        # 定性
        if score >= 0.7:
            interp = "订单持续高增长 + 合同负债强劲，长期锁定"
        elif score >= 0.5:
            interp = "订单增长明显，收入匹配"
        elif score >= 0.3:
            interp = "普通订单增长"
        else:
            interp = "无订单可见性或订单下滑"

        details = {
            'ols_raw': round(raw, 4),
            'contract_liability_yoy': round(cl, 4),
            'advance_payment_yoy': round(ap, 4),
            'revenue_yoy': round(rev, 4),
            'profit_yoy': round(prof, 4),
        }
        return round(score, 4), interp, details

    # ─────────────── CUS: 客户唯一性 ───────────────

    def _score_cus(self, data: PUSInput) -> Tuple[float, str, Dict]:
        """
        客户唯一性评分

        规则：
          1. 从概念标签推断是否进入核心供应链（占 60%）
          2. 从毛利率水平推断客户质量（占 40%）

        然后用 LLM 行业推断校正（如果有）
        """
        concepts = data.concepts or []

        # ── 规则基线 ──
        concat = ' '.join(concepts).lower()
        ind = data.industry.lower()

        # 判断是否进入核心供应链
        core_keywords = ['ai', '人工智能', '算力', '服务器', '光模块',
                         '新能源车', '锂电池', '军工', '航天', '大飞机',
                         '芯片', '半导体', '储能', '光伏']
        head_keywords = ['汽车电子', '消费电子', '通信', '苹果', '华为']

        has_core = sum(1 for k in core_keywords if k in concat or k in ind)
        has_head = sum(1 for k in head_keywords if k in concat or k in ind)

        # 毛利率代理客户质量
        gm = data.gross_margin
        gm_quality = 0.3 if gm >= 0.40 else 0.2 if gm >= 0.25 else 0.1

        # 规则分数
        if has_core >= 2:
            base_cus = 0.6 + gm_quality
        elif has_core >= 1:
            base_cus = 0.4 + gm_quality
        elif has_head >= 1:
            base_cus = 0.3 + gm_quality
        else:
            base_cus = 0.1 + gm_quality
        base_cus = min(base_cus, 0.95)

        # ── LLM 行业推断校正 ──
        chain_key = data.chain_tag or data.industry
        llm_info = self._get_industry_inference(chain_key, concepts)

        cus_from_llm = llm_info.get('customer_score', base_cus)
        llm_conf = llm_info.get('customer_confidence', 0.5)

        # 加权融合：LLM 置信度高则用 LLM 分
        if llm_conf >= 0.7:
            final_cus = cus_from_llm
        else:
            final_cus = base_cus * (1 - llm_conf) + cus_from_llm * llm_conf

        final_cus = max(0.0, min(1.0, final_cus))

        # 定性
        if final_cus >= 0.8:
            interp = "进入AI/军工/新能源全球龙头核心供应链"
        elif final_cus >= 0.55:
            interp = "进入行业头部客户"
        elif final_cus >= 0.3:
            interp = "一般工业客户"
        else:
            interp = "无明显客户壁垒"

        details = {
            'base_cus': round(base_cus, 4),
            'gm_quality': round(gm_quality, 4),
            'llm_cus': round(cus_from_llm, 4),
            'llm_confidence': round(llm_conf, 4),
            'core_matches': has_core,
            'head_matches': has_head,
        }
        return round(final_cus, 4), interp, details

    # ─────────────── TIS: 技术不可替代性 ───────────────

    def _score_tis(self, data: PUSInput) -> Tuple[float, str, Dict]:
        """
        技术不可替代性评分

        规则：
          1. 研发强度 rd_ratio × 0.30
          2. 毛利率水平 & 稳定性 × 0.35
          3. 行业技术壁垒（LLM推断）× 0.35
        """
        rd = min(data.rd_ratio / 0.15, 1.0) * 0.30  # rd_ratio >= 15% → 满分

        gm = data.gross_margin
        gm_part = 0.0
        if gm >= 0.50:
            gm_part = 0.35
        elif gm >= 0.35:
            gm_part = 0.25
        elif gm >= 0.20:
            gm_part = 0.15
        else:
            gm_part = 0.05

        chain_key = data.chain_tag or data.industry
        llm_info = self._get_industry_inference(chain_key, data.concepts)
        tis_llm = llm_info.get('tech_score', 0.4)
        llm_conf = llm_info.get('confidence', 0.5)

        final_tis = rd + gm_part * (1 - 0.3 * llm_conf) + tis_llm * 0.35 * llm_conf
        final_tis = max(0.0, min(1.0, final_tis))

        if final_tis >= 0.7:
            interp = "强技术壁垒：高研发投入+高毛利+长认证周期"
        elif final_tis >= 0.45:
            interp = "中等壁垒：有一定研发积累和工艺优势"
        elif final_tis >= 0.25:
            interp = "一般制造壁垒"
        else:
            interp = "低技术壁垒，替代成本低"

        details = {
            'rd_part': round(rd, 4),
            'gm_part': round(gm_part, 4),
            'llm_tis': round(tis_llm, 4),
            'llm_confidence': round(llm_conf, 4),
            'rd_ratio': round(data.rd_ratio, 4),
            'gross_margin': round(gm, 4),
        }
        return round(final_tis, 4), interp, details

    # ─────────────── SCS: 行业供给集中度 ───────────────

    def _score_scs(self, data: PUSInput) -> Tuple[float, str, Dict]:
        """
        行业供给集中度评分

        主要依赖 LLM 行业推断（因为缺乏财报层面的集中度数据）
        """
        chain_key = data.chain_tag or data.industry
        llm_info = self._get_industry_inference(chain_key, data.concepts)

        scs = llm_info.get('concentration_score', 0.4)
        confidence = llm_info.get('confidence', 0.5)

        if scs >= 0.8:
            interp = "寡头/双寡头市场，龙头掌控定价权"
        elif scs >= 0.55:
            interp = "中度集中市场，2~3家主导"
        elif scs >= 0.3:
            interp = "分散市场，无明显龙头"
        else:
            interp = "完全竞争市场"

        details = {
            'scs': round(scs, 4),
            'llm_confidence': round(confidence, 4),
            'llm_raw': llm_info.get('concentration', ''),
        }
        return round(scs, 4), interp, details

    # ─────────────── LLM 推断缓存 ───────────────

    def _get_industry_inference(self, chain_key: str,
                                 concepts: List[str]) -> Dict:
        """获取行业/产业链级别的推断（带缓存）"""
        if chain_key in self._industry_cache:
            return self._industry_cache[chain_key]

        result = _llm_infer_industry(chain_key, concepts)
        self._industry_cache[chain_key] = result
        if _LLM_AVAILABLE and result.get('confidence', 0) > 0.5:
            logger.info(f"[LLM] {chain_key}: CUS={result.get('customer_score',0):.1f} "
                        f"TIS={result.get('tech_score',0):.1f} "
                        f"SCS={result.get('concentration_score',0):.1f} "
                        f"conf={result.get('confidence',0):.1f}")
        return result

    # ─────────────── 主评分入口 ───────────────

    def score_single(self, data: PUSInput) -> PUSResult:
        """计算单只股票的 PUS"""
        cus, cus_interp, cus_details = self._score_cus(data)
        ols, ols_interp, ols_details = self._score_ols(data)
        tis, tis_interp, tis_details = self._score_tis(data)
        scs, scs_interp, scs_details = self._score_scs(data)

        pus = 0.30 * cus + 0.25 * ols + 0.25 * tis + 0.20 * scs

        # 合成一句话解释
        parts = []
        if cus >= 0.7:
            parts.append(f"客户: {cus_interp.split('：')[-1][:20]}")
        if ols >= 0.5:
            parts.append(f"订单: {ols_interp.split('，')[0][:20]}")
        if tis >= 0.45:
            parts.append(f"技术: {tis_interp.split('：')[-1][:20]}")
        if scs >= 0.55:
            parts.append(f"格局: {scs_interp.split('，')[0][:20]}")
        interpretation = "；".join(parts) if parts else "无明显不可替代性"

        # 置信度取各 LLM 推断置信度的最小值
        chain_key = data.chain_tag or data.industry
        llm_info = self._industry_cache.get(chain_key, {})
        confidence = llm_info.get('confidence', 1.0)

        return PUSResult(
            ts_code=data.ts_code,
            name=data.name,
            theme=data.theme,
            industry=data.industry,
            chain_tag=data.chain_tag,
            cus=cus,
            ols=ols,
            tis=tis,
            scs=scs,
            pus_score=round(pus, 4),
            is_unique_stock=pus >= 0.65,
            interpretation=interpretation,
            confidence_score=confidence,
            sub_details={
                'cus': cus_details,
                'ols': ols_details,
                'tis': tis_details,
                'scs': scs_details,
            }
        )

    def compute_all(self, data_list: List[PUSInput]) -> List[PUSResult]:
        """批量评分"""
        results = [self.score_single(d) for d in data_list]
        results.sort(key=lambda r: r.pus_score, reverse=True)
        return results


# ──────────────────────────────────────────
# 数据提取：从 Tushare 缓存提取 PUSInput
# ──────────────────────────────────────────

def extract_pus_data(
    row: pd.Series,
    financial_batch: Dict,
    config: Dict,
    concept_map: Dict[str, List[str]] = None,
) -> Optional[PUSInput]:
    """
    从一笔 financial_batch 数据中提取 PUSInput

    Args:
        row: stock_list 中的一行 (ts_code, name, industry)
        financial_batch: {ts_code: {income, balance, cashflow, forecast}}
        config: 配置
        concept_map: {ts_code: [concept1, concept2]}

    Returns:
        PUSInput or None
    """
    ts_code = row['ts_code']
    name = row['name']
    industry = str(row.get('industry', '')) if pd.notna(row.get('industry', '')) else ''

    fin = financial_batch.get(ts_code, {})
    income = fin.get('income', pd.DataFrame()) if isinstance(fin.get('income'), pd.DataFrame) else pd.DataFrame()
    balance = fin.get('balance', pd.DataFrame()) if isinstance(fin.get('balance'), pd.DataFrame) else pd.DataFrame()
    cashflow = fin.get('cashflow', pd.DataFrame()) if isinstance(fin.get('cashflow'), pd.DataFrame) else pd.DataFrame()

    if len(income) == 0:
        return None

    # ── 类型防御 ──
    def _ensure_str(df):
        if df is None or len(df) == 0:
            return df
        for col in df.columns:
            if col in ['ts_code', 'end_date', 'ann_date']:
                if not pd.api.types.is_string_dtype(df[col]):
                    df[col] = df[col].apply(
                        lambda x: str(int(x)) if pd.notna(x) and str(x).replace('.','').isdigit() else str(x) if pd.notna(x) else ''
                    )
        return df

    income = _ensure_str(income).sort_values('end_date', ascending=False).reset_index(drop=True)
    balance = _ensure_str(balance).sort_values('end_date', ascending=False).reset_index(drop=True) if len(balance) > 0 else pd.DataFrame()
    cashflow = _ensure_str(cashflow).sort_values('end_date', ascending=False).reset_index(drop=True) if len(cashflow) > 0 else pd.DataFrame()

    # ── 最新年报 ──
    annual = income[income['end_date'].str.endswith('1231')].copy() if 'end_date' in income.columns else income.copy()
    latest = income.iloc[0]

    revenue = float(latest.get('revenue')) if pd.notna(latest.get('revenue')) else 0.0
    n_income = float(latest.get('n_income')) if pd.notna(latest.get('n_income')) else 0.0
    total_cogs = float(latest.get('total_cogs')) if pd.notna(latest.get('total_cogs')) else 0.0
    rd_exp = float(latest.get('rd_exp')) if pd.notna(latest.get('rd_exp')) else 0.0

    gross_margin = (revenue - total_cogs) / revenue if revenue > 0 else 0.0
    rd_ratio = rd_exp / revenue if revenue > 0 else 0.0

    # ── ROE ──
    equity = 0.0
    if len(balance) > 0:
        equity = float(balance.iloc[0].get('total_hldr_eqy_exc_min_int')) if pd.notna(balance.iloc[0].get('total_hldr_eqy_exc_min_int')) else 0.0
    roe = n_income / equity if equity > 0 else 0.0

    # ── 同比增速 ──
    rev_yoy, prof_yoy = 0.0, 0.0
    annual_sorted = annual.sort_values('end_date', ascending=False).reset_index(drop=True)
    if len(annual_sorted) >= 2:
        c = annual_sorted.iloc[0]
        cr = float(c.get('revenue')) if pd.notna(c.get('revenue')) else 0.0
        cp = float(c.get('n_income')) if pd.notna(c.get('n_income')) else 0.0
        prev_year = str(c.get('end_date', ''))[:4]
        prev_year_s = str(int(prev_year) - 1) if prev_year.isdigit() else ''
        prev_rows = annual_sorted[annual_sorted['end_date'].str.startswith(prev_year_s)]
        if len(prev_rows) > 0:
            p = prev_rows.iloc[-1]
            pr = float(p.get('revenue')) if pd.notna(p.get('revenue')) else 0.0
            pp = float(p.get('n_income')) if pd.notna(p.get('n_income')) else 0.0
            rev_yoy = (cr - pr) / pr if pr > 0 else 0.0
            prof_yoy = (cp - pp) / pp if pp > 0 else 0.0

    # ── 合同负债/预付款 ──
    cl_yoy, ap_yoy = 0.0, 0.0
    cl_val, ap_val = 0.0, 0.0
    if len(balance) >= 2:
        bal = balance.sort_values('end_date', ascending=False).reset_index(drop=True)
        lat, prv = bal.iloc[0], bal.iloc[1]
        cl_c = float(lat.get('contract_liability', 0)) if pd.notna(lat.get('contract_liability')) else 0.0
        cl_p = float(prv.get('contract_liability', 0)) if pd.notna(prv.get('contract_liability')) else 0.0
        cl_val = cl_c
        if cl_p > 0:
            cl_yoy = (cl_c - cl_p) / cl_p

        ap_c = float(lat.get('advance_payment', 0)) if pd.notna(lat.get('advance_payment')) else 0.0
        ap_p = float(prv.get('advance_payment', 0)) if pd.notna(prv.get('advance_payment')) else 0.0
        ap_val = ap_c
        if ap_p > 0:
            ap_yoy = (ap_c - ap_p) / ap_p

    # ── 概念标签 ──
    concepts = []
    if concept_map and ts_code in concept_map:
        concepts = concept_map[ts_code]
    else:
        try:
            concepts = get_stock_ths_concepts(ts_code, config)
        except Exception:
            concepts = []

    # ── 产业链标签 + 主题 ──
    chain_tag = identify_chain_with_cache(ts_code, name, industry, config)
    theme = chain_tag.replace("链", "").replace("链", "") if chain_tag else ""

    return PUSInput(
        ts_code=ts_code,
        name=name,
        industry=industry,
        theme=theme,
        chain_tag=chain_tag,
        revenue=revenue,
        revenue_yoy=rev_yoy,
        gross_margin=gross_margin,
        roe=roe,
        rd_ratio=rd_ratio,
        profit_yoy=prof_yoy,
        contract_liability=cl_val,
        contract_liability_yoy=cl_yoy,
        advance_payment=ap_val,
        advance_payment_yoy=ap_yoy,
        concepts=concepts,
    )


# ──────────────────────────────────────────
# 输出函数
# ──────────────────────────────────────────

def results_to_dataframe(results: List[PUSResult]) -> pd.DataFrame:
    """PUSResult 列表转 DataFrame"""
    rows = []
    for r in results:
        rows.append({
            'ts_code': r.ts_code,
            'name': r.name,
            'theme': r.theme,
            'industry': r.industry,
            'chain_tag': r.chain_tag,
            'cus': r.cus,
            'ols': r.ols,
            'tis': r.tis,
            'scs': r.scs,
            'pus_score': r.pus_score,
            'is_unique_stock': r.is_unique_stock,
            'confidence_score': r.confidence_score,
            'interpretation': r.interpretation,
        })
    df = pd.DataFrame(rows)
    return df


def print_pus_summary(results: List[PUSResult], top_n: int = 20):
    """打印 PUS 评分摘要"""
    if not results:
        logger.info("无 PUS 评分结果")
        return

    uniq = [r for r in results if r.is_unique_stock]
    strong = [r for r in results if r.pus_score >= 0.75]
    core = [r for r in results if r.pus_score >= 0.85]

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"产品唯一性评分 (PUS) 完成")
    logger.info(f"  评分总数: {len(results)}")
    logger.info(f"  保留(PUS>=0.65): {len(uniq)}")
    logger.info(f"  强推荐(PUS>=0.75): {len(strong)}")
    logger.info(f"  核心龙头(PUS>=0.85): {len(core)}")
    logger.info("=" * 60)

    # TOP 20
    logger.info(f"")
    logger.info(f"▼ TOP {top_n} 产品唯一性最强股票")
    for i, r in enumerate(results[:top_n], 1):
        tag = "★核心" if r.pus_score >= 0.85 else "◆强推" if r.pus_score >= 0.75 else "●保留"
        logger.info(f"  {i:>2}. [{tag}] {r.name:<8} ({r.ts_code}) "
                    f"PUS={r.pus_score:.3f} | CUS={r.cus:.2f} OLS={r.ols:.2f} "
                    f"TIS={r.tis:.2f} SCS={r.scs:.2f} | {r.interpretation}")

    # 专项排名
    logger.info("")
    logger.info("▼ TOP 5 客户唯一性 (CUS)")
    top_cus = sorted(results, key=lambda x: x.cus, reverse=True)[:5]
    for i, r in enumerate(top_cus, 1):
        logger.info(f"  {i}. {r.name:<8} {r.ts_code} CUS={r.cus:.2f} | {r.interpretation}")

    logger.info("")
    logger.info("▼ TOP 5 订单锁定性 (OLS)")
    top_ols = sorted(results, key=lambda x: x.ols, reverse=True)[:5]
    for i, r in enumerate(top_ols, 1):
        logger.info(f"  {i}. {r.name:<8} {r.ts_code} OLS={r.ols:.2f}")

    logger.info("")
    logger.info("▼ TOP 5 技术不可替代性 (TIS)")
    top_tis = sorted(results, key=lambda x: x.tis, reverse=True)[:5]
    for i, r in enumerate(top_tis, 1):
        logger.info(f"  {i}. {r.name:<8} {r.ts_code} TIS={r.tis:.2f} | {r.interpretation}")

    logger.info("")
    logger.info("▼ TOP 5 行业供给集中度 (SCS)")
    top_scs = sorted(results, key=lambda x: x.scs, reverse=True)[:5]
    for i, r in enumerate(top_scs, 1):
        logger.info(f"  {i}. {r.name:<8} {r.ts_code} SCS={r.scs:.2f}")


# ──────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────

def main():
    """PUS 评分主程序"""
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

    logger.info("=" * 60)
    logger.info("产品唯一性评分系统 (PUS) 启动")
    logger.info(f"LLM 可用: {_LLM_AVAILABLE}")
    logger.info("=" * 60)

    # 加载配置
    config_path = str(Path(__file__).parent / 'config.yaml')
    import yaml
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取 Token
    token_env = config.get('tushare', {}).get('token_env', 'TUSHARE_TOKEN')
    token = os.environ.get(token_env)
    if not token:
        env_paths = [
            Path(__file__).resolve().parent.parent.parent / "config" / ".env",
            Path(__file__).resolve().parent.parent / "config" / ".env",
        ]
        for ep in env_paths:
            if ep.exists():
                with open(ep, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            k, v = line.split('=', 1)
                            if k.strip() == token_env:
                                token = v.strip().strip('"\'')
                                break
                break
    if not token:
        logger.error(f"未找到 Tushare Token，请设置环境变量 {token_env}")
        sys.exit(1)

    fetcher = DataFetcher(token, config)

    # ── 1. 获取股票列表 ──
    stocks = fetcher.get_stock_list(list_status='L')
    if config.get('universe', {}).get('exclude_st', True):
        stocks = stocks[~stocks['name'].str.contains('ST', na=False)]
    logger.info(f"待筛选股票: {len(stocks)}")

    # ── 2. 获取财务数据（复用缓存） ──
    ts_code_list = stocks['ts_code'].tolist()
    start_year = str(datetime.now().year - 3)
    logger.info("获取财务数据（复用缓存）...")
    financial_batch = fetcher.get_stock_financial_batch(ts_code_list, start_year=start_year, max_workers=16)
    logger.info(f"财务数据: {len(financial_batch)} 只")

    # ── 3. 加载概念缓存 ──
    logger.info("加载概念缓存...")
    concept_map = {}
    try:
        concept_map = load_concept_cache(config)
        logger.info(f"  ✓ 概念映射: {len(concept_map)} 只")
    except Exception as e:
        logger.warning(f"  概念加载失败: {e}")

    # ── 4. 提取 PUSInput ──
    logger.info("提取评分数据...")
    pus_inputs = []
    skip = 0
    for _, row in stocks.iterrows():
        data = extract_pus_data(row, financial_batch, config, concept_map)
        if data is not None:
            pus_inputs.append(data)
        else:
            skip += 1
    logger.info(f"有效数据: {len(pus_inputs)} 只, 跳过: {skip} 只")

    # ── 5. 并行预取 LLM 行业推断 ──
    scorer = ProductUniquenessScorer(config, fetcher)
    if _LLM_AVAILABLE and '--test-run' not in sys.argv:
        chain_keys = sorted({
            d.chain_tag or d.industry
            for d in pus_inputs
            if d.chain_tag or d.industry
        })
        scorer.preload_industry_inferences(chain_keys, max_workers=3)
    else:
        logger.info("跳过 LLM 推断（test-run 模式或 LLM 不可用）")

    # ── 6. 计算 PUS ──
    logger.info("计算 PUS 评分...")
    results = scorer.compute_all(pus_inputs)
    logger.info(f"PUS 评分完成: {len(results)} 只")

    # ── 6. 过滤 ──
    uniq_results = [r for r in results if r.is_unique_stock]
    logger.info(f"保留 (PUS>=0.65): {len(uniq_results)} 只")

    # ── 7. 输出 ──
    print_pus_summary(uniq_results, top_n=20)

    # ── 8. 保存 CSV ──
    output_dir = Path(config.get('output', {}).get('dir', 'output'))
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if uniq_results:
        df = results_to_dataframe(uniq_results)
        path = output_dir / f"pus_unique_{timestamp}.csv"
        df.to_csv(path, index=False, encoding='utf-8-sig')
        logger.info(f"唯一性标的结果已保存至: {path}")

    # 全量
    df_all = results_to_dataframe(results)
    all_path = output_dir / f"pus_all_{timestamp}.csv"
    df_all.to_csv(all_path, index=False, encoding='utf-8-sig')
    logger.info(f"全量数据已保存至: {all_path}")

    logger.info("=" * 60)
    logger.info("PUS 评分完成")
    logger.info("=" * 60)

    # 输出关键标的 JSON 摘要
    top10 = []
    for r in uniq_results[:10]:
        top10.append({
            'ts_code': r.ts_code,
            'name': r.name,
            'theme': r.theme,
            'pus_score': r.pus_score,
            'cus': r.cus, 'ols': r.ols, 'tis': r.tis, 'scs': r.scs,
            'interpretation': r.interpretation,
        })
    logger.info(f"\nTOP 10 唯一性标的摘要:\n{json.dumps(top10, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
