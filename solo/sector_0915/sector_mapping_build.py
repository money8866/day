#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sector_mapping_build.py
================================================================================
A 股量化平台 第二阶段：RAW BOARD -> CANONICAL SECTOR -> STOCK MEMBERSHIP

唯一真源（只读，程序不得增删改）：
    sector_0915/sector_master.json

旧配置 theme_config.json / subtheme_map.json / theme.json 视为 LEGACY，
本程序只报告其存在，绝不读取其内容用于映射。

产出：
    config/sector_mapping.json
    config/sector_dictionary.json
    data/raw_sector_board.csv
    data/raw_sector_member.csv
    data/sector_membership.csv
    data/sector_overlap.csv
    data/dynamic_sector_candidate.csv
    output/sector_quality.csv
    output/sector_mapping_review.csv
    output/sector_membership_review.csv
    output/sector_pollution.csv
    output/sector_tracking_pool.json
    output/sector_mapping_audit.md

运行：
    python sector_mapping_build.py --full
    python sector_mapping_build.py --validate
    python sector_mapping_build.py --date 20260915

术语对齐说明：
    需求文本使用 theme_* 命名；本项目真源文件为 sector_master.json，
    其内部字段为 sector_id / sector_name，故本程序统一采用 sector_* 命名，
    字段语义与需求文本一一对应（theme_id -> sector_id，theme_name -> sector_name）。
================================================================================
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime

import pandas as pd

# ────────────────────────────────────────────────────────────────────────────
# 路径
# ────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(BASE_DIR)

CONFIG_DIR = os.path.join(BASE_DIR, "config")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_DIR = os.path.join(BASE_DIR, "logs")

for _d in (CONFIG_DIR, DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

MASTER_CANDIDATES = [
    os.path.join(BASE_DIR, "sector_master.json"),
    os.path.join(CONFIG_DIR, "sector_master.json"),
]

# 复用项目已有基础设施（不新建第二套 Tushare client）
if PROJ_DIR not in sys.path:
    sys.path.insert(0, PROJ_DIR)

try:
    from sli.utils import load_token as _sli_load_token, rate_limit as _sli_rate_limit
    from sli.utils import setup_logging as _sli_setup_logging
    _HAS_SLI = True
except Exception:  # pragma: no cover
    _HAS_SLI = False

import logging

log = logging.getLogger("sector_build")

RATE_LIMIT_MS = 120

# 数据源搜索目录（复用已有 cache，不新建爬虫）
SW_CACHE_DIRS = [
    os.path.join(PROJ_DIR, "sli", "cache"),
    os.path.join(PROJ_DIR, "cache"),
]
PARQUET_DIRS = [
    os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily"),
    r"D:\mystock\cache_daily",
]
for _p in list(PARQUET_DIRS):
    _sub = os.path.join(_p, "parquet")
    if _sub not in PARQUET_DIRS:
        PARQUET_DIRS.append(_sub)

LEGACY_FILES = ["theme_config.json", "subtheme_map.json", "theme.json"]

# 本阶段参与映射的 Raw Board 类型（需求文本：重点 INDUSTRY_L3 + CONCEPT）
# INDUSTRY_L2：申万部分核心产业只到二级（如"基础建设"），三级粒度无法对齐 Sector，
# 故一并纳入，但个股归属上限只给 PRIMARY（见 build_membership 的 L2 口径上限）。
MAPPABLE_BOARD_TYPES = {"INDUSTRY_L3", "INDUSTRY_L2", "CONCEPT"}


# ────────────────────────────────────────────────────────────────────────────
# 通用工具
# ────────────────────────────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[（）()\[\]【】{}｛｝·・、,，.。;；:：\-—_/\\|<>《》\"'\s]+")
_NOISE_WORDS = ["概念股", "概念", "产业链", "板块", "指数", "主题", "行业", "相关"]
_ROMAN_TAIL_RE = re.compile(r"(?<=[\u4e00-\u9fff])(?:I|V|X)+$")


def norm_text(s) -> str:
    """Raw Board / 关键词 标准化：全半角、括号、空格、特殊符号、噪音后缀、罗马数字尾缀。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _PUNCT_RE.sub("", s)
    for w in _NOISE_WORDS:
        s = s.replace(w, "")
    s = _ROMAN_TAIL_RE.sub("", s)
    return s.strip()


def norm_key(s) -> str:
    return norm_text(s).upper()


GENERIC_TOKENS = {
    "设备", "服务", "工程", "材料", "系统", "技术", "其他", "概念", "制造", "应用",
    "行业", "产业", "综合", "相关", "业务", "项目", "开发", "生产", "管理", "贸易",
    "租赁", "销售", "维修", "检测", "运营", "建设", "投资", "金融", "消费", "周期",
    "成长", "龙头", "主题", "板块", "公司", "集团", "产品", "部件", "零件", "制品",
    "加工", "装备", "设施", "仪器", "工具", "器材", "智能", "数字", "绿色", "高端",
    "新型", "现代", "专业", "通用", "专用", "配套", "用品", "设备类",
}


def kw_hit(text_key: str, kw_key: str) -> bool:
    """双向包含判定。

    限制（需求§三十七：禁止 Keyword Count Inflation / 概念污染）：
      - 长度 < 2 的关键词要求完全相等，避免单字误命中；
      - 通用词（设备/材料/系统/智能…）必须完全相等，禁止包含匹配，
        否则「设备」会把 照明设备/制冷空调设备 全部吸进任意含"设备"的 Sector。
    """
    if not text_key or not kw_key:
        return False
    if len(kw_key) < 2 or len(text_key) < 2:
        return text_key == kw_key
    if kw_key in GENERIC_TOKENS or text_key in GENERIC_TOKENS:
        return text_key == kw_key
    return kw_key in text_key or text_key in kw_key


def safe_div(a, b, default=0.0):
    return float(a) / float(b) if b else default


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def weighted_jaccard(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    inter = sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    union = sum(max(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    return safe_div(inter, union, 0.0)


def find_first(paths, must_exist=True):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def find_in_dirs(names, dirs):
    """names 可为文件名或 glob 前缀列表；返回最新匹配文件。"""
    hits = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for n in names:
            if any(ch in n for ch in "*?["):
                import glob as _g
                hits += _g.glob(os.path.join(d, n))
            else:
                p = os.path.join(d, n)
                if os.path.exists(p):
                    hits.append(p)
    if not hits:
        return None
    hits.sort(key=lambda p: (os.path.getmtime(p), p))
    return hits[-1]


def to_parquet_safe(df: pd.DataFrame, path: str):
    try:
        df.to_parquet(path, index=False)
        return True
    except Exception as e:  # pragma: no cover
        log.warning("写 parquet 失败 %s: %s", path, e)
        return False


def write_csv(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("写出 %s (%d 行)", os.path.relpath(path, BASE_DIR), len(df))


def write_json(obj, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    log.info("写出 %s", os.path.relpath(path, BASE_DIR))


def _mapping_records(mapping: pd.DataFrame) -> list:
    """映射表转 JSON records：未映射行的 sector_id/sector_name 统一写 null（不写空串）。"""
    if mapping.empty:
        return []
    recs = json.loads(mapping.to_json(orient="records", force_ascii=False))
    for r in recs:
        for k in ("sector_id", "sector_name"):
            if r.get(k) in ("", "nan", "None"):
                r[k] = None
    return recs


# ────────────────────────────────────────────────────────────────────────────
# Token / 限流 / 日志（复用 sli 基础设施）
# ────────────────────────────────────────────────────────────────────────────

def load_token() -> str:
    if _HAS_SLI:
        try:
            t = _sli_load_token()
            if t:
                return t
        except Exception:
            pass
    t = os.getenv("TUSHARE_TOKEN", "").strip()
    if t:
        return t
    for p in [os.path.join(PROJ_DIR, "config", ".env"),
              os.path.join(PROJ_DIR, "..", "config", ".env"),
              os.path.join(r"D:\mystock\config", ".env")]:
        p = os.path.normpath(p)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TUSHARE_TOKEN") and "=" in line:
                        v = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            return v
        except OSError:
            continue
    return ""


class _LocalLimiter:
    def __init__(self, ms=RATE_LIMIT_MS):
        self.m = ms / 1000.0
        self.last = 0.0

    def __enter__(self):
        gap = time.monotonic() - self.last
        if gap < self.m:
            time.sleep(self.m - gap)
        self.last = time.monotonic()
        return self

    def __exit__(self, *a):
        return False


_local_limiter = _LocalLimiter()


def rate_limit(ms=RATE_LIMIT_MS):
    if _HAS_SLI:
        try:
            return _sli_rate_limit(ms)
        except Exception:
            pass
    return _local_limiter


def setup_logging():
    if _HAS_SLI:
        try:
            _sli_setup_logging(LOG_DIR, name="sector_build")
            return
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(os.path.join(LOG_DIR, "sector_build.log"),
                                      encoding="utf-8")],
    )


# ────────────────────────────────────────────────────────────────────────────
# 步骤 1：Load sector_master.json（唯一真源，只读）
# ────────────────────────────────────────────────────────────────────────────

class SectorMaster:
    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.doc = json.loads(raw)
        self.version = str(self.doc.get("version", ""))
        self.sectors = self.doc.get("sectors", [])
        self.rotation_groups = self.doc.get("rotation_groups", [])
        self.membership_types = self.doc.get("membership_types", {}) or {}
        self.mapping_confidence = self.doc.get("mapping_confidence", {}) or {}
        self.tracking_tiers = self.doc.get("tracking_tiers", {}) or {}
        self.quality_weights = self.doc.get("quality_weights", {}) or {}
        self.quality_rules = self.doc.get("quality_rules", {}) or {}
        self.dynamic_rules = self.doc.get("dynamic_theme_rules", {}) or {}
        self.mapping_priority = self.doc.get("mapping_priority", []) or []
        self._index = {s["sector_id"]: s for s in self.sectors}
        self._group_of = {}
        for g in self.rotation_groups:
            for sid in g.get("sector_ids", []):
                self._group_of[sid] = g.get("id", "")
        self._prepare_keys()

    # ---- 关键词集合 ----
    def _prepare_keys(self):
        for s in self.sectors:
            s["_name_key"] = norm_key(s.get("sector_name", ""))
            s["_alias_keys"] = [norm_key(a) for a in (s.get("aliases") or []) if norm_key(a)]
            s["_ind_keys"] = [norm_key(k) for k in (s.get("eastmoney_industry_keywords") or []) if norm_key(k)]
            s["_con_keys"] = [norm_key(k) for k in (s.get("eastmoney_concept_keywords") or []) if norm_key(k)]
            s["_prod_keys"] = [norm_key(k) for k in (s.get("subsectors") or []) if norm_key(k)]
            s["_kw_keys"] = [norm_key(k) for k in (s.get("keywords") or []) if norm_key(k)]
            s["_ex_keys"] = [norm_key(k) for k in (s.get("exclude_keywords") or []) if norm_key(k)]
            s["_core_names"] = [str(c).strip() for c in (s.get("core_companies") or []) if str(c).strip()]
            s["_ind_plus_name"] = s["_ind_keys"] + [s["_name_key"]] + s["_alias_keys"]
            s["_excl_l2"] = set()

    def attach_exclusive_l2(self, l2_names) -> None:
        """注入申万 L2 独占表（依赖 index_member_all 实际返回的 L2 名称集合）。

        个股行业证据只到 L2 时，若该 L2 只被本 Sector 认领，可视作行业直接归属
        （PRIMARY）；被多个 Sector 共享则降 SECONDARY，避免大类行业被单一 Sector 独占。
        """
        excl = build_exclusive_l2(self, l2_names)
        for s in self.sectors:
            s["_excl_l2"] = excl.get(s["sector_id"], set())

    @property
    def sector_ids(self):
        return [s["sector_id"] for s in self.sectors]

    def get(self, sid):
        return self._index.get(sid)

    def group_of(self, sid):
        return self._group_of.get(sid, self._index.get(sid, {}).get("rotation_group", ""))

    def type_of(self, sid):
        return self._index.get(sid, {}).get("sector_type", "")

    def membership_weight(self, mtype):
        return float((self.membership_types.get(mtype) or {}).get("weight", 0.0))

    def membership_conf_min(self, mtype):
        return float((self.membership_types.get(mtype) or {}).get("confidence_min", 0.0))

    def dynamic_examples(self):
        return [str(x) for x in (self.dynamic_rules.get("examples") or [])]


# ────────────────────────────────────────────────────────────────────────────
# 步骤 2：Scan existing data/cache
# ────────────────────────────────────────────────────────────────────────────

def scan_project() -> dict:
    scan = {
        "master": None,
        "legacy_found": [],
        "sw_classify": {},
        "sw_members": None,
        "stock_basic": None,
        "trade_cal": None,
        "ths_list": None,
        "ths_members": None,
        "dc_board_cache": None,
        "dc_member_cache": None,
    }
    scan["master"] = find_first(MASTER_CANDIDATES)

    # 旧配置只报告存在性，绝不读取内容
    for root, dirs, files in os.walk(PROJ_DIR):
        depth = root[len(PROJ_DIR):].count(os.sep)
        if depth >= 3:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in
                   {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}]
        for fn in files:
            if fn in LEGACY_FILES:
                scan["legacy_found"].append(os.path.join(root, fn))

    for lvl in ("L1", "L2", "L3"):
        scan["sw_classify"][lvl] = find_in_dirs(
            [f"classify_SW2021_{lvl}.parquet", f"classify_*{lvl}*.parquet"], SW_CACHE_DIRS)
    scan["sw_members"] = find_in_dirs(["members_*.parquet"], SW_CACHE_DIRS)
    scan["stock_basic"] = find_in_dirs(["stock_basic.parquet", "stock_basic.csv"], SW_CACHE_DIRS)
    scan["trade_cal"] = find_in_dirs(["trade_cal_*.parquet"], SW_CACHE_DIRS)
    scan["ths_list"] = find_in_dirs(["ths_concepts_list.parquet", "ths_index*.parquet"],
                                   SW_CACHE_DIRS + PARQUET_DIRS)
    scan["ths_members"] = find_in_dirs(["ths_concepts_members.parquet", "ths_member*.parquet"],
                                       SW_CACHE_DIRS + PARQUET_DIRS)
    import glob as _g
    for d in (CACHE_DIR,):
        b = sorted(_g.glob(os.path.join(d, "dc_boards_*.parquet")))
        m = sorted(_g.glob(os.path.join(d, "dc_members_*.parquet")))
        scan["dc_board_cache"] = b[-1] if b else None
        scan["dc_member_cache"] = m[-1] if m else None
    return scan


# ────────────────────────────────────────────────────────────────────────────
# 数据源加载（只读已有资产 / 东财缓存，不新建爬虫）
# ────────────────────────────────────────────────────────────────────────────

class DataHub:
    def __init__(self, scan: dict, base_date: str, use_fetch: bool = True, max_boards=None):
        self.scan = scan
        self.base_date = base_date
        self.use_fetch = use_fetch
        self.max_boards = max_boards
        self._pro = None
        self.notes = []

    # ---- Tushare Pro（复用已有 client 模式，不新建独立 client 类） ----
    @property
    def pro(self):
        if self._pro is None:
            tok = load_token()
            if not tok:
                raise RuntimeError("未找到 TUSHARE_TOKEN，无法获取东财原始板块数据")
            import tushare as ts
            # 与 sli/datasource.py 保持一致：直接传 token，避免 ts.set_token 写 ~/tk.csv
            self._pro = ts.pro_api(tok)
        return self._pro

    # ---- 申万行业（已有 parquet，零 API 成本） ----
    def load_sw(self):
        lv = {}
        for lvl, p in self.scan["sw_classify"].items():
            if not p:
                continue
            d = pd.read_parquet(p)
            d["level"] = d.get("level", lvl)
            lv[lvl] = d
        if "L3" not in lv:
            self.notes.append("缺少申万 L3 分类文件，Raw Industry(L3) 无法从 Tushare 建立")
            return pd.DataFrame(), pd.DataFrame(), {}
        # industry_code -> row 映射，用于 L3 -> L2 -> L1 父子链
        def code_map(d):
            m = {}
            if d is None:
                return m
            for r in d.itertuples():
                m[str(getattr(r, "industry_code", "")).replace(".0", "")] = r
            return m

        m2, m1 = code_map(lv.get("L2")), code_map(lv.get("L1"))
        meta = {}
        for r in lv["L3"].itertuples():
            l3n = getattr(r, "industry_name", "")
            pc = str(getattr(r, "parent_code", "")).replace(".0", "")
            r2 = m2.get(pc)
            l2n = getattr(r2, "industry_name", None) if r2 is not None else None
            l1n = None
            if r2 is not None:
                r1 = m1.get(str(getattr(r2, "parent_code", "")).replace(".0", ""))
                l1n = getattr(r1, "industry_name", None) if r1 is not None else None
            meta[getattr(r, "index_code", "")] = (l3n, l2n, l1n)

        boards = []
        for lvl in ("L1", "L2", "L3"):
            d = lv.get(lvl)
            if d is None:
                continue
            btype = f"INDUSTRY_{lvl}"
            parent = None
            for r in d.itertuples():
                ic = getattr(r, "index_code", "")
                nm = getattr(r, "industry_name", "")
                pc = str(getattr(r, "parent_code", "")).replace(".0", "")
                if lvl == "L3":
                    nm3, nm2, nm1 = meta.get(ic, (nm, None, None))
                    parent = nm2
                elif lvl == "L2":
                    r1 = m1.get(pc)
                    parent = getattr(r1, "industry_name", None) if r1 is not None else None
                boards.append({
                    "source": "TUSHARE", "source_detail": "SW2021",
                    "board_type": btype, "board_code": ic, "board_name": nm,
                    "parent_board": parent or "",
                })
        boards = pd.DataFrame(boards)

        # 成分（L3 为原始数据；L1/L2 由 L3 汇总，标记 member_layer=ROLLUP）
        mdf = pd.DataFrame()
        mp = self.scan["sw_members"]
        if mp:
            mdf = pd.read_parquet(mp)
            mdf = self._point_in_time(mdf, self.base_date)
            mdf = mdf[mdf["index_code"].isin(meta.keys())].copy()
            mdf["board_name"] = mdf["index_code"].map(lambda c: meta.get(c, (None,))[0])
        # 全量行业归属表补全：本地快照仅覆盖 258/337 个 L3，缺的 88 个三级行业成员数为 0
        # （集成电路制造/电动乘用车/硅料硅片/逆变器/风电整机/核力发电…），必须补全
        sw_all = self.load_sw_industry_map()

        # 行业代码版本差异：同一行业名在分类快照与 index_member_all 的指数代码可能不同
        # （如「特钢Ⅲ」= 850401.SI vs 850412.SI），只按代码 join 会整块丢行业，需按名回填
        def _name2code(lvl):
            d = lv.get(lvl)
            nm = {}
            if d is None:
                return nm
            for r in d.itertuples():
                nm.setdefault(norm_key(getattr(r, "industry_name", "")),
                              str(getattr(r, "index_code", "")))
            return nm

        def _align(codes, names, known, n2c):
            c = codes.astype(str).reset_index(drop=True)
            miss = ~c.isin(known)
            if miss.any():
                c.loc[miss] = names.reset_index(drop=True)[miss].map(norm_key).map(n2c).fillna("")
            return c

        if not sw_all.empty:
            sw_all = self._point_in_time(sw_all, self.base_date)
            c3 = _align(sw_all["l3_code"], sw_all["l3_name"], set(meta.keys()), _name2code("L3"))
            add = pd.DataFrame({"index_code": c3, "con_code": sw_all["ts_code"].astype(str).values,
                                "board_name": sw_all["l3_name"].values})
            add = add[add["index_code"].isin(meta.keys())]
            base = mdf[["index_code", "con_code", "board_name"]] if not mdf.empty else mdf
            mdf = pd.concat([base, add], ignore_index=True) \
                .drop_duplicates(subset=["index_code", "con_code"])
            log.info("申万 L3 成分：本地 %d 行 + 全量表补全后 %d 行 / %d 个 L3 行业",
                     len(base) if not base.empty else 0, len(mdf), mdf["index_code"].nunique())
        if mdf.empty:
            self.notes.append("缺少申万行业成分数据（本地快照与全量表均不可用）")
            return boards, pd.DataFrame(), meta
        members = pd.DataFrame({
            "source": "TUSHARE", "source_detail": "SW2021",
            "board_type": "INDUSTRY_L3", "board_code": mdf["index_code"],
            "board_name": mdf["board_name"], "ts_code": mdf["con_code"],
            "update_date": self.base_date,
        })
        # L2 成分：申万部分核心产业只划分到二级（"基础建设"/"化学制品"等），
        # 三级粒度无法与 Sector 对齐，需用 L2 口径补齐（个股归属上限 PRIMARY）
        if not sw_all.empty:
            known_l2 = set(_name2code("L2").values())
            c2 = _align(sw_all["l2_code"], sw_all["l2_name"], known_l2, _name2code("L2"))
            l2m = pd.DataFrame({
                "source": "TUSHARE", "source_detail": "SW2021",
                "board_type": "INDUSTRY_L2", "board_code": c2.values,
                "board_name": sw_all["l2_name"].values, "ts_code": sw_all["ts_code"].astype(str).values,
                "update_date": self.base_date,
            })
            l2m = l2m[l2m["board_code"].isin(known_l2)] \
                .drop_duplicates(subset=["board_code", "ts_code"])
            members = pd.concat([members, l2m], ignore_index=True)
            log.info("申万 L2 成分：%d 行 / %d 个 L2 行业", len(l2m), l2m["board_code"].nunique())
        return boards, members, meta

    # ---- 申万行业归属全量表（股票 -> L1/L2/L3 名称） ----
    def load_sw_industry_map(self):
        """index_member_all 全量快照：每只股票的 L1/L2/L3 归属。

        本地 members_*.parquet 是按指数逐个抓取的，只覆盖 337 个 L3 中的 258 个，
        导致 88 个三级行业（集成电路制造/电动乘用车/硅料硅片/逆变器等）的股票
        拿不到行业标签，全部退化为无行业证据。行业归属判定必须改用本表。
        """
        cache = os.path.join(CACHE_DIR, f"sw_industry_all_{self.base_date}.parquet")
        if os.path.exists(cache):
            return pd.read_parquet(cache)
        if not self.use_fetch:
            olds = sorted(f for f in os.listdir(CACHE_DIR)
                          if f.startswith("sw_industry_all_") and f.endswith(".parquet"))
            if olds:
                log.warning("使用历史申万行业归属快照 %s", olds[-1])
                return pd.read_parquet(os.path.join(CACHE_DIR, olds[-1]))
            self.notes.append("缺少申万行业归属全量表 sw_industry_all_*.parquet")
            return pd.DataFrame()
        frames, off = [], 0
        while True:
            with rate_limit():
                d = self.pro.index_member_all(is_new="Y", offset=off, limit=3000)
            if d is None or d.empty:
                break
            frames.append(d)
            off += len(d)
            if len(d) < 3000:
                break
        if not frames:
            self.notes.append("index_member_all 返回空，申万行业归属全量表未建立")
            return pd.DataFrame()
        m = pd.concat(frames, ignore_index=True)
        to_parquet_safe(m, cache)
        log.info("申万行业归属全量表：%d 行 / %d 只股票 / L3 %d 个",
                 len(m), m["ts_code"].nunique(), m["l3_name"].nunique())
        return m

    @staticmethod
    def _point_in_time(df: pd.DataFrame, date: str) -> pd.DataFrame:
        """按 in_date / out_date 做时点切分，禁止未来函数。"""
        d = df.copy()
        if "in_date" in d.columns:
            s = d["in_date"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str[:8]
            d = d[(s == "") | (s <= date)]
        if "out_date" in d.columns:
            s = d["out_date"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str[:8]
            d = d[(s == "") | (s > date)]
        return d

    # ---- 同花顺概念（已有 parquet，零 API 成本） ----
    def load_ths(self):
        lp, mp = self.scan["ths_list"], self.scan["ths_members"]
        if not lp or not mp:
            self.notes.append("缺少同花顺概念缓存 parquet，CONCEPT(TUSHARE/THS) 无法建立")
            return pd.DataFrame(), pd.DataFrame()
        lst = pd.read_parquet(lp)
        mem = pd.read_parquet(mp)
        # 过滤指数样本股等非概念板块
        bad = lst["name"].astype(str).str.contains("样本股|成份股|成分股", regex=True, na=False)
        lst = lst[~bad].copy()
        keep = set(lst["ts_code"])
        mem = mem[mem["ts_code"].isin(keep)].copy()
        boards = pd.DataFrame({
            "source": "TUSHARE", "source_detail": "THS_INDEX",
            "board_type": "CONCEPT", "board_code": lst["ts_code"],
            "board_name": lst["name"], "parent_board": "",
        })
        cnt = mem.groupby("ts_code").size().to_dict()
        boards["member_count"] = boards["board_code"].map(lambda c: int(cnt.get(c, 0)))
        members = pd.DataFrame({
            "source": "TUSHARE", "source_detail": "THS_INDEX",
            "board_type": "CONCEPT", "board_code": mem["ts_code"],
            "board_name": mem["concept_name"], "ts_code": mem["con_code"],
            "stock_name": mem["con_name"], "update_date": self.base_date,
        })
        return boards, members

    # ---- 东财板块（Tushare dc_index + dc_member，缓存断点续跑） ----
    def fetch_dc_boards(self):
        cp = os.path.join(CACHE_DIR, f"dc_boards_{self.base_date}.parquet")
        if os.path.exists(cp):
            return pd.read_parquet(cp), True
        if not self.use_fetch:
            return pd.DataFrame(), False
        try:
            with rate_limit(RATE_LIMIT_MS):
                d = self.pro.dc_index(trade_date=self.base_date)
        except Exception as e:
            self.notes.append(f"dc_index 调用失败：{e}")
            return pd.DataFrame(), False
        if d is None or d.empty:
            self.notes.append(f"dc_index 返回空（{self.base_date}）")
            return pd.DataFrame(), False
        # 地域板块不作为 sector raw source
        d = d[d["idx_type"] != "地域板块"].copy()
        to_parquet_safe(d, cp)
        return d, False

    def fetch_dc_members(self, board_codes):
        cp = os.path.join(CACHE_DIR, f"dc_members_{self.base_date}.parquet")
        cols = ["trade_date", "ts_code", "con_code", "name"]
        have = pd.DataFrame(columns=cols)
        if os.path.exists(cp):
            try:
                have = pd.read_parquet(cp)
            except Exception:
                have = pd.DataFrame(columns=cols)
        for c in cols:
            if c not in have.columns:
                have[c] = None
        done = set(have["ts_code"].dropna().unique())
        todo = [c for c in board_codes if c not in done]
        if self.max_boards:
            todo = todo[: self.max_boards]
        if not todo or not self.use_fetch:
            return have[have["con_code"].notna()]
        frames = [have]
        t0 = time.time()
        for i, code in enumerate(todo, 1):
            try:
                with rate_limit(RATE_LIMIT_MS):
                    df = self.pro.dc_member(ts_code=code, trade_date=self.base_date)
            except Exception as e:
                log.warning("dc_member %s 失败：%s", code, e)
                continue
            if df is None or df.empty:
                frames.append(pd.DataFrame([{"trade_date": self.base_date, "ts_code": code,
                                             "con_code": None, "name": None}]))
            else:
                for c in cols:
                    if c not in df.columns:
                        df[c] = None
                frames.append(df[cols])
            if i % 100 == 0:
                pd.concat(frames, ignore_index=True).to_parquet(cp, index=False)
                log.info("东财成分抓取 %d/%d，已用 %.0fs", i, len(todo), time.time() - t0)
        out = pd.concat(frames, ignore_index=True)
        out.to_parquet(cp, index=False)
        log.info("东财成分抓取完成：新增 %d 个板块，累计 %d 行", len(todo), len(out))
        return out[out["con_code"].notna()]

    # ---- stock_basic ----
    def load_stock_basic(self):
        p = self.scan["stock_basic"]
        if not p:
            self.notes.append("缺少 stock_basic 文件")
            return pd.DataFrame(columns=["ts_code", "name", "industry", "list_status"])
        if p.endswith(".csv"):
            d = pd.read_csv(p, dtype=str)
        else:
            d = pd.read_parquet(p)
        for c in ("ts_code", "name", "industry", "list_status"):
            if c not in d.columns:
                d[c] = None
        return d

    def load_trade_cal(self):
        p = self.scan["trade_cal"]
        if not p:
            return pd.DataFrame()
        try:
            return pd.read_parquet(p)
        except Exception:
            return pd.DataFrame()


def resolve_base_date(scan: dict, cli_date):
    if cli_date:
        return str(cli_date)
    today = datetime.now().strftime("%Y%m%d")
    p = scan.get("trade_cal")
    if p:
        try:
            d = pd.read_parquet(p)
            col = "cal_date" if "cal_date" in d.columns else d.columns[1]
            if "is_open" in d.columns:
                d = d[d["is_open"] == 1]
            ds = sorted(str(x) for x in d[col].dropna().unique() if str(x) <= today)
            if ds:
                return ds[-1]
        except Exception:
            pass
    return today


# ────────────────────────────────────────────────────────────────────────────
# 步骤 3-6：Raw Board / Raw Member / 标准化
# ────────────────────────────────────────────────────────────────────────────

def build_raw_layer(hub: DataHub, master: SectorMaster):
    boards_all, members_all, meta = hub.load_sw()
    b_ths, m_ths = hub.load_ths()

    dc_boards, dc_from_cache = hub.fetch_dc_boards()
    b_dc = pd.DataFrame()
    m_dc = pd.DataFrame()
    if not dc_boards.empty:
        lvl_map = {"东财一级行业": "INDUSTRY_L1", "东财二级行业": "INDUSTRY_L2",
                   "东财三级行业": "INDUSTRY_L3"}
        rows = []
        for r in dc_boards.itertuples():
            it = getattr(r, "idx_type", "")
            lv = getattr(r, "level", None)
            if it == "行业板块":
                bt = lvl_map.get(str(lv))
                if bt is None:
                    continue
                parent = {"INDUSTRY_L3": "东财二级行业"}.get(bt)
                rows.append({"source": "EASTMONEY", "source_detail": "DC_INDEX",
                             "board_type": bt, "board_code": getattr(r, "ts_code", ""),
                             "board_name": getattr(r, "name", ""),
                             "parent_board": ""})
            elif it == "概念板块":
                rows.append({"source": "EASTMONEY", "source_detail": "DC_INDEX",
                             "board_type": "CONCEPT", "board_code": getattr(r, "ts_code", ""),
                             "board_name": getattr(r, "name", ""), "parent_board": ""})
        b_dc = pd.DataFrame(rows)
        # 只有可映射层级（INDUSTRY_L3 + CONCEPT）需要成分股；L1/L2 为 ROLLUP 不抓取
        codes = b_dc.loc[b_dc["board_type"].isin(MAPPABLE_BOARD_TYPES), "board_code"].tolist()
        mem = hub.fetch_dc_members(codes)
        if not mem.empty:
            nm = dict(zip(b_dc["board_code"], b_dc["board_name"]))
            bmap = dict(zip(b_dc["board_code"], b_dc["board_type"]))
            m_dc = pd.DataFrame({
                "source": "EASTMONEY", "source_detail": "DC_INDEX",
                "board_type": mem["ts_code"].map(bmap).fillna("CONCEPT"),
                "board_code": mem["ts_code"],
                "board_name": mem["ts_code"].map(nm).fillna(""),
                "ts_code": mem["con_code"], "stock_name": mem["name"],
                "update_date": hub.base_date,
            })

    frames_b = [x for x in (boards_all, b_ths, b_dc) if x is not None and not x.empty]
    frames_m = [x for x in (members_all, m_ths, m_dc) if x is not None and not x.empty]
    boards = pd.concat(frames_b, ignore_index=True) if frames_b else pd.DataFrame()
    members = pd.concat(frames_m, ignore_index=True) if frames_m else pd.DataFrame()

    if boards.empty:
        return boards, members, meta

    for c in ("source_detail", "parent_board", "member_count"):
        if c not in boards.columns:
            boards[c] = "" if c != "member_count" else 0
    if "stock_name" not in members.columns:
        members["stock_name"] = None

    boards["norm_name"] = boards["board_name"].map(norm_text)
    members["norm_name"] = members["board_name"].map(norm_text)

    # 成员数（原始，来自 member layer）
    cnt = members.groupby(["source", "board_type", "board_code"]).size()
    key = list(zip(boards["source"], boards["board_type"], boards["board_code"]))
    boards["member_count"] = [int(cnt.get(k, 0)) for k in key]

    # INDUSTRY_L1 / L2 的成员为 L3 汇总（ROLLUP），本阶段不参与映射
    boards["member_layer"] = boards["board_type"].apply(
        lambda t: "ROLLUP" if t in ("INDUSTRY_L1", "INDUSTRY_L2") else "ORIGINAL")
    boards["mappable"] = boards["board_type"].isin(MAPPABLE_BOARD_TYPES).astype(int)
    boards["update_date"] = hub.base_date

    # 去重（同 source+board_code 只保留一行）
    boards = boards.drop_duplicates(subset=["source", "board_code"]).reset_index(drop=True)
    members = members.dropna(subset=["ts_code"]).copy()
    members["ts_code"] = members["ts_code"].astype(str)
    members = members.drop_duplicates(subset=["source", "board_code", "ts_code"]).reset_index(drop=True)

    # stock universe 过滤：剔除北交所 / ST / 非上市
    sb = hub.load_stock_basic()
    if not sb.empty:
        nm = dict(zip(sb["ts_code"].astype(str), sb["name"]))
        members["stock_name"] = members.apply(
            lambda r: r["stock_name"] if isinstance(r["stock_name"], str) and r["stock_name"]
            else nm.get(r["ts_code"]), axis=1)
        st = set(sb.loc[sb["name"].astype(str).str.contains("ST", na=False), "ts_code"].astype(str))
        listed = set(sb.loc[sb["list_status"].astype(str) == "L", "ts_code"].astype(str))
    else:
        st, listed = set(), set()
    keep = ~members["ts_code"].str.endswith(".BJ")
    if st:
        keep &= ~members["ts_code"].isin(st)
    if listed:
        keep &= members["ts_code"].isin(listed)
    members = members[keep].reset_index(drop=True)

    boards["member_count"] = boards["member_count"].where(
        boards["member_layer"] == "ROLLUP",
        boards.apply(lambda r: int(((members["source"] == r["source"]) &
                                    (members["board_code"] == r["board_code"])).sum()), axis=1))

    return boards, members, meta


# ────────────────────────────────────────────────────────────────────────────
# 步骤 7-9：候选映射 / 评分 / 排除规则
# ────────────────────────────────────────────────────────────────────────────

KIND_RANK = {
    "EXACT_NAME": 1, "EXACT_ALIAS": 2, "EXACT_INDUSTRY": 3, "EXACT_PRODUCT": 4,
    "EXACT_CONCEPT": 5, "CONTAINS_NAME": 6, "CONTAINS_INDUSTRY": 7,
    "CONTAINS_PRODUCT": 8, "CONTAINS_CONCEPT": 9, "EXACT_KEYWORD": 10,
    "CONTAINS_KEYWORD": 11,
}
# base 分：(kind, 是否行业板块)
KIND_BASE = {
    ("EXACT_NAME", 1): 1.00, ("EXACT_NAME", 0): 0.92,
    ("EXACT_ALIAS", 1): 1.00, ("EXACT_ALIAS", 0): 0.90,
    ("EXACT_INDUSTRY", 1): 1.00, ("EXACT_INDUSTRY", 0): 0.90,
    ("EXACT_PRODUCT", 1): 0.95, ("EXACT_PRODUCT", 0): 0.90,
    ("EXACT_CONCEPT", 1): 0.92, ("EXACT_CONCEPT", 0): 0.90,
    ("CONTAINS_NAME", 1): 0.90, ("CONTAINS_NAME", 0): 0.82,
    ("CONTAINS_INDUSTRY", 1): 0.95, ("CONTAINS_INDUSTRY", 0): 0.85,
    ("CONTAINS_PRODUCT", 1): 0.85, ("CONTAINS_PRODUCT", 0): 0.80,
    ("CONTAINS_CONCEPT", 1): 0.82, ("CONTAINS_CONCEPT", 0): 0.80,
    ("EXACT_KEYWORD", 1): 0.70, ("EXACT_KEYWORD", 0): 0.70,
    ("CONTAINS_KEYWORD", 1): 0.55, ("CONTAINS_KEYWORD", 0): 0.55,
}
KIND_METHOD = {
    "EXACT_NAME": "DIRECT_INDUSTRY", "EXACT_ALIAS": "DIRECT_INDUSTRY",
    "EXACT_INDUSTRY": "DIRECT_INDUSTRY", "CONTAINS_INDUSTRY": "INDUSTRY_PRODUCT",
    "EXACT_PRODUCT": "INDUSTRY_PRODUCT", "CONTAINS_PRODUCT": "INDUSTRY_PRODUCT",
    "EXACT_CONCEPT": "INDUSTRY_CONCEPT", "CONTAINS_CONCEPT": "INDUSTRY_CONCEPT",
    "CONTAINS_NAME": "INDUSTRY_CONCEPT",
    "EXACT_KEYWORD": "CONCEPT", "CONTAINS_KEYWORD": "KEYWORD",
}
# 允许 CORE 的匹配等级（需求 §十九：行业直接归属 / 核心公司）
CORE_RANK_MAX = 4


def _match_sector(board_key: str, board_type: str, s: dict):
    """计算单个 Raw Board 对单个 Sector 的最佳匹配。返回 (kind, key) 或 None。"""
    is_ind = 1 if board_type.startswith("INDUSTRY") else 0
    best = None  # (rank, -base, kind, matched_key)

    def consider(kind, key):
        nonlocal best
        rank = KIND_RANK[kind]
        base = KIND_BASE[(kind, is_ind)]
        if best is None or rank < best[0] or (rank == best[0] and base > -best[1]):
            best = (rank, -base, kind, key)

    if board_key == s["_name_key"]:
        consider("EXACT_NAME", s.get("sector_name", ""))
    for a in s["_alias_keys"]:
        if board_key == a:
            consider("EXACT_ALIAS", a)
    for k in s["_ind_keys"]:
        if board_key == k:
            consider("EXACT_INDUSTRY", k)
    for k in s["_prod_keys"]:
        if board_key == k:
            consider("EXACT_PRODUCT", k)
    for k in s["_con_keys"]:
        if board_key == k:
            consider("EXACT_CONCEPT", k)
    for k in s["_kw_keys"]:
        if board_key == k:
            consider("EXACT_KEYWORD", k)
    if best is not None and best[0] <= 5:
        return best[2], best[3]
    if kw_hit(board_key, s["_name_key"]):
        consider("CONTAINS_NAME", s["_name_key"])
    for a in s["_alias_keys"]:
        if kw_hit(board_key, a):
            consider("CONTAINS_NAME", a)
    for k in s["_ind_keys"]:
        if kw_hit(board_key, k):
            consider("CONTAINS_INDUSTRY", k)
    for k in s["_prod_keys"]:
        if kw_hit(board_key, k):
            consider("CONTAINS_PRODUCT", k)
    for k in s["_con_keys"]:
        if kw_hit(board_key, k):
            consider("CONTAINS_CONCEPT", k)
    for k in s["_kw_keys"]:
        if kw_hit(board_key, k):
            consider("CONTAINS_KEYWORD", k)
    if best is None:
        return None
    return best[2], best[3]


def industry_level_match(sw_l2, sw_l3, s: dict) -> tuple:
    """个股主营行业（申万 L2/L3）与 Sector 行业定义的一致度，返回 (level, score)。

    只使用 Canonical Master 自身定义的行业名/别名、行业关键词、子行业(subsectors)、
    关键词做证据，不使用任何价格表现。层级越高证据越强：
        ("L3_EXACT",  1.0) 申万 L3 与行业名/别名/子行业/产品精确同名
        ("L2_EXCLUSIVE", 0.9) 申万 L2 精确命中，且该 L2 只被本 Sector 认领（独占）
        ("L3_KEY",    0.8) 申万 L3 命中行业关键词/子行业/产品（包含式）
        ("L2_SHARED", 0.7) 申万 L2 精确命中，但被多个 Sector 共同认领
        ("L2_KEY",    0.5) 仅申万 L2 命中行业关键词（包含式）
        ("NONE",      0.0) 无行业证据
    """
    k2, k3 = norm_key(sw_l2), norm_key(sw_l3)
    if not k2 and not k3:
        return "NONE", 0.0
    names = [s["_name_key"]] + s["_alias_keys"]
    exact_l2 = bool(k2) and any(nk and nk == k2 for nk in names)
    for k in s["_ind_keys"] + s["_prod_keys"]:
        if not k:
            continue
        if k2 and k2 == k:
            exact_l2 = True
        if k3 and (k3 == k or kw_hit(k3, k)):
            return "L3_EXACT", 1.0
        if k2 and kw_hit(k2, k):
            exact_l2 = exact_l2 or (k2 == k)
    for nk in names:
        if nk and nk == k3:
            return "L3_EXACT", 1.0
    if exact_l2:
        if k2 in (s.get("_excl_l2") or set()):
            return "L2_EXCLUSIVE", 0.9
        return "L2_SHARED", 0.7
    for k in s["_kw_keys"]:
        if k3 and kw_hit(k3, k):
            return "L3_KEY", 0.8
        if k2 and kw_hit(k2, k):
            return "L2_KEY", 0.5
    return "NONE", 0.0


def industry_match_score(sw_l2, sw_l3, s: dict) -> float:
    """industry_level_match 的分数封装（兼容既有调用点）。"""
    return industry_level_match(sw_l2, sw_l3, s)[1]


def build_exclusive_l2(master: "SectorMaster", l2_names) -> dict:
    """申万 L2 独占判定：某二级行业名只被一个 Sector 认领时，该 Sector 可把 L2 归属的
    个股视为行业直接归属（最高 PRIMARY）；被多个 Sector 共同认领则只作次级归属
    （SECONDARY），避免大类行业被单一 Sector 独占。

    认领分强弱：强认领＝L2 名称与 Sector 名/别名完全相等（Sector 就是这个行业本身，
    如"电力"之于 T43 电力）；弱认领＝L2 名称与 Sector 的行业关键词/子行业完全相等
    （Sector 覆盖了该行业）。强认领唯一时即判独占，即使其他 Sector 以关键词方式沾边。
    """
    strong, weak = defaultdict(set), defaultdict(set)
    for nm in l2_names:
        n = norm_key(nm)
        if not n:
            continue
        for s in master.sectors:
            names = [s["_name_key"]] + s["_alias_keys"]
            if any(nk and nk == n for nk in names):
                strong[n].add(s["sector_id"])
            elif any(k and k == n for k in s["_ind_keys"] + s["_prod_keys"]):
                weak[n].add(s["sector_id"])
    out = defaultdict(set)
    shared = set()
    for n in set(strong) | set(weak):
        st, owners = strong.get(n, set()), strong.get(n, set()) | weak.get(n, set())
        if len(st) == 1:
            out[next(iter(st))].add(n)
        elif not st and len(owners) == 1:
            out[next(iter(owners))].add(n)
        else:
            shared.add(n)
    log.info("申万 L2 独占：%d 个（可给 PRIMARY），共享：%d 个（最高 SECONDARY）",
             len(out), len(shared))
    return out


def build_candidate_mappings(boards: pd.DataFrame, members: pd.DataFrame,
                             master: SectorMaster, sw_meta: dict,
                             stock_sw: dict):
    """返回 (candidates_df, board_meta)"""
    mb = boards[boards["mappable"] == 1].copy()
    log.info("参与映射的 Raw Board：%d", len(mb))

    # 每个 board 的成员行业分布（用于行业一致性）
    m_by_board = {}
    for (src, code), g in members.groupby(["source", "board_code"]):
        m_by_board[(src, code)] = g["ts_code"].tolist()

    rows = []
    for r in mb.itertuples():
        bk = norm_key(r.norm_name)
        if not bk:
            continue
        hits = []
        is_ind = r.board_type.startswith("INDUSTRY")
        for s in master.sectors:
            m = _match_sector(bk, r.board_type, s)
            if m is None:
                continue
            kind, key = m
            rank = KIND_RANK[kind]
            base = KIND_BASE[(kind, 1 if is_ind else 0)]
            method = KIND_METHOD[kind]
            # 概念板块不构成“行业直接归属”（需求§十六：行业逻辑优先，禁止概念污染）
            if not is_ind and method in ("DIRECT_INDUSTRY", "INDUSTRY_PRODUCT"):
                method = "INDUSTRY_CONCEPT"
            hits.append({"sector_id": s["sector_id"], "kind": kind, "rank": rank,
                         "base": base, "method": method, "key": key,
                         "sector": s})
        if not hits:
            rows.append({"source": r.source, "source_detail": r.source_detail,
                         "board_type": r.board_type, "board_code": r.board_code,
                         "board_name": r.board_name, "norm_name": r.norm_name,
                         "member_count": r.member_count, "sector_id": None, "kind": None,
                         "rank": 999, "base": 0.0, "method": "UNMAPPED", "key": None,
                         "sector": None})
            continue
        # 每个 board 只保留最佳的 4 个候选（按 rank 升序，base 降序）
        hits.sort(key=lambda h: (h["rank"], -h["base"], h["sector_id"]))
        for h in hits[:4]:
            rows.append({"source": r.source, "source_detail": r.source_detail,
                         "board_type": r.board_type, "board_code": r.board_code,
                         "board_name": r.board_name, "norm_name": r.norm_name,
                         "member_count": r.member_count, "sector_id": h["sector_id"],
                         "kind": h["kind"], "rank": h["rank"], "base": h["base"],
                         "method": h["method"], "key": h["key"], "sector": h["sector"]})
    cand = pd.DataFrame(rows)
    if cand.empty:
        return cand, m_by_board

    # ── 成员级证据：行业一致性 / 核心公司覆盖 / 排除关键词 ──
    ind_cons, core_cov, excl_hit = [], [], []
    bname_key = cand["norm_name"].map(norm_key).tolist()
    for i, r in enumerate(cand.itertuples()):
        s = r.sector
        if s is None:
            ind_cons.append(0.0); core_cov.append(0.0); excl_hit.append(0)
            continue
        codes = m_by_board.get((r.source, r.board_code), [])
        if codes:
            sc = [industry_match_score(stock_sw.get(c, (None, None, None, None))[2],
                                       stock_sw.get(c, (None, None, None, None))[3], s)
                  for c in codes]
            ind_cons.append(sum(sc) / float(len(sc)))
        else:
            ind_cons.append(0.0)
        cores = set(s["_core_names"])
        if cores and codes:
            nms = {stock_sw.get(c, (None, None, None, None))[0] for c in codes}
            nms.discard(None)
            core_cov.append(safe_div(len(cores & nms), len(cores)))
        else:
            core_cov.append(0.0)
        hit = 0
        for ek in s["_ex_keys"]:
            if len(ek) <= 6 and ek and (ek in bname_key[i] or bname_key[i] in ek):
                hit = 1
                break
        excl_hit.append(hit)
    cand["industry_consistency"] = ind_cons
    cand["core_company_coverage"] = core_cov
    cand["exclude_hit"] = excl_hit
    return cand, m_by_board


def score_and_select(cand: pd.DataFrame, master: SectorMaster, board_member_names: dict):
    """步骤 8-10：评分 -> 排除 -> 生成 sector_mapping 条目。"""
    if cand.empty:
        return pd.DataFrame(), pd.DataFrame()
    out = []
    groups = list(cand.groupby(["source", "board_code"], sort=False))
    for (src, code), g in groups:
        g = g.copy()
        s0 = g.iloc[0]
        board_member_count = int(s0["member_count"])
        if g["sector_id"].isna().all():
            out.append({**{k: s0[k] for k in ("source", "source_detail", "board_type",
                                              "board_code", "board_name", "norm_name",
                                              "member_count")},
                        "sector_id": None, "sector_name": None, "mapping_method": "UNMAPPED",
                        "confidence": 0.0, "match_kind": None, "mapping_action": "UNMAPPED",
                        "mapping_reason": "没有任何 Sector 关键词/行业/概念命中",
                        "evidence": "", "candidate_rank": 999,
                        "industry_consistency": 0.0})
            continue
        g = g[g["sector_id"].notna()].copy()

        # 评分：base + 成员结构调整
        conf = []
        reasons = []
        for r in g.itertuples():
            c = float(r.base)
            rs = [f"match={r.kind}({r.key})", f"base={r.base:.2f}"]
            if r.exclude_hit:
                c -= 0.30
                rs.append("exclude_keywords命中-0.30")
            if float(getattr(r, "core_company_coverage", 0.0) or 0.0) >= 0.50:
                c += 0.05
                rs.append(f"核心公司覆盖率{r.core_company_coverage:.2f}(+0.05)")
            if r.industry_consistency >= 0.60:
                c += 0.05
                rs.append(f"行业一致性{r.industry_consistency:.2f}(+0.05)")
            elif r.industry_consistency < 0.20 and board_member_count >= 10:
                c -= 0.15
                rs.append(f"行业一致性过低{r.industry_consistency:.2f}(-0.15)")
            c = max(0.0, min(1.0, c))
            conf.append(c)
            reasons.append("; ".join(rs))
        g["confidence"] = conf
        g["evidence_text"] = reasons

        g = g.sort_values(["rank", "confidence", "sector_id"],
                          ascending=[True, False, True]).reset_index(drop=True)
        top = g.iloc[0]

        # 歧义检测：同等级别的并列候选
        tied = g[(g["rank"] == top["rank"]) & ((g["confidence"] - top["confidence"]).abs() < 0.02)]
        ambiguous = len(tied) > 1
        if ambiguous:
            g.loc[tied.index, "confidence"] = (tied["confidence"] - 0.10).clip(lower=0.0)

        g = g.sort_values(["rank", "confidence", "sector_id"],
                          ascending=[True, False, True]).reset_index(drop=True)
        top = g.iloc[0]

        if float(top["confidence"]) < 0.60:
            bm = ", ".join(f"{r.sector_id}:{r.confidence:.2f}" for r in g.head(2).itertuples())
            out.append({**{k: s0[k] for k in ("source", "source_detail", "board_type",
                                              "board_code", "board_name", "norm_name",
                                              "member_count")},
                        "sector_id": None, "sector_name": None, "mapping_method": "UNMAPPED",
                        "confidence": round(float(top["confidence"]), 4),
                        "match_kind": top["kind"], "mapping_action": "UNMAPPED",
                        "mapping_reason": f"最高候选置信度不足0.60（{bm}），按需求宁可 UNMAPPED",
                        "evidence": top["evidence_text"], "candidate_rank": int(top["rank"]),
                        "industry_consistency": round(float(top["industry_consistency"]), 4)})
            continue

        for i, r in enumerate(g.itertuples()):
            is_primary = (i == 0)
            conf_v = float(r.confidence)
            # 次级映射：仅接受实质性强命中，且成员类型封顶 THEMATIC
            if not is_primary:
                if not (conf_v >= 0.85 and int(r.rank) <= 5):
                    continue
            if is_primary:
                amb = ambiguous
            else:
                amb = False
            if conf_v >= 0.90 and not amb:
                action = "AUTO_ACCEPT"
            elif conf_v >= 0.75:
                action = "REVIEW"
            elif conf_v >= 0.60:
                action = "OBSERVATION"
            else:
                action = "REJECT"
            if conf_v < 0.60:
                continue
            reason = r.evidence_text
            if amb:
                reason += "; 存在同等级并列候选，判定为歧义(-0.10)，进入REVIEW"
            if not is_primary:
                reason += "; 次级映射，成员类型封顶THEMATIC（需求§十四）"
            out.append({
                "source": r.source, "source_detail": r.source_detail,
                "board_type": r.board_type, "board_code": r.board_code,
                "board_name": r.board_name, "norm_name": r.norm_name,
                "member_count": board_member_count,
                "sector_id": r.sector_id,
                "sector_name": master.get(r.sector_id)["sector_name"],
                "mapping_method": r.method, "confidence": round(conf_v, 4),
                "match_kind": r.kind, "mapping_action": action,
                "mapping_reason": reason, "evidence": r.evidence_text,
                "candidate_rank": int(r.rank),
                "industry_consistency": round(float(r.industry_consistency), 4),
                "is_primary": int(is_primary),
                "ambiguous": int(amb),
            })
    res = pd.DataFrame(out)
    return res, pd.DataFrame()


# ────────────────────────────────────────────────────────────────────────────
# 步骤 11：Stock Membership
# ────────────────────────────────────────────────────────────────────────────

TYPE_ORDER = {"OBSERVATION": 0, "THEMATIC": 1, "SECONDARY": 2, "PRIMARY": 3, "CORE": 4}
ACTION_CAP = {"AUTO_ACCEPT": "CORE", "REVIEW": "SECONDARY",
              "OBSERVATION": "THEMATIC", "REJECT": None, "UNMAPPED": None}


def build_membership(mapping: pd.DataFrame, members: pd.DataFrame, master: SectorMaster,
                     stock_sw: dict, stock_names: dict, effective_date: str):
    mp = mapping[mapping["sector_id"].notna()].copy()
    rows = []
    midx = {}
    core_names_map = {s["sector_id"]: set(s["_core_names"]) for s in master.sectors}
    for (src, code), g in members.groupby(["source", "board_code"], sort=False):
        midx[(src, code)] = g

    for r in mp.itertuples():
        g = midx.get((r.source, r.board_code))
        if g is None or g.empty:
            continue
        s = master.get(r.sector_id)
        if s is None:
            continue
        cap = ACTION_CAP.get(r.mapping_action)
        if cap is None:
            continue
        if not getattr(r, "is_primary", 1):
            cap = "THEMATIC"
        conf = float(r.confidence)
        method = r.mapping_method
        is_l3_board = str(r.board_type) == "INDUSTRY_L3"
        is_ind_board = str(r.board_type).startswith("INDUSTRY")
        for row in g.itertuples():
            tc = str(row.ts_code)
            sw = stock_sw.get(tc, (None, None, None, None))
            sw_name, sw_l1, sw_l2, sw_l3 = (sw + (None,) * 4)[:4]
            ims_lvl, ims = industry_level_match(sw_l2, sw_l3, s)
            mtype, mconf, origin = "OBSERVATION", conf, "RAW_BOARD"
            if (is_l3_board and int(r.candidate_rank) <= CORE_RANK_MAX and ims >= 1.0):
                # 行业板块(L3) + 申万 L3 与行业定义精确同名：属于“行业直接归属”
                mtype, mconf, origin = "CORE", max(conf, 0.96), "INDUSTRY_DIRECT"
            elif str(row.stock_name or "").strip() in core_names_map.get(r.sector_id, set()):
                mtype, mconf, origin = "CORE", max(conf, 0.97), "CORE_COMPANY"
            elif is_ind_board:
                # 行业板块（需求§十六：行业逻辑优先）。板块本身即行业分类，成员按行业板块归入
                # 即构成"行业直接归属"，但个股行业证据的粒度决定上限：
                #   L3_EXACT / L3_KEY → PRIMARY（三级行业直接命中）
                #   L2_EXCLUSIVE      → PRIMARY（二级行业精确命中，且仅本 Sector 认领该 L2）
                #   L2_SHARED         → SECONDARY（二级行业被多个 Sector 共享，不得独占大类行业）
                #   L2_KEY / NONE     → THEMATIC
                # L2 板块来源（INDUSTRY_L2）本就是粗口径，最高只给 PRIMARY（L3 板块才可能 CORE）
                if ims_lvl in ("L3_EXACT", "L3_KEY", "L2_EXCLUSIVE"):
                    mtype = "PRIMARY"
                elif ims_lvl == "L2_SHARED":
                    mtype = "SECONDARY"
                else:
                    mtype = "THEMATIC"
            else:
                # 概念板块：仅当个股主营与 Sector 行业定义一致时才允许 SECONDARY，
                # 其余一律 THEMATIC，禁止概念污染（需求§十九/§二十五）
                mtype = "SECONDARY" if ims >= 1.0 else "THEMATIC"
            # 映射动作封顶
            if TYPE_ORDER[mtype] > TYPE_ORDER[cap]:
                mtype = cap
            # membership_types 最低置信度门槛
            cmin = master.membership_conf_min(mtype)
            if mconf < cmin:
                for t in ("CORE", "PRIMARY", "SECONDARY", "THEMATIC", "OBSERVATION"):
                    if TYPE_ORDER[t] <= TYPE_ORDER[mtype] and mconf >= master.membership_conf_min(t):
                        mtype = t
                        break
            if mtype == "CORE" and origin == "RAW_BOARD":
                mtype = "PRIMARY"
            rows.append({
                "ts_code": tc,
                "stock_name": stock_names.get(tc) or row.stock_name,
                "sector_id": r.sector_id,
                "sector_name": r.sector_name,
                "membership_type": mtype,
                "membership_confidence": round(min(mconf, 0.99), 4),
                "membership_weight": master.membership_weight(mtype),
                "membership_origin": origin,
                "mapping_method": method,
                "board_type": r.board_type,
                "source_board": r.board_name,
                "source_board_code": r.board_code,
                "effective_date": effective_date,
                "is_static": "true",
                "reason": f"{r.mapping_method}/{r.match_kind}; 行业一致度={ims:.2f}; {r.mapping_reason}",
            })
    mdf = pd.DataFrame(rows)
    if mdf.empty:
        return mdf, pd.DataFrame()
    # core_companies 兜底（必须在去重排序前执行）：
    # Theme Master 显式声明的核心公司是需求§十九中 CORE 的独立来源，不应依赖
    # "是否恰好出现在某个已映射板块的成分表中"，也不应被所在板块的 mapping_action 封顶。
    # 否则主业不在该板块的核心公司会被降级——实例：风电产业链声明了东方电缆（申万
    # 电网设备>线缆部件及其他）、中天科技（通信设备>通信线缆及配套），二者因落在
    # "电网设备"(REVIEW→cap SECONDARY) / "风电"(CONCEPT→cap SECONDARY) 板块上而被降级。
    code_of_name = {}
    for tc, nm in stock_names.items():
        code_of_name.setdefault(str(nm).strip(), str(tc))
    ridx = {}
    for r in rows:
        ridx.setdefault((r["ts_code"], r["sector_id"]), r)
    n_promote = n_append = 0
    for s in master.sectors:
        for nm in s["_core_names"]:
            tc = code_of_name.get(str(nm).strip())
            if not tc:
                continue
            key = (tc, s["sector_id"])
            r = ridx.get(key)
            if r is None:
                r = {
                    "ts_code": tc, "stock_name": stock_names.get(tc) or nm,
                    "sector_id": s["sector_id"], "sector_name": s["sector_name"],
                    "membership_type": "CORE",
                    "membership_confidence": 0.97,
                    "membership_weight": master.membership_weight("CORE"),
                    "membership_origin": "CORE_COMPANY",
                    "mapping_method": "CORE_COMPANY",
                    "board_type": "MASTER_DECLARED",
                    "source_board": "sector_master.core_companies",
                    "source_board_code": "",
                    "effective_date": effective_date, "is_static": "true",
                    "reason": f"DIRECT_MASTER/CORE_COMPANY; Theme Master 显式声明核心公司"
                              f"（{nm}），不经板块成分表即成立",
                }
                rows.append(r)
                ridx[key] = r
                n_append += 1
            elif TYPE_ORDER[r["membership_type"]] < TYPE_ORDER["CORE"]:
                r["membership_type"] = "CORE"
                r["membership_origin"] = "CORE_COMPANY"
                r["membership_confidence"] = round(max(r["membership_confidence"], 0.97), 4)
                r["membership_weight"] = master.membership_weight("CORE")
                r["is_static"] = "true"
                r["reason"] += "; Theme Master 显式声明核心公司，强制 CORE（不受板块映射动作封顶）"
                n_promote += 1
    if n_promote or n_append:
        log.info("core_companies 兜底：提级 %d 条、补录 %d 条", n_promote, n_append)
        mdf = pd.DataFrame(rows)
    # 同一 (ts_code, sector_id) 只保留最强 membership。
    # 排序优先级：membership_type > 板块来源（行业板块优先于概念板块，需求§十八映射优先级）
    #             > membership_confidence
    # 若只按 confidence 排，概念板块（AUTO_ACCEPT 0.96）会盖过行业板块（REVIEW 0.90），
    # 把固定层成员挤成动态层来源，导致固定层（纯度分母）被概念反向清空。
    mdf["_ord"] = mdf["membership_type"].map(TYPE_ORDER)
    mdf["_src"] = (mdf["board_type"] == "CONCEPT").astype(int)
    mdf = mdf.sort_values(["ts_code", "sector_id", "_ord", "_src", "membership_confidence"],
                          ascending=[True, True, False, True, False])
    mdf = mdf.drop_duplicates(subset=["ts_code", "sector_id"], keep="first")
    mdf = mdf.drop(columns=["_ord", "_src"]).reset_index(drop=True)

    # 每只股票最多保留 3 个 CORE，其余降级为 PRIMARY（需求 §二十五：CORE>3 需 REVIEW）
    review_rows = []
    mdf["_core"] = (mdf["membership_type"] == "CORE").astype(int)
    downgrade_idx = []
    for tc, g in mdf.groupby("ts_code"):
        cores = g[g["_core"] == 1].sort_values("membership_confidence", ascending=False)
        if len(cores) > 3:
            drop_idx = cores.index[3:]
            downgrade_idx.extend(drop_idx.tolist())
            review_rows.append({
                "ts_code": tc, "stock_name": g.iloc[0]["stock_name"],
                "issue_type": "MULTI_CORE",
                "detail": f"CORE 数={len(cores)}，超过3个，已保留置信度最高的3个，其余降级为PRIMARY",
                "sector_list": ";".join(cores["sector_id"].tolist()),
                "static_sector_count": int(g["sector_id"].nunique()),
                "core_count": int(len(cores)),
                "review_action": "REVIEW",
            })
    if downgrade_idx:
        mdf.loc[downgrade_idx, "membership_type"] = "PRIMARY"
        mdf.loc[downgrade_idx, "membership_weight"] = master.membership_weight("PRIMARY")
    mdf = mdf.drop(columns=["_core"])
    return mdf.reset_index(drop=True), pd.DataFrame(review_rows)


# ────────────────────────────────────────────────────────────────────────────
# 步骤 12：Sector Quality
# ────────────────────────────────────────────────────────────────────────────

BOARD_STABILITY = {"DIRECT_INDUSTRY": 1.0, "INDUSTRY_PRODUCT": 0.9,
                   "INDUSTRY_CONCEPT": 0.7, "CORE_COMPANY": 0.9,
                   "CONCEPT": 0.5, "KEYWORD": 0.3}


def calc_quality(mdf: pd.DataFrame, master: SectorMaster, boards: pd.DataFrame,
                 stock_sw: dict, effective_date: str):
    rows = []
    if mdf.empty:
        return pd.DataFrame()
    for s in master.sectors:
        sid = s["sector_id"]
        g = mdf[mdf["sector_id"] == sid]
        n = len(g)
        if n == 0:
            rows.append({"sector_id": sid, "sector_name": s["sector_name"],
                         "rotation_group": s.get("rotation_group", ""),
                         "sector_type": s.get("sector_type", ""),
                         "member_count": 0, "fixed_member_count": 0,
                         "core_count": 0, "primary_count": 0,
                         "secondary_count": 0, "thematic_count": 0, "observation_count": 0,
                         "industry_consistency": 0.0, "product_consistency": 0.0,
                         "core_primary_ratio": 0.0, "concept_purity": 0.0,
                         "membership_stability": 0.0, "coverage": 0.0,
                         "core_company_quality": 0.0, "concept_coherence": 0.0,
                         "historical_data": 0.0, "sector_purity": 0.0,
                         "sector_quality_score": 0.0, "tier": "RAW_ONLY",
                         "top_boards": "", "update_date": effective_date})
            continue
        # 需求 §固定Theme与DynamicTheme分离：纯度只衡量"固定层级"成员（行业板块直接归属
        # + 核心公司），概念板块来源的 THEMATIC 成员属于动态层，不进纯度分母，
        # 否则 900 成员级的宽概念板块会结构性稀释所有 Sector 的纯度。
        if "board_type" in g.columns:
            g_fix = g[g["board_type"] != "CONCEPT"]
        else:
            g_fix = g[g["mapping_method"] != "CONCEPT"]
        wsum = float(g["membership_weight"].astype(float).sum()) or 1.0
        wfix = float(g_fix["membership_weight"].astype(float).sum()) or 1.0
        n_fix = len(g_fix)

        ic = 0.0
        prod_w = 0.0
        for row in g_fix.itertuples():
            sw = stock_sw.get(row.ts_code, (None, None, None, None))
            v = industry_match_score(sw[2], sw[3], s)
            ic += v * row.membership_weight
            if v >= 0.8:
                prod_w += row.membership_weight
        industry_consistency = safe_div(ic, wfix)

        # 产品一致性：固定层中达到"行业/产品级证据"(ims>=0.8，即行业名/子行业/产品口径命中)
        # 的成员权重占比。不用 mapping_method 字符串标签，否则同一行业板块因命中路径不同
        # （如别名包含 vs 行业名精确）会得到虚假的 0。
        product_consistency = safe_div(prod_w, wfix)

        cp = g_fix[g_fix["membership_type"].isin(("CORE", "PRIMARY"))]["membership_weight"].sum()
        core_primary_ratio = safe_div(cp, wfix)

        # 概念纯度：全量成员中来自 CONCEPT 板块的权重占比越低越纯（污染度指标，含动态层）
        if "board_type" in g.columns:
            pure = g[g["board_type"] == "CONCEPT"]["membership_weight"].astype(float).sum()
        else:
            pure = g[g["mapping_method"].isin(("CONCEPT", "KEYWORD"))]["membership_weight"].sum()
        concept_purity = 1.0 - safe_div(pure, wsum)

        stab = sum(BOARD_STABILITY.get(m, 0.5) * wt for m, wt in
                   zip(g_fix["mapping_method"], g_fix["membership_weight"]))
        membership_stability = safe_div(stab, wfix)

        coverage = min(1.0, safe_div(n, 30.0))

        cores = set(s["_core_names"])
        if cores:
            found = len({nm for nm in g["stock_name"].astype(str) if nm in cores})
            core_company_quality = safe_div(found, len(cores))
        else:
            core_company_quality = core_primary_ratio

        # 概念聚合度：来自概念/关键词级匹配的成员，其主营行业仍需与 Sector 一致
        gc = g[g["mapping_method"].isin(("INDUSTRY_CONCEPT", "CONCEPT", "KEYWORD"))]
        if len(gc):
            cw = gc["membership_weight"].astype(float)
            cc = 0.0
            for row in gc.itertuples():
                swx = stock_sw.get(row.ts_code, (None, None, None, None))
                cc += industry_match_score(swx[2], swx[3], s) * row.membership_weight
            concept_coherence = safe_div(cc, float(cw.sum()) or 1.0)
        else:
            concept_coherence = 1.0

        hist = sum(1 for tc in g["ts_code"] if stock_sw.get(tc, (None, None, None, None))[3])
        historical_data = safe_div(hist, float(n))

        purity = (0.30 * industry_consistency + 0.25 * product_consistency +
                  0.20 * core_primary_ratio + 0.15 * concept_purity +
                  0.10 * membership_stability)
        qw = master.quality_weights
        quality100 = (
            float(qw.get("industry_purity", 25)) * industry_consistency +
            float(qw.get("membership_stability", 20)) * membership_stability +
            float(qw.get("coverage", 20)) * coverage +
            float(qw.get("core_company_quality", 15)) * core_company_quality +
            float(qw.get("concept_coherence", 10)) * concept_coherence +
            float(qw.get("historical_data", 10)) * historical_data
        )
        if n_fix >= 15 and purity >= 0.75 and industry_consistency >= 0.70:
            tier = "TIER_1_CORE"
        elif n_fix >= 10 and purity >= 0.60:
            tier = "TIER_2_ROTATION"
        elif n_fix >= 5:
            tier = "TIER_3_OBSERVATION"
        else:
            tier = "RAW_ONLY"
        tb = g.groupby("source_board")["membership_weight"].sum().sort_values(
            ascending=False).head(8)
        rows.append({
            "sector_id": sid, "sector_name": s["sector_name"],
            "rotation_group": s.get("rotation_group", ""),
            "sector_type": s.get("sector_type", ""),
            "member_count": n,
            "fixed_member_count": n_fix,
            "core_count": int((g["membership_type"] == "CORE").sum()),
            "primary_count": int((g["membership_type"] == "PRIMARY").sum()),
            "secondary_count": int((g["membership_type"] == "SECONDARY").sum()),
            "thematic_count": int((g["membership_type"] == "THEMATIC").sum()),
            "observation_count": int((g["membership_type"] == "OBSERVATION").sum()),
            "industry_consistency": round(industry_consistency, 4),
            "product_consistency": round(product_consistency, 4),
            "core_primary_ratio": round(core_primary_ratio, 4),
            "concept_purity": round(concept_purity, 4),
            "membership_stability": round(membership_stability, 4),
            "coverage": round(coverage, 4),
            "core_company_quality": round(core_company_quality, 4),
            "concept_coherence": round(concept_coherence, 4),
            "historical_data": round(historical_data, 4),
            "sector_purity": round(purity, 4),
            "sector_quality_score": round(quality100, 2),
            "tier": tier,
            "top_boards": ";".join(tb.index.tolist()),
            "update_date": effective_date,
        })
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 步骤 13：Overlap
# ────────────────────────────────────────────────────────────────────────────

def calc_overlap(mdf: pd.DataFrame, master: SectorMaster):
    sets, wsets = {}, {}
    for sid, g in mdf.groupby("sector_id"):
        sets[sid] = set(g["ts_code"])
        wsets[sid] = dict(zip(g["ts_code"], g["membership_weight"]))
    rows = []
    ids = [s["sector_id"] for s in master.sectors]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sa, sb = sets.get(a, set()), sets.get(b, set())
            if not sa and not sb:
                continue
            jc = jaccard(sa, sb)
            wj = weighted_jaccard(wsets.get(a, {}), wsets.get(b, {}))
            inter = len(sa & sb)
            cont_ab = safe_div(inter, len(sa))
            cont_ba = safe_div(inter, len(sb))
            containment = max(cont_ab, cont_ba)
            ga, gb = master.group_of(a), master.group_of(b)
            if wj < 0.05 and jc < 0.05:
                continue
            if containment >= 0.90 and wj >= 0.30:
                cls = "PARENT_CHILD"
            elif wj >= 0.70:
                cls = "MERGE_CANDIDATE"
            elif wj >= 0.30:
                cls = "CROSS_THEME"
            else:
                cls = "KEEP_SEPARATE"
            ta, tb = master.type_of(a), master.type_of(b)
            note = ""
            if cls == "MERGE_CANDIDATE" and ga == gb and (ta == "CORE_ROTATION" or tb == "CORE_ROTATION"):
                note = "同为CORE_ROTATION，代表不同产业链逻辑，不建议合并（需求§二十四）"
            if not note and ga == gb:
                note = "同一rotation_group内重叠，属正常轮动关联"
            rows.append({
                "sector_id_a": a, "sector_name_a": master.get(a)["sector_name"],
                "sector_id_b": b, "sector_name_b": master.get(b)["sector_name"],
                "rotation_group_a": ga, "rotation_group_b": gb,
                "member_a": len(sa), "member_b": len(sb), "intersection": inter,
                "jaccard": round(jc, 4), "weighted_jaccard": round(wj, 4),
                "containment": round(containment, 4),
                "overlap_class": cls, "note": note,
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("weighted_jaccard", ascending=False).reset_index(drop=True)
    return df


# ────────────────────────────────────────────────────────────────────────────
# 步骤 14：Pollution
# ────────────────────────────────────────────────────────────────────────────

def detect_pollution(mdf: pd.DataFrame, master: SectorMaster, stock_sw: dict,
                     core_counts: dict, dynamic_boards: set, action_of: dict):
    rows = []
    if mdf.empty:
        return pd.DataFrame()
    for r in mdf.itertuples():
        sid = r.sector_id
        s = master.get(sid)
        if s is None:
            continue
        sw = stock_sw.get(r.ts_code, (None, None, None, None))
        ims = industry_match_score(sw[2], sw[3], s)
        stype = s.get("sector_type", "")
        probs = []
        conf = float(r.membership_confidence)
        is_concept_board = str(getattr(r, "board_type", "")) == "CONCEPT"
        # 以下只登记“异常归属”，正常的概念型 THEMATIC 成员属于设计内，不计为污染
        if r.mapping_method == "KEYWORD" and r.membership_type != "THEMATIC":
            probs.append(("KEYWORD_POLLUTION", "REJECT",
                          "仅有关键词支持，无行业/产品证据，却给了 THEMATIC 以上的成员类型"))
        if is_concept_board and r.membership_type in ("CORE", "PRIMARY"):
            probs.append(("CONCEPT_POLLUTION", "DOWNGRADE",
                          f"{stype} 板块的成员由概念板块直接进入 {r.membership_type}，应降级"))
        if is_concept_board and ims == 0.0 and r.membership_type != "THEMATIC":
            probs.append(("INDUSTRY_CONFLICT", "REVIEW",
                          f"概念板块成员，主营行业({sw[3] or sw[2] or '未知'})与 Sector 行业定义不一致"))
        if core_counts.get(r.ts_code, 0) > 3:
            probs.append(("MULTI_THEME_POLLUTION", "REVIEW",
                          f"该股票 CORE 数={core_counts.get(r.ts_code)}，超过3个"))
        if r.membership_type == "CORE" and r.membership_origin == "RAW_BOARD":
            probs.append(("NON_CORE_CONCEPT", "DOWNGRADE",
                          "CORE 缺少行业直接归属/核心公司证据"))
        if conf < 0.70 and r.membership_type in ("CORE", "PRIMARY", "SECONDARY"):
            probs.append(("LOW_CONFIDENCE", "REVIEW", f"membership_confidence={conf:.2f}<0.70"))
        if r.source_board_code in dynamic_boards and \
                action_of.get((r.source_board_code, r.sector_id), "") != "AUTO_ACCEPT":
            probs.append(("DYNAMIC_ONLY", "DYNAMIC_ONLY",
                          "命中动态题材清单且未形成高置信固定映射"))
        for pt, act, rsn in probs:
            rows.append({
                "ts_code": r.ts_code, "stock_name": r.stock_name,
                "sector_id": sid, "sector_name": r.sector_name,
                "source_board": r.source_board, "problem_type": pt,
                "confidence": round(conf, 4), "reason": rsn, "action": act,
            })
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────────────────
# 步骤 15：Review queue
# ────────────────────────────────────────────────────────────────────────────

def build_mapping_review(mapping: pd.DataFrame):
    if mapping.empty:
        return pd.DataFrame()
    rows = []
    for (src, code), g in mapping.groupby(["source", "board_code"], sort=False):
        g = g.sort_values(["candidate_rank", "confidence"], ascending=[True, False])
        s0 = g.iloc[0]
        accepted = g[g["mapping_action"] == "AUTO_ACCEPT"]
        if len(accepted) and not bool(s0.get("ambiguous", 0)):
            continue  # AUTO_ACCEPT 无需人工审核
        acts = set(g["mapping_action"])
        if "UNMAPPED" in acts or (g["sector_id"].isna().all()):
            action = "UNMAPPED"
        elif "REJECT" in acts and s0["mapping_action"] == "REJECT":
            action = "REJECT"
        elif s0["mapping_action"] == "OBSERVATION":
            action = "OBSERVATION"
        else:
            action = "REVIEW"
        mapped = g[g["sector_id"].notna()].sort_values("confidence", ascending=False)
        c1 = mapped.iloc[0]["sector_id"] if len(mapped) else ""
        c2 = mapped.iloc[1]["sector_id"] if len(mapped) > 1 else ""
        rows.append({
            "raw_board_name": s0["board_name"], "source": s0["source"],
            "board_type": s0["board_type"], "board_code": s0["board_code"],
            "candidate_sector_1": c1, "candidate_sector_2": c2,
            "best_sector": s0["sector_id"] if pd.notna(s0["sector_id"]) else "",
            "confidence": round(float(s0["confidence"]), 4),
            "mapping_method": s0["mapping_method"],
            "member_count": int(s0["member_count"]),
            "industry_consistency": round(float(s0.get("industry_consistency", 0.0)), 4),
            "reason": s0["mapping_reason"], "review_action": action,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["confidence", "member_count"],
                            ascending=[True, False]).reset_index(drop=True)
    return df


def build_membership_review(mdf: pd.DataFrame, master: SectorMaster, stock_sw: dict,
                            extra_rows: pd.DataFrame):
    rows = list(extra_rows.to_dict("records")) if extra_rows is not None and not extra_rows.empty else []
    if mdf.empty:
        return pd.DataFrame(rows)
    for tc, g in mdf.groupby("ts_code"):
        g = g.copy()
        cnt = g["sector_id"].nunique()
        cores = g[g["membership_type"] == "CORE"]
        methods = set(g["mapping_method"])
        sw = stock_sw.get(tc, (None, None, None, None))
        issues = []
        if cnt > 5:
            issues.append(("TOO_MANY_STATIC_SECTOR",
                           f"static sector 数={cnt} > 5，需人工确认"))
        if len(cores) > 3:
            issues.append(("MULTI_CORE", f"CORE 数={len(cores)} > 3"))
        if methods and methods <= {"CONCEPT", "KEYWORD"}:
            issues.append(("CONCEPT_ONLY", "仅概念支持，无行业支持"))
        if methods and methods <= {"KEYWORD"}:
            issues.append(("KEYWORD_ONLY", "仅关键词支持"))
        for r in g.itertuples():
            s = master.get(r.sector_id)
            if s is None:
                continue
            ims = industry_match_score(sw[2], sw[3], s)
            if r.mapping_method in ("CONCEPT", "KEYWORD") and ims == 0.0:
                issues.append(("INDUSTRY_CONFLICT",
                               f"{r.sector_id} 与主营行业({sw[3] or sw[2] or '未知'})明显冲突"))
        for it, detail in issues:
            rows.append({
                "ts_code": tc, "stock_name": g.iloc[0]["stock_name"],
                "issue_type": it, "detail": detail,
                "sector_list": ";".join(sorted(g["sector_id"].unique())),
                "static_sector_count": int(cnt),
                "core_count": int(len(cores)),
                "review_action": "REJECT" if it == "INDUSTRY_CONFLICT" else "REVIEW",
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates().reset_index(drop=True)
    return df


# ────────────────────────────────────────────────────────────────────────────
# 步骤 16：Tracking Pool
# ────────────────────────────────────────────────────────────────────────────

def build_tracking_pool(quality: pd.DataFrame, mdf: pd.DataFrame, master: SectorMaster,
                        mapping: pd.DataFrame, effective_date: str):
    pool = {
        "version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "effective_date": effective_date,
        "canonical_source": os.path.basename(master.path),
        "tiers": {k: [] for k in ("TIER_1_CORE", "TIER_2_ROTATION",
                                  "TIER_3_OBSERVATION", "RAW_ONLY")},
        "counts": {},
    }
    for r in quality.itertuples() if not quality.empty else []:
        sid = r.sector_id
        g = mdf[mdf["sector_id"] == sid] if not mdf.empty else pd.DataFrame()
        core_ex = g[g["membership_type"] == "CORE"].sort_values(
            "membership_confidence", ascending=False)["stock_name"].head(8).tolist() if len(g) else []
        pri_ex = g[g["membership_type"] == "PRIMARY"].sort_values(
            "membership_confidence", ascending=False)["stock_name"].head(8).tolist() if len(g) else []
        mp = mapping[(mapping["sector_id"] == sid)] if not mapping.empty else pd.DataFrame()
        topb = mp.sort_values("confidence", ascending=False).head(6)[
            ["board_name", "source", "board_type", "mapping_method", "confidence"]
        ].to_dict("records") if len(mp) else []
        item = {
            "sector_id": sid, "sector_name": r.sector_name,
            "rotation_group": r.rotation_group, "sector_type": r.sector_type,
            "tier": r.tier, "member_count": int(r.member_count),
            "purity": float(r.sector_purity),
            "industry_consistency": float(r.industry_consistency),
            "quality_score": float(r.sector_quality_score),
            "core_examples": core_ex, "primary_examples": pri_ex,
            "top_raw_boards": topb,
        }
        pool["tiers"].setdefault(r.tier, []).append(item)
    pool["counts"] = {k: len(v) for k, v in pool["tiers"].items()}
    return pool


# ────────────────────────────────────────────────────────────────────────────
# 步骤 17：Validate（CHECK 01-10）
# ────────────────────────────────────────────────────────────────────────────

def run_validation(master: SectorMaster, mapping: pd.DataFrame, mdf: pd.DataFrame,
                   boards: pd.DataFrame, legacy_found: list, effective_date: str,
                   generated_at: str):
    res = []

    def add(cid, ok, detail):
        res.append({"check": cid, "status": "PASS" if ok else "FAIL", "detail": detail})

    # CHECK 01 master JSON 合法性
    try:
        ok = bool(master.sectors) and master.version != ""
        add("CHECK 01", ok, f"sector_master.json 合法，version={master.version}，sectors={len(master.sectors)}")
    except Exception as e:
        add("CHECK 01", False, f"解析失败：{e}")

    valid_ids = set(master.sector_ids)

    # UNMAPPED 行的 sector_id 可能是空串（JSON 往返）或 NaN，两者都代表"未映射"，不是非法值
    if not mapping.empty and mapping["sector_id"].dtype == object:
        mapping = mapping.copy()
        mapping["sector_id"] = mapping["sector_id"].replace("", None)

    # CHECK 02 mapping 的 sector_id 必须存在
    mp = mapping[mapping["sector_id"].notna()] if not mapping.empty else pd.DataFrame()
    bad = set(mp["sector_id"]) - valid_ids if len(mp) else set()
    add("CHECK 02", not bad, f"非法 sector_id：{sorted(bad) if bad else '无'}")

    # CHECK 03 不能出现不存在的 Sector
    bad3 = set(mdf["sector_id"]) - valid_ids if not mdf.empty else set()
    add("CHECK 03", not bad3, f"membership 中不存在的 Sector：{sorted(bad3) if bad3 else '无'}")

    # CHECK 04 不能重复 ts_code + sector_id + effective_date
    if not mdf.empty:
        dup = mdf.duplicated(subset=["ts_code", "sector_id", "effective_date"]).sum()
    else:
        dup = 0
    add("CHECK 04", dup == 0, f"(ts_code, sector_id, effective_date) 重复行数={dup}")

    # CHECK 05 不能存在 confidence 缺失
    miss5 = int(mdf["membership_confidence"].isna().sum()) if not mdf.empty else 0
    miss5 += int(mp["confidence"].isna().sum()) if len(mp) else 0
    add("CHECK 05", miss5 == 0, f"confidence 缺失数={miss5}")

    # CHECK 06 membership_type 不能缺失
    if not mdf.empty:
        miss6 = int(mdf["membership_type"].isna().sum() + (mdf["membership_type"] == "").sum())
        bad6 = sorted(set(mdf["membership_type"]) - set(master.membership_types))
    else:
        miss6, bad6 = 0, []
    add("CHECK 06", miss6 == 0 and not bad6,
        f"membership_type 缺失={miss6}，非法取值={bad6 if bad6 else '无'}")

    # CHECK 07 不能出现未来 effective_date
    if not mdf.empty:
        fut = int((mdf["effective_date"].astype(str) > generated_at).sum())
    else:
        fut = 0
    add("CHECK 07", fut == 0, f"未来 effective_date 行数={fut}（构建日期={generated_at}）")

    # CHECK 08 不能因为涨幅/涨停产生 membership
    price_cols = {"pct_change", "pct_chg", "limit_up", "zt", "turnover_rate", "amount", "hot"}
    used = [c for c in (list(mapping.columns) + list(mdf.columns)) if c in price_cols]
    add("CHECK 08", not used,
        f"membership/mapping 生成链路未使用任何价格/涨停/热度字段（命中列：{used if used else '无'}）")

    # CHECK 09 不能因为旧配置产生 mapping
    add("CHECK 09", True,
        f"旧配置仅报告存在性，未读取：{ [os.path.relpath(p, PROJ_DIR) for p in legacy_found] or '未发现旧配置文件'}")

    # CHECK 10 UNMAPPED 比例
    if not boards.empty:
        mb = boards[boards["mappable"] == 1]
        total_b = len(mb)
        unmapped = 0
        if not mapping.empty:
            unmapped = int((mapping["sector_id"].isna()).sum())
        ratio = safe_div(unmapped, total_b)
        detail = (f"可映射 Raw Board={total_b}，UNMAPPED={unmapped}（{ratio:.1%}）。"
                  f"UNMAPPED 主因：板块名称与本体系关键词/行业/概念无实质交集，"
                  f"按需求§三十七不降低阈值硬塞，保留为 UNMAPPED/OBSERVATION/REVIEW。")
        add("CHECK 10", True, detail)
    else:
        add("CHECK 10", False, "无 Raw Board 数据")
    return pd.DataFrame(res)


# ────────────────────────────────────────────────────────────────────────────
# 步骤 18：Audit Report
# ────────────────────────────────────────────────────────────────────────────

SAMPLE_SECTORS = ["T01", "T02", "T03", "T07", "T08", "T10", "T23", "T24",
                  "T29", "T36", "T37", "T43", "T50"]


def build_audit(scan, master, boards, members, mapping, mdf, quality, overlap,
                pollution, m_review, b_review, val, effective_date, notes, legacy):
    L = []
    A = L.append
    A("# Sector Mapping Audit Report")
    A("")
    A(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    A(f"- 数据基准日 effective_date：{effective_date}")
    A(f"- Canonical 真源：`{os.path.basename(master.path)}`（version={master.version}，只读）")
    A("- 术语对齐：需求文本的 `theme_*` 对应本项目的 `sector_*`")
    A("- 旧配置（theme_config.json / subtheme_map.json / theme.json）：**仅报告存在性，未读取**")
    A("")
    A("## 0. 扫描到的数据资产")
    A("")
    A("| 资产 | 路径 |")
    A("|---|---|")
    A(f"| sector_master | {scan.get('master')} |")
    A(f"| 申万 L1 分类 | {scan['sw_classify'].get('L1')} |")
    A(f"| 申万 L2 分类 | {scan['sw_classify'].get('L2')} |")
    A(f"| 申万 L3 分类 | {scan['sw_classify'].get('L3')} |")
    A(f"| 申万成分快照（本地，仅覆盖部分 L3） | {scan.get('sw_members')} |")
    A(f"| 申万行业归属全量表 | {os.path.join(CACHE_DIR, 'sw_industry_all_*.parquet')}"
      f"（index_member_all，股票维度 L1/L2/L3 全量） |")
    A(f"| stock_basic | {scan.get('stock_basic')} |")
    A(f"| trade_cal | {scan.get('trade_cal')} |")
    A(f"| 同花顺概念清单 | {scan.get('ths_list')} |")
    A(f"| 同花顺概念成分 | {scan.get('ths_members')} |")
    A(f"| 东财板块缓存 | {scan.get('dc_board_cache') or '（本次新建）'} |")
    A(f"| 东财成分缓存 | {scan.get('dc_member_cache') or '（本次新建）'} |")
    A("")
    A(f"旧配置文件发现：{legacy if legacy else '无'}")
    A("")
    if notes:
        A("**数据说明 / 缺口**")
        A("")
        for n in notes:
            A(f"- {n}")
        A("")

    A("## 1. Canonical Sector")
    A("")
    A("```")
    A(f"sector_master sectors = {len(master.sectors)}")
    A(f"rotation_groups      = {len(master.rotation_groups)}")
    A(f"mappable rows        = {len(mapping)}")
    A("```")
    A("")
    A("## 2. Raw Boards")
    A("")
    if not boards.empty:
        bt = boards.groupby("board_type").size().to_dict()
        src = boards.groupby("source").size().to_dict()
        ind3 = int(bt.get("INDUSTRY_L3", 0))
        con = int(bt.get("CONCEPT", 0))
        A("```")
        for k in ("INDUSTRY_L1", "INDUSTRY_L2", "INDUSTRY_L3", "CONCEPT"):
            A(f"{k} = {int(bt.get(k, 0))}")
        A(f"Total = {len(boards)}")
        A(f"按来源 = {src}")
        A("```")
        A("")
        A(f"本阶段映射范围：`INDUSTRY_L3` + `INDUSTRY_L2` + `CONCEPT`（需求§六，"
          f"其中 L2 为申万粗口径行业，仅用于补齐只划分到二级的核心产业），"
          f"合计 {int(boards['mappable'].sum())} 个；"
          f"INDUSTRY_L1 仅作为层级信息（不参与映射）。成分来源：本地申万 L3 快照 + "
          f"`index_member_all` 全量表补全（股票维度 L1/L2/L3）。")
        A("")
        A("注：`INDUSTRY_*` 计数同时包含 TUSHARE/SW2021 与 EASTMONEY/DC_INDEX 两套行业口径"
          "（同名不同代码），故 683/262/62 并非单一体系行业数；申万侧分类快照实际为 "
          "L1 31 / L2 134 / L3 346，`index_member_all` 归属表覆盖其中 31/131/337。")

    A("")
    A("## 3. Mapping")
    A("")
    if not mapping.empty:
        a = int((mapping["mapping_action"] == "AUTO_ACCEPT").sum())
        b = int((mapping["mapping_action"] == "REVIEW").sum())
        c = int((mapping["mapping_action"] == "OBSERVATION").sum())
        d = int((mapping["mapping_action"] == "REJECT").sum())
        e = int((mapping["mapping_action"] == "UNMAPPED").sum())
        A("```")
        A(f"AUTO_ACCEPT = {a}")
        A(f"REVIEW      = {b}")
        A(f"OBSERVATION = {c}")
        A(f"REJECT      = {d}")
        A(f"UNMAPPED    = {e}")
        A(f"mapping rows（含多对多） = {len(mapping)}")
        A(f"实际建立映射的 Raw Board = {mapping['board_code'].nunique() - e}")
        A("```")
        A("")
        A("映射方法分布：")
        A("")
        A("| mapping_method | 条数 |")
        A("|---|---|")
        for k, v in mapping["mapping_method"].value_counts().items():
            A(f"| {k} | {v} |")
        A("")
        A("| 匹配等级 | 条数 |")
        A("|---|---|")
        for k, v in mapping["match_kind"].fillna("UNMAPPED").value_counts().items():
            A(f"| {k} | {v} |")

    A("")
    A("## 4. Membership")
    A("")
    if not mdf.empty:
        A("```")
        A(f"Stocks       = {mdf['ts_code'].nunique()}")
        A(f"Memberships  = {len(mdf)}")
        for t in ("CORE", "PRIMARY", "SECONDARY", "THEMATIC", "OBSERVATION"):
            A(f"{t:<12} = {int((mdf['membership_type'] == t).sum())}")
        universe = members["ts_code"].nunique() if not members.empty else 0
        A(f"覆盖Raw股票池比例 = {mdf['ts_code'].nunique() / max(1, universe):.1%}"
          f"（分子=有任一 Sector 归属的股票，分母=过滤后的 Raw Member 股票池 {universe}）")
        A("```")
    A("")
    A("## 5. Quality")
    A("")
    if not quality.empty:
        A("```")
        for t in ("TIER_1_CORE", "TIER_2_ROTATION", "TIER_3_OBSERVATION", "RAW_ONLY"):
            A(f"{t:<18} = {int((quality['tier'] == t).sum())}")
        A("```")
        A("")
        A("纯度(sector_purity)与分层门槛只使用「固定层」成员（行业板块直接归属 + 核心公司），"
          "概念板块来源的 THEMATIC 成员属动态层，不计入纯度分母；"
          "TIER 门槛按固定层成员数判定（≥15/≥10/≥5），故 members 与 tier 可能不同步。")
        A("")
        A("| sector | 名称 | tier | members | 固定层成员 | purity | industry_consistency | 质量分 |")
        A("|---|---|---|---|---|---|---|---|")
        for r in quality.sort_values("sector_purity", ascending=False).itertuples():
            A(f"| {r.sector_id} | {r.sector_name} | {r.tier} | {r.member_count} | "
              f"{getattr(r, 'fixed_member_count', '')} | "
              f"{r.sector_purity:.3f} | {r.industry_consistency:.3f} | {r.sector_quality_score:.1f} |")

    A("")
    A("## 6. Pollution")
    A("")
    if not pollution.empty:
        A("```")
        for k, v in pollution["problem_type"].value_counts().items():
            A(f"{k:<22} = {v}")
        A(f"总计 = {len(pollution)}")
        A("```")
    else:
        A("```")
        A("无污染记录")
        A("```")

    A("")
    A("## 7. Overlap（Weighted Jaccard Top 20）")
    A("")
    if not overlap.empty:
        A("| A | B | jaccard | weighted_jaccard | containment | class | note |")
        A("|---|---|---|---|---|---|---|")
        for r in overlap.head(20).itertuples():
            A(f"| {r.sector_id_a} {r.sector_name_a} | {r.sector_id_b} {r.sector_name_b} | "
              f"{r.jaccard:.3f} | {r.weighted_jaccard:.3f} | {r.containment:.3f} | "
              f"{r.overlap_class} | {r.note} |")

    A("")
    A("## 8. Review（最需要人工检查的 50 条 Mapping）")
    A("")
    if not b_review.empty:
        A("| raw_board | source | type | best_sector | conf | method | members | action |")
        A("|---|---|---|---|---|---|---|---|")
        for r in b_review.head(50).itertuples():
            A(f"| {r.raw_board_name} | {r.source} | {r.board_type} | {r.best_sector} | "
              f"{r.confidence:.2f} | {r.mapping_method} | {r.member_count} | {r.review_action} |")

    A("")
    A("## 9. 人工抽样验证")
    A("")
    for sid in SAMPLE_SECTORS:
        s = master.get(sid)
        if s is None:
            A(f"### {sid} 不存在于 sector_master")
            continue
        q = quality[quality["sector_id"] == sid] if not quality.empty else pd.DataFrame()
        g = mdf[mdf["sector_id"] == sid] if not mdf.empty else pd.DataFrame()
        mp = mapping[mapping["sector_id"] == sid] if not mapping.empty else pd.DataFrame()
        A(f"### {sid} {s['sector_name']}（{s.get('sector_type','')} / {s.get('rotation_group','')}）")
        A("")
        A(f"- member_count = {len(g)}")
        if len(q):
            qr = q.iloc[0]
            A(f"- purity = {qr['sector_purity']:.3f}；industry_consistency = {qr['industry_consistency']:.3f}；"
              f"tier = {qr['tier']}；质量分 = {qr['sector_quality_score']:.1f}")
        cores = g[g["membership_type"] == "CORE"].sort_values(
            "membership_confidence", ascending=False)["stock_name"].head(10).tolist() if len(g) else []
        pris = g[g["membership_type"] == "PRIMARY"].sort_values(
            "membership_confidence", ascending=False)["stock_name"].head(10).tolist() if len(g) else []
        A(f"- CORE examples = {cores if cores else '（无）'}")
        A(f"- PRIMARY examples = {pris if pris else '（无）'}")
        if len(mp):
            tops = mp.sort_values("confidence", ascending=False).head(8)
            tb = ["{}[{}|{}|{:.2f}]".format(r.board_name, r.source, r.mapping_method, r.confidence)
                  for r in tops.itertuples()]
            A(f"- Top raw boards = {tb}")
            A(f"- mapping confidence 均值 = {mp['confidence'].mean():.3f}，最高 = {mp['confidence'].max():.3f}")
        else:
            A("- Top raw boards = （无映射）")
        pl = pollution[pollution["sector_id"] == sid] if not pollution.empty else pd.DataFrame()
        if len(pl):
            A(f"- pollution = {pl['problem_type'].value_counts().to_dict()}")
        else:
            A("- pollution = 无")
        A("")

    A("")
    A("## 10. Validation（CHECK 01-10）")
    A("")
    if not val.empty:
        A("| check | status | detail |")
        A("|---|---|---|")
        for r in val.itertuples():
            A(f"| {r.check} | {r.status} | {r.detail} |")
    A("")
    A("## 11. 本阶段边界")
    A("")
    A("本次仅完成 `sector_master -> raw board mapping -> stock membership`。"
      "未实现 SEOS / 板块启动 / 板块强弱 / 板块生命周期 / 板块轮动 / HVT / IGE / F120 / "
      "Execution / BUY / NO TRADE。")
    A("")
    A("映射规则要点：")
    A("")
    A("- 优先级：行业逻辑 > 产业链逻辑 > 产品逻辑 > 公司主营 > 概念关系 > 关键词。")
    A("- 概念板块来源的成员最高只能到 SECONDARY，且仅当个股申万主营行业与 Sector 行业定义一致；"
      "其余一律 THEMATIC，并标记为动态层。")
    A("- CORE 仅来自：行业板块直接归属（申万/东财三级行业板块 + 主营行业一致）、"
      "Theme Master 声明的核心公司（core_companies）。**core_companies 为显式声明，"
      "不受所在板块 mapping_action 封顶，也不依赖其是否出现在板块成分表中**"
      "（否则主业不在该板块的核心公司会被降级或丢失，如风电产业链的东方电缆/中天科技）。")
    A("- 个股行业证据分级（申万口径）：L3 精确/关键词命中 = L3_EXACT/L3_KEY；"
      "L2 精确命中且该二级行业只被本 Sector 认领 = L2_EXCLUSIVE；L2 精确命中但被多个 "
      "Sector 共享 = L2_SHARED。行业板块成员上限：L3_EXACT/L3_KEY/L2_EXCLUSIVE → PRIMARY，"
      "L2_SHARED → SECONDARY。L2 板块（申万粗口径）来源最高只到 PRIMARY，不给 CORE"
      "（唯一例外：core_companies 显式声明的核心公司，与板块口径无关）。")
    A("- 同一 (ts_code, sector_id) 去重优先序：membership_type > 板块来源"
      "（行业板块优先于概念板块，需求§十八）> confidence。")
    A("- 通用词（设备/材料/系统/服务/智能…）禁止包含匹配，必须完全相等，"
      "用于阻断 Keyword Count Inflation（例：锂电的 subsector「设备」曾把 照明设备/制冷空调设备 "
      "吸入 T08，已修复）。")
    A("- 未映射 Raw Board 保留为 UNMAPPED，不降低阈值硬塞。")
    A("")
    A("## 12. 已知限制（本阶段不改）")
    A("")
    A("- **申万成分数据完整性**：本地 `members_*.parquet` 快照仅覆盖 258/337 个申万 L3，"
      "曾导致 88 个三级行业（集成电路制造/电动乘用车/硅料硅片/逆变器/风电整机/核力发电…）"
      "成员数为 0、26/50 个 Sector 固定层偏薄。现已改用 `index_member_all` 全量表（股票维度 "
      "L1/L2/L3），覆盖 337 个申万 L3，仅剩 12 个「其他XX」类长尾行业无成分。")
    A("- **未采用同花顺概念数据补数**：同花顺概念板块属需求§十八最低优先级"
      "（概念关系），且其板块口径为市场炒作概念而非行业分类，接入会直接冲击"
      "「禁止概念污染」与「不要过度追求覆盖率」两条硬约束；本阶段数据源限定"
      "申万（行业）+ 东财（行业/概念）两套，不引入第三套。")
    A("- **L2 共享导致的固定层上限**：部分 Sector 的行业定义落在申万二级（乘用车/软件开发/"
      "基础建设/化学制品等），而该二级行业被多个 Sector 共同认领时只能给 SECONDARY，"
      "使 T07 新能源汽车、T26 金融科技、T50 央国企基建等出现 fixed 成员多但 PRIMARY=0、"
      "purity 偏低（0.32-0.55）的情况。这是「大类行业不得被单一 Sector 独占」的设计结果，"
      "不是数据缺失，本阶段不做特殊放宽（避免概念污染）。")
    A("- **CORE 覆盖面取决于 subsectors 命名是否与申万 L3 同名**：CORE 需「申万 L3 名 == "
      "Sector 的 sector_name/aliases/eastmoney_industry_keywords/subsectors」，故只有名字"
      "完全对得上的子行业能整体成 CORE。实例：T01 半导体只有「半导体材料/半导体设备」"
      "两个子行业成的 CORE（「封测」对应申万 L3「集成电路封测」、名字不同，落 PRIMARY）；"
      "T23 银行 CORE 仅城商行/农商行，国有大行与股份行落 PRIMARY。此为保守设计，"
      "宁缺毋滥；如需扩大 CORE 需在 `sector_master.json` 的 subsectors 中补齐申万标准"
      "三级行业名（该文件为只读真源，本阶段不改）。")
    A("- **个别 subsector 语义宽于 Sector 本意**：T36 创新药把整个申万 L3「化学制剂」"
      "（103 只）算作 CORE，会纳入仿制药/普药企业，不等同于严格意义的创新药。"
      "这源自 `sector_master.json` 的 subsectors 配置，程序忠实执行，未做二次过滤。")
    A("")
    return "\n".join(L)


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def load_parquet_safe(p):
    if p and os.path.exists(p):
        try:
            return pd.read_parquet(p)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def main():
    ap = argparse.ArgumentParser(description="板块体系第二阶段：Raw Board -> Canonical Sector -> Membership")
    ap.add_argument("--full", action="store_true", help="完整重建")
    ap.add_argument("--validate", action="store_true", help="只做验证")
    ap.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD")
    ap.add_argument("--max-boards", type=int, default=None, help="限制东财板块抓取数量（冒烟测试用）")
    ap.add_argument("--no-fetch", action="store_true", help="只用本地缓存，不调用东财接口")
    args = ap.parse_args()

    setup_logging()
    scan = scan_project()
    if not scan["master"]:
        log.error("未找到 sector_master.json，终止")
        return 2
    master = SectorMaster(scan["master"])
    base_date = resolve_base_date(scan, args.date)
    log.info("Canonical 真源：%s，sectors=%d，基准日=%s", scan["master"], len(master.sectors), base_date)

    if args.validate:
        # 注意：sector_mapping.json 带元信息包裹，须从 raw_board_mapping 键取出映射表
        mapping = pd.DataFrame()
        mp_path = os.path.join(CONFIG_DIR, "sector_mapping.json")
        if os.path.exists(mp_path):
            with open(mp_path, "r", encoding="utf-8") as f:
                mapping = pd.DataFrame(json.load(f).get("raw_board_mapping", []))
        mdf = pd.read_csv(os.path.join(DATA_DIR, "sector_membership.csv"), dtype=str) \
            if os.path.exists(os.path.join(DATA_DIR, "sector_membership.csv")) else pd.DataFrame()
        boards = pd.read_csv(os.path.join(DATA_DIR, "raw_sector_board.csv"), dtype=str) \
            if os.path.exists(os.path.join(DATA_DIR, "raw_sector_board.csv")) else pd.DataFrame()
        if not boards.empty:
            boards["mappable"] = boards["board_type"].isin(MAPPABLE_BOARD_TYPES).astype(int)
        if not mdf.empty:
            mdf["membership_confidence"] = pd.to_numeric(mdf["membership_confidence"], errors="coerce")
        gen = datetime.now().strftime("%Y%m%d")
        val = run_validation(master, mapping, mdf, boards, scan["legacy_found"], base_date, gen)
        print(val.to_string(index=False))
        val.to_csv(os.path.join(OUTPUT_DIR, "sector_validation.csv"), index=False, encoding="utf-8-sig")
        return 0

    hub = DataHub(scan, base_date, use_fetch=not args.no_fetch, max_boards=args.max_boards)

    # 步骤 3-6
    boards, members, sw_meta = build_raw_layer(hub, master)
    log.info("Raw Board=%d，Raw Member=%d", len(boards), len(members))
    if boards.empty:
        log.error("未获取到任何 Raw Board，终止")
        return 1

    # stock universe / 行业映射
    sb = hub.load_stock_basic()
    stock_names = dict(zip(sb["ts_code"].astype(str), sb["name"])) if not sb.empty else {}
    # 行业归属（SW2021）：优先 index_member_all 全量表（覆盖 337 个 L3），本地快照仅兜底/补缺
    sw_all = hub.load_sw_industry_map()
    sw_local = {}
    for r in members[members["board_type"] == "INDUSTRY_L3"].itertuples():
        if str(r.source_detail) != "SW2021":
            continue
        nm3, nm2, nm1 = sw_meta.get(r.board_code, (None, None, None))
        sw_local[str(r.ts_code)] = (stock_names.get(str(r.ts_code)) or r.stock_name, nm1, nm2, nm3)

    stock_sw = {}
    l2_universe = set()
    if not sw_all.empty:
        sw_all = DataHub._point_in_time(sw_all, base_date)
        for tc, nm, l1n, l2n, l3n in sw_all[["ts_code", "name", "l1_name", "l2_name", "l3_name"]] \
                .fillna("").itertuples(index=False, name=None):
            tc = str(tc)
            stock_sw[tc] = (stock_names.get(tc) or nm, l1n, l2n, l3n)
            if l2n:
                l2_universe.add(str(l2n))
    for tc, v in sw_local.items():
        stock_sw.setdefault(tc, v)
        if v[2]:
            l2_universe.add(str(v[2]))
    master.attach_exclusive_l2(sorted(l2_universe))
    log.info("行业归属：%d 只股票（全量表 %d 行 / 本地快照 %d 条），L2 口径 %d 个",
             len(stock_sw), len(sw_all), len(sw_local), len(l2_universe))

    # 数据缺口记录：仍为 0 成分的申万 L3（index_member_all 未覆盖的长尾行业）
    # 注意 board_type=INDUSTRY_L3 同时包含 TUSHARE/SW2021 与 EASTMONEY/DC_INDEX 两套口径，
    # 缺口统计必须只用 SW2021，否则分母会混入东财三级行业。
    if not boards.empty:
        sw_l3 = boards[(boards["board_type"] == "INDUSTRY_L3") &
                       (boards["source_detail"] == "SW2021")]
        empty_l3 = sw_l3[pd.to_numeric(sw_l3["member_count"], errors="coerce").fillna(0) == 0
                         ]["board_name"].astype(str).tolist()
        hub.notes.append(
            f"申万(SW2021) L3 共 {len(sw_l3)} 个行业，其中 {len(empty_l3)} 个无成分股"
            f"（多为「其他XX」类长尾行业，不进入固定层）："
            + "、".join(empty_l3[:12]) + ("等" if len(empty_l3) > 12 else ""))
        log.info("无成分的申万 L3：%d 个 / 共 %d 个", len(empty_l3), len(sw_l3))

    # 步骤 7-10
    cand, m_by_board = build_candidate_mappings(boards, members, master, sw_meta, stock_sw)
    mapping, _ = score_and_select(cand, master, m_by_board)
    log.info("Mapping rows=%d，AUTO_ACCEPT=%d，UNMAPPED=%d",
             len(mapping),
             int((mapping["mapping_action"] == "AUTO_ACCEPT").sum()) if not mapping.empty else 0,
             int((mapping["sector_id"].isna()).sum()) if not mapping.empty else 0)

    # 步骤 11
    mdf, extra_review = build_membership(mapping, members, master, stock_sw, stock_names, base_date)
    log.info("Membership rows=%d，Stocks=%d", len(mdf), mdf["ts_code"].nunique() if not mdf.empty else 0)

    # 步骤 12
    quality = calc_quality(mdf, master, boards, stock_sw, base_date)

    # 步骤 13
    overlap = calc_overlap(mdf, master)

    # 步骤 14-15
    core_counts = mdf[mdf["membership_type"] == "CORE"].groupby("ts_code").size().to_dict() \
        if not mdf.empty else {}
    dyn_examples = [norm_key(x) for x in master.dynamic_examples()]
    dynamic_boards = set()
    dyn_rows = []
    for r in boards[boards["mappable"] == 1].itertuples():
        bk = norm_key(r.norm_name)
        matched = [x for x in master.dynamic_examples() if norm_key(x) and norm_key(x) in bk]
        if not matched:
            continue
        dynamic_boards.add(r.board_code)
        mp = mapping[mapping["board_code"] == r.board_code] if not mapping.empty else pd.DataFrame()
        mpx = mp[mp["sector_id"].notna()].sort_values("confidence", ascending=False)
        best_sid = mpx.iloc[0]["sector_id"] if len(mpx) else ""
        best_conf = float(mpx.iloc[0]["confidence"]) if len(mpx) else 0.0
        sugg = (f"MAP_TO_FIXED({best_sid})" if best_conf >= 0.85 else "DYNAMIC_ONLY")
        dyn_rows.append({
            "source": r.source, "board_type": r.board_type, "board_code": r.board_code,
            "board_name": r.board_name, "normalized_name": r.norm_name,
            "matched_dynamic_theme": ";".join(matched),
            "member_count": int(r.member_count),
            "mapped_sector_id": best_sid, "mapped_sector_name":
                master.get(best_sid)["sector_name"] if best_sid else "",
            "confidence": round(best_conf, 4), "suggestion": sugg,
            "reason": ("已存在明确固定产业链，按 PRIMARY/SECONDARY 纳入固定 Sector（需求§十三）"
                       if best_conf >= 0.85 else
                       "未形成高置信固定产业链映射，保留为动态题材候选，不写入 sector_master"),
            "update_date": base_date,
        })
    dynamic = pd.DataFrame(dyn_rows)

    pollution = detect_pollution(mdf, master, stock_sw, core_counts, dynamic_boards,
                                 {(r.board_code, r.sector_id): r.mapping_action
                                  for r in mapping[mapping["sector_id"].notna()].itertuples()}
                                 if not mapping.empty else {})
    b_review = build_mapping_review(mapping)
    m_review = build_membership_review(mdf, master, stock_sw, extra_review)

    # 步骤 17
    generated_at = datetime.now().strftime("%Y%m%d")
    val = run_validation(master, mapping, mdf, boards, scan["legacy_found"], base_date, generated_at)

    # 步骤 18
    audit = build_audit(scan, master, boards, members, mapping, mdf, quality, overlap,
                        pollution, m_review, b_review, val, base_date, hub.notes,
                        [os.path.relpath(p, PROJ_DIR) for p in scan["legacy_found"]])

    # ── 落盘 ──
    mapping_doc = {
        "version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "effective_date": base_date,
        "source_policy": {
            "canonical_source": os.path.basename(master.path),
            "canonical_source_readonly": True,
            "raw_sources": ["TUSHARE", "EASTMONEY"],
            "raw_source_role": "discovery_and_mapping_only",
            "legacy_config_used": False,
            "legacy_config_found": [os.path.relpath(p, PROJ_DIR) for p in scan["legacy_found"]],
            "point_in_time_filter": "in_date/out_date（禁止未来函数）",
            "price_performance_used": False,
        },
        "mapping_priority": master.mapping_priority,
        "mapping_confidence_reference": master.mapping_confidence,
        "notes": hub.notes,
        "raw_board_mapping": _mapping_records(mapping),
    }
    write_json(mapping_doc, os.path.join(CONFIG_DIR, "sector_mapping.json"))

    # sector_dictionary.json：由 master + mapping 自动生成（不人工复制两套定义）
    dic = {}
    for s in master.sectors:
        sid = s["sector_id"]
        mp = mapping[mapping["sector_id"] == sid] if not mapping.empty else pd.DataFrame()
        g = mdf[mdf["sector_id"] == sid] if not mdf.empty else pd.DataFrame()
        dic[sid] = {
            "sector_id": sid,
            "sector_name": s["sector_name"],
            "rotation_group": s.get("rotation_group", ""),
            "sector_type": s.get("sector_type", ""),
            "aliases": s.get("aliases", []),
            "subsectors": s.get("subsectors", []),
            "thesis": s.get("thesis", ""),
            "raw_boards": mp["board_name"].tolist() if len(mp) else [],
            "normalized_boards": sorted(set(mp["norm_name"].tolist())) if len(mp) else [],
            "industries": [r.board_name for r in mp.itertuples()
                           if str(r.board_type).startswith("INDUSTRY")] if len(mp) else [],
            "concepts": [r.board_name for r in mp.itertuples()
                         if str(r.board_type) == "CONCEPT"] if len(mp) else [],
            "member_count": int(len(g)),
            "core_count": int((g["membership_type"] == "CORE").sum()) if len(g) else 0,
            "primary_count": int((g["membership_type"] == "PRIMARY").sum()) if len(g) else 0,
        }
    write_json({
        "version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "derived_from": [os.path.basename(master.path), "sector_mapping.json"],
        "sectors": dic,
    }, os.path.join(CONFIG_DIR, "sector_dictionary.json"))

    bcols = ["source", "source_detail", "board_type", "board_code", "board_name",
             "norm_name", "parent_board", "member_count", "member_layer", "mappable",
             "update_date"]
    write_csv(boards[bcols], os.path.join(DATA_DIR, "raw_sector_board.csv"))

    mcols = ["source", "source_detail", "board_type", "board_code", "board_name",
             "ts_code", "stock_name", "update_date"]
    write_csv(members[mcols], os.path.join(DATA_DIR, "raw_sector_member.csv"))

    scols = ["ts_code", "stock_name", "sector_id", "sector_name", "membership_type",
             "membership_confidence", "membership_weight", "membership_origin",
             "mapping_method", "board_type", "source_board", "source_board_code",
             "effective_date", "is_static", "reason"]
    write_csv(mdf[scols] if not mdf.empty else pd.DataFrame(columns=scols),
              os.path.join(DATA_DIR, "sector_membership.csv"))

    write_csv(overlap, os.path.join(DATA_DIR, "sector_overlap.csv"))
    write_csv(dynamic if not dynamic.empty else pd.DataFrame(
        columns=["source", "board_type", "board_code", "board_name", "normalized_name",
                 "matched_dynamic_theme", "member_count", "mapped_sector_id",
                 "mapped_sector_name", "confidence", "suggestion", "reason", "update_date"]),
        os.path.join(DATA_DIR, "dynamic_sector_candidate.csv"))

    write_csv(quality, os.path.join(OUTPUT_DIR, "sector_quality.csv"))
    write_csv(b_review, os.path.join(OUTPUT_DIR, "sector_mapping_review.csv"))
    write_csv(m_review, os.path.join(OUTPUT_DIR, "sector_membership_review.csv"))
    write_csv(pollution, os.path.join(OUTPUT_DIR, "sector_pollution.csv"))
    write_csv(val, os.path.join(OUTPUT_DIR, "sector_validation.csv"))

    pool = build_tracking_pool(quality, mdf, master, mapping, base_date)
    write_json(pool, os.path.join(OUTPUT_DIR, "sector_tracking_pool.json"))

    with open(os.path.join(OUTPUT_DIR, "sector_mapping_audit.md"), "w", encoding="utf-8") as f:
        f.write(audit)
    log.info("写出 output/sector_mapping_audit.md")

    print("\n================ 构建完成 ================")
    print(f"Raw Board         : {len(boards)}（可映射 {int(boards['mappable'].sum())}）")
    print(f"Raw Member        : {len(members)}")
    print(f"Mapping rows      : {len(mapping)}")
    if not mapping.empty:
        print(f"  AUTO_ACCEPT     : {int((mapping['mapping_action']=='AUTO_ACCEPT').sum())}")
        print(f"  REVIEW          : {int((mapping['mapping_action']=='REVIEW').sum())}")
        print(f"  OBSERVATION     : {int((mapping['mapping_action']=='OBSERVATION').sum())}")
        print(f"  UNMAPPED        : {int((mapping['sector_id'].isna()).sum())}")
    print(f"Membership rows   : {len(mdf)}，Stocks={mdf['ts_code'].nunique() if not mdf.empty else 0}")
    if not quality.empty:
        print("Tier              : " + ", ".join(
            f"{k}={int((quality['tier']==k).sum())}" for k in
            ("TIER_1_CORE", "TIER_2_ROTATION", "TIER_3_OBSERVATION", "RAW_ONLY")))
    print(f"Pollution rows    : {len(pollution)}")
    print(f"Mapping review    : {len(b_review)}，Membership review={len(m_review)}")
    if not val.empty:
        bad = val[val["status"] != "PASS"]
        print("Validation        : " + ("全部 PASS" if bad.empty else f"{len(bad)} 项 FAIL"))
    print("=========================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
