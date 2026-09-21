# -*- coding: utf-8 -*-
"""
EDB（Extreme Dry-up Breakout）极致缩量 → 爆量突破 选股引擎 V1.0
================================================================================
独立引擎，输出标准化候选池供 HVT / TE / IGE / 基本面 二筛，不单独决定买入。

模块：
    edb.config   —— 全部阈值（唯一真源）
    edb.data     —— 数据层（复用项目既有缓存；HVT 结构 / 基本面 / 事件联动）
    edb.engine   —— 指标计算 + 评分 + 风险扣分 + 分层 + 建议动作
    edb.scanner  —— 全市场扫描 / 单股诊断 / CLI
    edb.backtest —— 历史回测（含 VR 分区间收益验证）
    edb.report   —— Markdown 报告

用法：
    python -m edb.scanner --date 20260918
    python -m edb.scanner --date 20260918 --top 30 --report
    python -m edb.scanner --symbol 600519
    python -m edb.scanner --backtest 20250601 20260918
"""

__version__ = '1.0.0'
