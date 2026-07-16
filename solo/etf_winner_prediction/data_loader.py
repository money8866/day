#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction - 数据加载模块
=======================================
复用 etf_alpha_engine 的 DataLoader，直接导入。
"""
import sys
import os
from typing import Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_alpha_engine.data_loader import DataLoader, load_config, json_load

__all__ = ["DataLoader", "load_config", "json_load"]