# -*- coding: utf-8 -*-
"""market.py 冒烟验证（临时脚本，验证后删除）。"""
import sys
sys.path.insert(0, r"d:\mystock\solo")

import numpy as np
import pandas as pd
from crr import market as M
from crr.config import SCAN_STATES

ok = True


def check(label, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + label + (" | " + extra if extra else ""))
    if not cond:
        ok = False


F, T = False, True

# ---- 1) classify_market_states：单日反弹不得结束暴跌段 ----
n = 12
feat = pd.DataFrame({
    "data_sufficient": [T] * n,
    "is_crash_base": [F, F, T, T, T, F, F, F, F, F, F,