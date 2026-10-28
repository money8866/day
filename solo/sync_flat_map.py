#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sync_flat_map.py
================
theme3.json 一致性维护脚本（一次性 / 可反复运行）。

背景：
  theme3.json 有两份主题配置 —— CATEGORIES（层级结构，唯一事实源）和
  THEME_FLAT_MAP（扁平索引，供 theme_purity_engine_v4 等脚本读取）。
  人工/脚本只改了 CATEGORIES 导致两者漂移（2026-10-27 实测：FLAT_MAP 70 个
  主题 vs CATEGORIES 74 个，AI 目录还是拆分前的旧"AI算力链"，另有低空 4 主题
  + 消费电子共 5 个字段级不一致）。

  由于生成链（generate_theme3.py + theme_v2_meta_part1~4.py）已断裂，
  本脚本采用"程序化同步"策略：**永远以 CATEGORIES 为准重建 THEME_FLAT_MAP**，
  不做任何手工合并。

同时：
  把 theme3_constituents_v2.py 里硬编码的 MANUAL_CONCEPT 迁移为顶层键
  MANUAL_CONCEPT_BOARDS（代码侧保留同名默认值作为回退）。

使用： python sync_flat_map.py [--check]
  --check  只检查漂移，不写回
"""
import os
import sys
import json
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THEME3_PATH = os.path.join(BASE_DIR, "theme3.json")

# 从 theme3_constituents_v2.py 迁移过来的手工补强表（单一事实源移入配置）
MANUAL_CONCEPT_BOARDS = {
    "AI应用": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念", "数字人"],
    "AI模型与AI Agent": ["人工智能", "AI应用", "AI智能体", "多模态AI", "ChatGPT概念", "AIGC概念"],
    "AI算力芯片": ["AI芯片", "算力概念", "GPU概念", "人工智能", "半导体概念", "先进封装"],
    "光模块与CPO": ["CPO概念", "光通信模块", "光纤概念", "光通信", "5G概念"],
    "数据中心网络": ["东数西算", "算力概念", "云计算", "数字经济"],
    "数据中心散热": ["液冷概念", "算力概念", "数据中心", "绿色电力"],
    "高速铜连接": ["铜缆高速连接", "连接器", "算力概念"],
    "行星滚柱丝杠": ["人形机器人", "减速器", "工业母机", "高端装备"],
    "核聚变": ["核能核电", "高温超导", "军工"],
    "低空运营服务": ["低空经济", "通用航空", "无人机"],
    "低空基础设施": ["低空经济", "通用航空", "无人机"],
}


def flatten_categories(data: dict) -> dict:
    """从 CATEGORIES 重建扁平 map：{主题名: V2 cfg}（cfg 为深拷贝）。"""
    flat = {}
    for cat_name, cat_obj in data.get("CATEGORIES", {}).items():
        for tname, cfg in (cat_obj.get("themes") or {}).items():
            flat[tname] = json.loads(json.dumps(cfg, ensure_ascii=False))
    return flat


def main():
    check_only = "--check" in sys.argv

    with open(THEME3_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cats = data.get("CATEGORIES", {})
    flat_map = data.get("THEME_FLAT_MAP", {})

    truth = flatten_categories(data)
    print(f"[Check] CATEGORIES 子主题数 : {len(truth)}")
    print(f"[Check] THEME_FLAT_MAP 数量 : {len(flat_map)}")

    only_cat = sorted(set(truth) - set(flat_map))
    only_flat = sorted(set(flat_map) - set(truth))
    field_diff = []
    for t in sorted(set(truth) & set(flat_map)):
        a = json.dumps(truth[t], sort_keys=True, ensure_ascii=False)
        b = json.dumps(flat_map[t], sort_keys=True, ensure_ascii=False)
        if a != b:
            field_diff.append(t)

    drift = bool(only_cat or only_flat or field_diff)
    if only_cat:
        print(f"[Check] 仅在 CATEGORIES 中（FLAT_MAP 缺失）: {only_cat}")
    if only_flat:
        print(f"[Check] 仅在 FLAT_MAP 中（CATEGORIES 已删）: {only_flat}")
    if field_diff:
        print(f"[Check] 字段不一致 ({len(field_diff)} 个): {field_diff}")
    if not drift:
        print("[Check] ✅ THEME_FLAT_MAP 与 CATEGORIES 完全一致")

    manual_needed = (
        "MANUAL_CONCEPT_BOARDS" not in data
        or data.get("MANUAL_CONCEPT_BOARDS") != MANUAL_CONCEPT_BOARDS
    )
    if manual_needed:
        print("[Check] MANUAL_CONCEPT_BOARDS 顶层键缺失或与代码默认值不一致")

    if check_only:
        print("\n[--check] 只检查模式，未写回。")
        return

    if not drift and not manual_needed:
        print("\n✅ 无需同步。")
        return

    # ---------- 备份 ----------
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = os.path.join(BASE_DIR, f"theme3.json.bak_{ts}")
    shutil.copy2(THEME3_PATH, bak_path)
    print(f"\n[Backup] 已备份 -> {os.path.basename(bak_path)}")

    # ---------- 重建 FLAT_MAP ----------
    data["THEME_FLAT_MAP"] = truth

    # ---------- 迁移 MANUAL_CONCEPT_BOARDS ----------
    if manual_needed:
        data["MANUAL_CONCEPT_BOARDS"] = MANUAL_CONCEPT_BOARDS

    with open(THEME3_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[Write] THEME_FLAT_MAP 重建为 {len(truth)} 个主题（以 CATEGORIES 为准）")
    if manual_needed:
        print(f"[Write] MANUAL_CONCEPT_BOARDS 写入 {len(MANUAL_CONCEPT_BOARDS)} 个主题")

    # ---------- 回读验证 ----------
    with open(THEME3_PATH, "r", encoding="utf-8") as f:
        verify = json.load(f)
    assert flatten_categories(verify) == verify.get("THEME_FLAT_MAP"), "回读验证失败！"
    assert verify.get("CATEGORIES") == cats, "CATEGORIES 被意外改动！"
    print("[Verify] ✅ 回读验证通过：FLAT_MAP 与 CATEGORIES 一致，CATEGORIES 未被改动")


if __name__ == "__main__":
    main()
