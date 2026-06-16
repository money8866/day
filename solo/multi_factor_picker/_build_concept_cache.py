# -*- coding: utf-8 -*-
"""
同花顺概念板块缓存构建脚本
================================

从Tushare获取所有同花顺概念板块及其成分股，构建本地缓存。
后续产业链识别将基于此缓存进行精准匹配。

输出缓存文件:
  cache/ths_concept_index.parquet  - 概念板块列表
  cache/ths_concept_members.parquet - 概念成分股 (ts_code -> [concept_names])
"""
import sys
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import pandas as pd
import tushare as ts
import loguru

logger = loguru.logger

# 添加项目根目录
sys.path.insert(0, '.')
from data_fetcher import save_cache, load_cache, get_cache_dir
from main import load_config, get_token

logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


def build_ths_concept_cache(config: Dict) -> None:
    """
    构建同花顺概念板块缓存

    Args:
        config: 配置字典
    """
    token = get_token(config)
    pro = ts.pro_api(token=token)
    cache_dir = get_cache_dir(config)

    logger.info("=" * 60)
    logger.info("开始构建同花顺概念板块缓存")
    logger.info("=" * 60)

    # ============== 1. 获取概念板块列表 ==============
    logger.info("步骤1: 获取概念板块列表...")
    try:
        concepts = pro.ths_index(exchange='A', type='N',
                                 fields='ts_code,name,count,list_date,type')
        if concepts is None or len(concepts) == 0:
            logger.error("未获取到概念板块列表")
            return
        logger.info(f"✓ 共 {len(concepts)} 个概念板块")

        # 保存概念列表
        save_cache(concepts, cache_dir, 'ths_concept_index')
        logger.info(f"  已保存: ths_concept_index")
    except Exception as e:
        logger.error(f"获取概念列表失败: {e}")
        return

    # ============== 2. 获取所有概念成分股 ==============
    logger.info("\n步骤2: 获取所有概念成分股...")
    all_members = []
    total = len(concepts)
    failed = 0

    for idx, row in concepts.iterrows():
        ts_code = row['ts_code']
        name = row['name']
        try:
            members = pro.ths_member(ts_code=ts_code, fields='ts_code,con_code,con_name')
            if members is not None and len(members) > 0:
                all_members.append(members)
            time.sleep(0.05)  # 限速
        except Exception as e:
            failed += 1
            if failed <= 5:
                logger.warning(f"  {name} 失败: {str(e)[:60]}")

        if (idx + 1) % 50 == 0:
            logger.info(f"  进度: {idx+1}/{total}, 已收集 {sum(len(m) for m in all_members)} 条记录")

    if not all_members:
        logger.error("未获取到任何成分股数据")
        return

    df = pd.concat(all_members, ignore_index=True)
    logger.info(f"\n✓ 共 {len(df)} 条成分股记录, 涵盖 {df['con_code'].nunique()} 只股票")

    # 保存
    save_cache(df, cache_dir, 'ths_concept_members')
    logger.info(f"  已保存: ths_concept_members")

    # ============== 3. 构建 ts_code -> concepts 反向索引 ==============
    logger.info("\n步骤3: 构建反向索引 ts_code -> [concept_names]...")

    # 每只股票可能属于多个概念
    reverse_index = df.groupby('con_code')['con_name'].apply(list).reset_index()
    reverse_index.columns = ['ts_code', 'concepts']
    reverse_index['concept_count'] = reverse_index['concepts'].apply(len)
    logger.info(f"  反向索引: {len(reverse_index)} 只股票")
    logger.info(f"  平均每只股票属于 {reverse_index['concept_count'].mean():.1f} 个概念")

    save_cache(reverse_index, cache_dir, 'ths_concept_reverse')
    logger.info(f"  已保存: ths_concept_reverse")

    # ============== 4. 统计 ==============
    logger.info("\n" + "=" * 60)
    logger.info("概念板块统计 Top 30")
    logger.info("=" * 60)
    concept_stats = df['con_name'].value_counts().head(30)
    for cn, cnt in concept_stats.items():
        logger.info(f"  {cn}: {cnt} 只")


if __name__ == "__main__":
    config = load_config()
    build_ths_concept_cache(config)
    logger.info("\n✓ 概念缓存构建完成！")
