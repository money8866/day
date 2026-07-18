#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Ranking System
========================
Production-grade Learning-To-Rank system for A-share industry ETFs.

Objective:
    Every trading day after market close, rank all industry ETFs and identify
    the ETF with the highest probability of outperforming over the next 20-60
    trading days using a LightGBM LambdaRank model.

Architecture:
    TDX Market Data -> Local SQLite DB -> Feature Engine -> LGBMRanker
    -> ETF Future Ranking -> Single ETF Portfolio Decision

Author: Quant Desk
"""
__version__ = "1.0.0"
