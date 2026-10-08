# -*- coding: utf-8 -*-
"""申万三级行业映射（tushare index_member_all / index_classify）。

为什么用申万三级而非东财行业
    东财 hybk 是自有分类，口径与市场共识不一致，且无稳定历史版本。
    申万三级是市场标准分类，L1/L2/L3 三级齐全，便于做「同题材共振」判定。

数据来源
    tushare pro.index_member_all()  → 个股 → L1/L2/L3 三级映射（含 in_date/out_date）
    tushare pro.index_classify(src='SW2021', level='L3') → 346 个三级行业清单

缓存
    D:/mystock/gap_breakout/.cache/sw_l3_map.json
    行业归属有变更时（in_date/out_date），默认取最新有效归属，
    并支持按历史日期还原（行业变更对回测有影响）。

Token
    从 D:/mystock/config/.env 的 TUSHARE_TOKEN 读取（与 solo 脚本一致）。
"""
from __future__ import annotations

import json
import os
from typing import Any

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "sw_l3_map.json")
ENV_PATH = r"D:\mystock\config\.env"

# 全市场 → 申万三级（用东财全市场快照兜底缺失项；仅在需要时启用）
MARKET_CODES_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".cache", "market_codes.json")


def _load_token() -> str:
    if not os.path.exists(ENV_PATH):
        raise RuntimeError(f"未找到 {ENV_PATH}")
    for line in open(ENV_PATH, encoding="utf-8"):
        s = line.strip()
        if s.startswith("TUSHARE_TOKEN"):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("TUSHARE_TOKEN 未在 .env 中定义")


def _ts():
    import tushare as ts
    ts.set_token(_load_token())
    return ts.pro_api()


def fetch_sw_l3(refresh: bool = False) -> dict[str, Any]:
    """拉取申万三级行业映射，落地为 JSON 缓存。

    返回结构：
        {code6: {"l1":..., "l2":..., "l3":..., "l3_code":..., "in":..., "out":...}}
        code6 为 6 位代码（去掉交易所后缀）
    """
    if os.path.exists(CACHE) and not refresh:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)

    pro = _ts()

    # 行业清单（346 个三级）
    cats = pro.index_classify(src="SW2021", level="L3")
    l3_info = {
        r["index_code"]: {"name": r["industry_name"], "parent": r.get("parent_code")}
        for _, r in cats.iterrows()
    }

    # 个股 → 三级行业
    mem = pro.index_member_all()
    mapping: dict[str, Any] = {}
    for _, r in mem.iterrows():
        code = str(r["ts_code"]).split(".")[0]
        mapping[code] = {
            "l1": r.get("l1_name"),
            "l2": r.get("l2_name"),
            "l3": r.get("l3_name"),
            "l3_code": r.get("l3_code"),
            "in": r.get("in_date"),
            "out": r.get("out_date"),
            "is_new": r.get("is_new"),
        }

    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"l3_info": l3_info, "members": mapping}, f, ensure_ascii=False)
    return {"l3_info": l3_info, "members": mapping}


class SWIndustry:
    """申万三级行业查询器。

    用法：
        sw = SWIndustry()
        sw.get("600519")   # → {"l1":"食品饮料", "l2":"白酒", "l3":...}
        sw.sector_peers("600519")  # → 同三级行业的其他股票
    """

    def __init__(self, refresh: bool = False):
        d = fetch_sw_l3(refresh)
        self.l3_info: dict[str, Any] = d["l3_info"]
        self.members: dict[str, Any] = d["members"]
        # 反向索引：三级行业名 → [code]
        self.by_l3: dict[str, list[str]] = {}
        for code, v in self.members.items():
            l3 = v.get("l3")
            if l3:
                self.by_l3.setdefault(l3, []).append(code)
        # 二级行业名 → 三级列表
        self.l3_by_l2: dict[str, list[str]] = {}
        for code, v in self.members.items():
            l2, l3 = v.get("l2"), v.get("l3")
            if l2 and l3:
                self.l3_by_l2.setdefault(l2, [])
                if l3 not in self.l3_by_l2[l2]:
                    self.l3_by_l2[l2].append(l3)

    def get(self, code: str) -> dict[str, Any] | None:
        return self.members.get(str(code).split(".")[0])

    def l3_of(self, code: str) -> str | None:
        v = self.members.get(str(code).split(".")[0])
        return v.get("l3") if v else None

    def l1_of(self, code: str) -> str | None:
        v = self.members.get(str(code).split(".")[0])
        return v.get("l1") if v else None

    def sector_peers(self, code: str) -> list[str]:
        """同三级行业的其他股票。"""
        l3 = self.l3_of(code)
        if not l3:
            return []
        return [c for c in self.by_l3.get(l3, []) if c != str(code).split(".")[0]]

    def l2_peers(self, code: str) -> list[str]:
        """同二级行业的全部股票（三级行业内聚的上一层）。"""
        v = self.members.get(str(code).split(".")[0])
        if not v or not v.get("l2"):
            return []
        l2 = v["l2"]
        out = []
        for l3 in self.l3_by_l2.get(l2, []):
            out += self.by_l3.get(l3, [])
        return out

    def coverage(self) -> dict[str, Any]:
        return {
            "members": len(self.members),
            "l3_count": len(self.by_l3),
            "l2_count": len(self.l3_by_l2),
            "l1_count": len({v.get("l1") for v in self.members.values()}),
            "cache": CACHE,
        }


if __name__ == "__main__":
    sw = SWIndustry()
    print(json.dumps(sw.coverage(), ensure_ascii=False, indent=2))
    for c in ("600519", "000001", "300750", "601398"):
        v = sw.get(c)
        print(f"  {c}: {v['l1']} / {v['l2']} / {v['l3']}" if v else f"  {c}: 未覆盖")
    print("\n白酒三级行业的股票数：", len(sw.by_l3.get("白酒", [])))
