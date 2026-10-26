"""MarketNode V1 —— A 股大盘炒作节点识别引擎。

模块结构：
    features.py : 从 stock_cache(daily_cache / index_daily_cache) 计算每日市场特征并缓存
    engine.py   : 滚动分位数 + 规则引擎，识别 N1-N6 节点、判断进攻/观察/防守
    report.py   : 生成每日 Markdown 报告与节点历史 CSV
    run.py      : 命令行入口
"""
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_config(path=None):
    path = path or CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


__all__ = ["BASE_DIR", "CONFIG_PATH", "load_config"]