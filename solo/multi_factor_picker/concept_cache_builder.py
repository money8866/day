# -*- coding: utf-8 -*-
"""
构建同花顺概念板块数据缓存

用途: 用于产业链标签的精细化识别（如"鼎泰高科→AI PCB链"）

数据来源: Tushare ths_index + ths_member
- ths_index: 获取概念板块列表 (~409个)
- ths_member: 获取每个概念的成分股

缓存策略:
- 缓存到 cache/ths_concepts_full.parquet (一次完整快照)
- 缓存到 cache/concept_stocks.parquet (股票→概念反向索引)
- 每周更新一次
"""
import sys
import os
sys.path.insert(0, '.')

import time
import pandas as pd
from pathlib import Path
from datetime import datetime
from loguru import logger

import tushare as ts
try:
    from main import load_config, get_token
except ImportError:
    # 以 multi_factor_picker.main 模块方式导入时，顶层 d:\mystock\solo\main.py 会截获
    from multi_factor_picker.main import load_config, get_token
from data_fetcher import save_cache, load_cache, get_cache_dir

# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")


def build_concept_cache(config: dict, expire_hours: int = 168) -> dict:
    """
    构建概念板块缓存

    Returns:
        {
            'concepts_df': 概念列表,
            'members_df': 概念成分股详情,
            'stock_concept_map': {ts_code: [concept_names]}
        }
    """
    cache_dir = get_cache_dir(config)

    # 检查缓存
    cache_key_concepts = "ths_concepts_list"
    cache_key_members = "ths_concepts_members"

    cached_concepts = load_cache(cache_dir, cache_key_concepts, expire_hours)
    cached_members = load_cache(cache_dir, cache_key_members, expire_hours)

    if cached_concepts is not None and cached_members is not None:
        logger.info(f"使用缓存: {len(cached_concepts)} 个概念, {len(cached_members)} 条成分股记录")
        return _build_stock_concept_map(cached_concepts, cached_members)

    # 获取token
    token = get_token(config)
    pro = ts.pro_api(token=token)

    # 1. 获取概念列表
    logger.info("调用 Tushare ths_index 获取概念列表...")
    try:
        concepts_df = pro.ths_index(exchange='A', type='N', fields='ts_code,name,count,list_date')
        time.sleep(0.2)
        logger.info(f"  ✓ 获取 {len(concepts_df)} 个概念")
    except Exception as e:
        logger.error(f"  ✗ 失败: {e}")
        return {}

    # 2. 获取每个概念的成分股
    logger.info("调用 Tushare ths_member 获取概念成分股...")
    all_members = []
    total = len(concepts_df)

    for idx, row in concepts_df.iterrows():
        try:
            members = pro.ths_member(ts_code=row['ts_code'], fields='ts_code,con_code,con_name')
            if not members.empty:
                all_members.append(members)
            time.sleep(0.06)  # 避免频率超限
        except Exception as e:
            logger.warning(f"  获取 {row['name']}({row['ts_code']}) 失败: {str(e)[:50]}")
            continue

        if (idx + 1) % 50 == 0:
            logger.info(f"  进度: {idx+1}/{total}")

    if not all_members:
        logger.error("未获取到任何概念成分股")
        return {}

    members_df = pd.concat(all_members, ignore_index=True)

    # 重要: ths_member 返回的字段是
    #   ts_code = 概念代码 (如 885959.TI)
    #   con_code = 股票代码 (如 300476.SZ)
    #   con_name = 股票名称 (如 胜宏科技)
    # 需要把 ts_code 转换为概念名称
    concept_name_map = dict(zip(concepts_df['ts_code'], concepts_df['name']))
    members_df['concept_name'] = members_df['ts_code'].map(concept_name_map)
    members_df = members_df.dropna(subset=['concept_name'])

    logger.info(f"  ✓ 共 {len(members_df)} 条成分股记录, {members_df['con_code'].nunique()} 只股票, "
                f"{members_df['concept_name'].nunique()} 个概念")

    # 3. 保存缓存
    save_cache(concepts_df, cache_dir, cache_key_concepts)
    save_cache(members_df, cache_dir, cache_key_members)
    logger.info(f"缓存已保存: {cache_key_concepts}, {cache_key_members}")

    return _build_stock_concept_map(concepts_df, members_df)


def _build_stock_concept_map(concepts_df: pd.DataFrame, members_df: pd.DataFrame) -> dict:
    """
    构建股票→概念的反向索引

    Returns:
        {
            'concepts_df': 概念列表DataFrame,
            'members_df': 成分股DataFrame,
            'stock_concepts': {ts_code: [concept_name1, ...]},
            'concept_stocks': {concept_name: [ts_code1, ...]}
        }
    """
    stock_concepts = {}
    concept_stocks = {}

    for _, row in members_df.iterrows():
        ts_code = row['con_code']
        con_name = row['con_name']
        if ts_code and con_name:
            stock_concepts.setdefault(ts_code, []).append(con_name)
            concept_stocks.setdefault(con_name, []).append(ts_code)

    return {
        'concepts_df': concepts_df,
        'members_df': members_df,
        'stock_concepts': stock_concepts,
        'concept_stocks': concept_stocks
    }


if __name__ == "__main__":
    print("=" * 60)
    print("构建同花顺概念板块缓存")
    print("=" * 60)

    config = load_config()
    result = build_concept_cache(config)

    if not result:
        print("构建失败")
        sys.exit(1)

    print(f"\n✓ 概念板块: {len(result['concepts_df'])} 个")
    print(f"✓ 成分股记录: {len(result['members_df'])} 条")
    print(f"✓ 覆盖股票: {len(result['stock_concepts'])} 只")
    print(f"✓ 股票→概念映射: {len(result['stock_concepts'])} 条")

    # 验证几个关键股票
    print("\n关键股票验证:")
    for ts_code, name in [
        ('300476.SZ', '胜宏科技'),  # PCB
        ('301377.SZ', '鼎泰高科'),  # PCB钻针
        ('002371.SZ', '北方华创'),  # 半导体设备
        ('300308.SZ', '中际旭创'),  # 光模块
        ('300750.SZ', '宁德时代'),  # 锂电池
    ]:
        concepts = result['stock_concepts'].get(ts_code, [])
        print(f"  {name}({ts_code}): {concepts[:5]}")

    # 找一些核心概念
    print("\n核心产业链概念股票数 Top 15:")
    for con_name in ['AI算力', 'PCB概念', '半导体设备', '锂电池概念', '光伏概念',
                     '低空经济', '人形机器人', '光模块', 'CPO概念', 'HBM概念',
                     '先进封装', '服务器', '存储芯片', '工业母机', '军工电子']:
        stocks = result['concept_stocks'].get(con_name, [])
        if stocks:
            print(f"  {con_name}: {len(stocks)} 只")
