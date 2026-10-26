# -*- coding: utf-8 -*-
"""
选股记录与跟踪数据库 (stock_pick_db)
================================================
全项目统一的"选股信号落库 + 后续表现跟踪"模块。

数据流:
  1. 各策略每日收盘后调用 record_picks() 写入选股结果 (幂等, 重复运行自动覆盖)
  2. 每日盘后运行 update_tracking() 回填跟踪收益 (基于共享行情库 daily_cache 前复权数据)
  3. 通过 v_pick_full / get_picks() / get_strategy_stats() 查看明细与策略胜率

表结构:
  strategy       策略注册表 (strategy_id, strategy_name, category, horizon ...)
  stock_pick     选股记录主表, 唯一键 (pick_date, strategy_id, ts_code)
                 标准列: close / pct_chg / signal / action / score / rank_no /
                         industry / reason / stop_price / target_price
                 策略自有字段自动序列化进 indicators JSON 列
  pick_tracking  跟踪表, 每个信号一行: ret_1d/3d/5d/10d/20d, max_gain,
                 max_drawdown, status(ACTIVE/STOP_HIT/TARGET_HIT/EXPIRED)
  v_pick_full    stock_pick LEFT JOIN pick_tracking 明细视图
  v_strategy_daily 每日每策略选股数量统计视图

跟踪口径:
  收益基准 = 选股日收盘价 (close); 第 N 日 = pick_date 之后的第 N 个交易日;
  max_gain = 选股后最高价相对基准的最大涨幅; max_drawdown = 选股后最低价
  相对基准的最大回撤; 满 20 个交易日自动置 EXPIRED。

策略接入示例 (bts):
    from stock_pick_db import record_picks
    from dataclasses import asdict
    record_picks('bts', '趋势启动系统',
                 [asdict(r) for r in results],            # dataclass 直接转 dict
                 field_map={'buy_point': 'action', 'grade': 'signal'})

策略接入示例 (普通 dict):
    record_picks('my_strategy', '我的策略', [
        {'ts_code': '000001.SZ', 'stock_name': '平安银行', 'close': 12.34,
         'pct_chg': 1.5, 'signal': 'pullback_buy', 'action': '低吸',
         'score': 86, 'industry': '银行',
         'indicators': {'ma20': 12.1, 'vol_ratio': 1.8}},   # 自有指标放这里
    ])

命令行:
    python stock_pick_db.py init        # 建库建表
    python stock_pick_db.py tracking    # 盘后回填跟踪收益
    python stock_pick_db.py stats       # 各策略胜率统计
    python stock_pick_db.py demo        # 演示全链路 (临时库, 不污染正式库)
"""
import os
import re
import sys
import json
import shutil
import sqlite3
import argparse
import tempfile
from datetime import date, timedelta
from contextlib import contextmanager
from dataclasses import is_dataclass, asdict

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('PICK_DB_PATH', os.path.join(BASE_DIR, 'picks_db', 'stock_picks.db'))
MARKET_DB_PATH = os.environ.get('PICK_MARKET_DB', r"D:\mystock\cache_daily\stock_data.db")

HORIZONS = (1, 3, 5, 10, 20)
TRACK_WINDOW = 20
CONCEPT_TAG = '走势最相关'   # 腾讯板块快照里标记"走势最相关"的概念
# 腾讯概念里混着大量「交易属性/风格/指数」标签，不是行业或题材，直接排除
CONCEPT_SKIP = re.compile(
    r'股通|融资融券|转融券|转融通|融券标的|同股同权|龙头|大盘股|中盘股|小盘股|'
    r'高价股|低价股|微利股|白马股|茅指数|MSCI|标普|富时|上证50|沪深\d|中证\d|深成|'
    r'AH股|B股|次新股|涨停|高振幅|股权激励|高送转|纳入|央企央资|政府控股|'
    r'双百企业|出海\d|独角兽|分拆上市|中科院系|高校|国企改革|中特估|专精特新|创投|顺周期|周期股'
)
CONCEPT_HOT = re.compile(r'涨停\s*(\d+)')   # tag 形如「涨停45」= 板块内涨停家数，用作热度
# 大而全的宽泛板块：几乎什么股都能挂，热度常年靠前，容易盖过真正的细分题材
CONCEPT_BROAD = re.compile(
    r'人工智能|大数据|云计算|TMT|自主可控|信创|国产软件|数字经济|AIGC|DeepSeek|'
    r'机器人概念|华为概念|消费电子产业|半导体产业|新能源车|大消费|国企改革|中特估|专精特新|'
    r'芯片概念|无人驾驶|车联网|智能汽车|激光雷达|电子后视镜|液冷概念|数据中心|5G概念'
)
# 他人概念：挂在别家公司/巨头供应链下的标签，不如自身细分题材贴切
CONCEPT_OTHER = re.compile(
    r'英伟达|谷歌|华为|苹果|特斯拉|小米|阿里|腾讯|百度|字节|抖音|快手|小红书|'
    r'蔚来|宁德时代|比亚迪|宇树科技|OpenAI|AMD|英特尔|台积电|三星|Meta|微软'
)
CONCEPT_WEAK_FACTOR = 0.4   # 宽泛/他人概念的涨幅折算系数，压住它们对细分题材的干扰
_FX_CLIENT = None           # FinchX 惰性单例（补所属概念时初始化）

STD_COLS = frozenset({
    'pick_date', 'strategy_id', 'ts_code', 'stock_name', 'close', 'pct_chg',
    'signal', 'action', 'score', 'rank_no', 'industry', 'reason',
    'stop_price', 'target_price', 'indicators', 'details',
})
ALIAS_COLS = {'name': 'stock_name', 'stock': 'ts_code', 'code': 'ts_code'}

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy (
    strategy_id   TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    category      TEXT DEFAULT '',
    horizon       TEXT DEFAULT 'short',
    description   TEXT DEFAULT '',
    enabled       INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stock_pick (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_date     TEXT NOT NULL,
    strategy_id   TEXT NOT NULL,
    ts_code       TEXT NOT NULL,
    stock_name    TEXT DEFAULT '',
    close         REAL,
    pct_chg       REAL,
    signal        TEXT DEFAULT '',
    action        TEXT DEFAULT '',
    score         REAL,
    rank_no       INTEGER,
    industry      TEXT DEFAULT '',
    concept       TEXT DEFAULT '',
    reason        TEXT DEFAULT '',
    stop_price    REAL,
    target_price  REAL,
    indicators    TEXT DEFAULT '{}',
    details       TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (pick_date, strategy_id, ts_code)
);
CREATE INDEX IF NOT EXISTS ix_pick_date ON stock_pick (pick_date);
CREATE INDEX IF NOT EXISTS ix_pick_strategy ON stock_pick (strategy_id, pick_date);
CREATE INDEX IF NOT EXISTS ix_pick_code ON stock_pick (ts_code);

CREATE TABLE IF NOT EXISTS pick_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_date       TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    ts_code         TEXT NOT NULL,
    base_close      REAL,
    last_date       TEXT,
    last_close      REAL,
    ret_1d          REAL,
    ret_3d          REAL,
    ret_5d          REAL,
    ret_10d         REAL,
    ret_20d         REAL,
    max_high        REAL,
    max_low         REAL,
    max_gain        REAL,
    max_drawdown    REAL,
    status          TEXT DEFAULT 'ACTIVE',
    hit_date        TEXT,
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE (pick_date, strategy_id, ts_code)
);
CREATE INDEX IF NOT EXISTS ix_track_status ON pick_tracking (status);

DROP VIEW IF EXISTS v_pick_full;
CREATE VIEW v_pick_full AS
SELECT p.pick_date, p.strategy_id, s.strategy_name, p.ts_code, p.stock_name, p.close, p.pct_chg,
       p.signal, p.action, p.score, p.rank_no, p.industry, p.concept, p.reason,
       p.stop_price, p.target_price, p.indicators, p.created_at,
       t.base_close, t.last_date, t.last_close,
       t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
       t.max_gain, t.max_drawdown, t.status, t.hit_date
FROM stock_pick p
LEFT JOIN strategy s ON s.strategy_id = p.strategy_id
LEFT JOIN pick_tracking t
  ON t.pick_date = p.pick_date AND t.strategy_id = p.strategy_id
 AND t.ts_code = p.ts_code;

DROP VIEW IF EXISTS v_strategy_daily;
CREATE VIEW v_strategy_daily AS
SELECT p.pick_date, p.strategy_id, s.strategy_name, COUNT(*) AS pick_cnt,
       AVG(p.score) AS avg_score, AVG(p.pct_chg) AS avg_day_chg
FROM stock_pick p
LEFT JOIN strategy s ON s.strategy_id = p.strategy_id
GROUP BY p.pick_date, p.strategy_id;
"""


def set_db_path(path):
    global DB_PATH
    DB_PATH = path


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.execute('PRAGMA journal_mode = WAL')
    except Exception:
        pass
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn):
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_pick)")}
    if cols and 'concept' not in cols:
        conn.execute("ALTER TABLE stock_pick ADD COLUMN concept TEXT DEFAULT ''")


def init_db(path=None):
    if path:
        set_db_path(path)
    with get_conn() as conn:
        _ensure_columns(conn)
        conn.executescript(SCHEMA)
    return DB_PATH


def upsert_strategy(strategy_id, strategy_name='', category='', horizon='short',
                    description='', enabled=1):
    if not strategy_name:
        strategy_name = strategy_id
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO strategy (strategy_id, strategy_name, category, horizon,
                                  description, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(strategy_id) DO UPDATE SET
                strategy_name = excluded.strategy_name,
                category = excluded.category,
                horizon = excluded.horizon,
                description = excluded.description,
                enabled = excluded.enabled,
                updated_at = datetime('now','localtime')
        """, (strategy_id, strategy_name, category, horizon, description, enabled))


def _to_dict(item):
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, '__dict__'):
        return dict(vars(item))
    raise TypeError(f'unsupported pick type: {type(item)}')


def record_picks(strategy_id, strategy_name='', picks=(), pick_date=None, field_map=None,
                 replace=False):
    """写入选股结果（幂等 upsert）。

    replace=True（20261006 新增，默认 False 保持既有行为）：写入后，把该
      (pick_date, strategy_id) 下「本次未出现」的旧记录从 stock_pick 及派生的
      pick_tracking 中删除。用于当日信号池会缩小的场景（如 VSW 下蹲池新增剔除门槛后），
      避免被剔除标的仍残留在库/网页。默认 False 时不删任何旧行。
      20261014：replace=True 且 picks 为空时，清空该 (pick_date, strategy_id) 全部旧记录
      （replace 语义 = 当日池完整替换为本次结果，包括空）。
    """
    if pick_date is None:
        pick_date = date.today().strftime('%Y%m%d')
    field_map = dict(field_map or {})
    init_db()
    if strategy_name:
        upsert_strategy(strategy_id, strategy_name)
    rows = []
    for item in picks:
        d = _to_dict(item)
        for src, dst in ALIAS_COLS.items():
            if src in d and dst not in d:
                d[dst] = d.pop(src)
        for src, dst in field_map.items():
            if src in d and dst not in d:
                d[dst] = d[src]
        extra = {k: v for k, v in d.items() if k not in STD_COLS}
        row = (
            pick_date, strategy_id,
            str(d.get('ts_code', '') or ''),
            str(d.get('stock_name', '') or ''),
            _f(d.get('close')), _f(d.get('pct_chg')),
            str(d.get('signal', '') or ''),
            str(d.get('action', '') or ''),
            _f(d.get('score')), _i(d.get('rank_no')),
            str(d.get('industry', '') or ''),
            str(d.get('reason', '') or ''),
            _f(d.get('stop_price')), _f(d.get('target_price')),
            json.dumps(extra, ensure_ascii=False, default=str),
            json.dumps(d['details'], ensure_ascii=False, default=str) if 'details' in d else None,
        )
        if not row[2]:
            continue
        rows.append(row)
    if not rows:
        if replace:
            # replace=True 且本次为空：清空该 (pick_date, strategy_id) 全部旧记录。
            # 20261014：当日信号池被质量门收缩为 0 的重跑场景，旧行同样须清除
            # （replace 语义 = 当日池完整替换为本次结果，包括空）。
            with get_conn() as conn:
                for tbl in ("stock_pick", "pick_tracking"):
                    conn.execute("DELETE FROM %s WHERE pick_date=? AND strategy_id=?"
                                 % tbl, [pick_date, strategy_id])
        return 0
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO stock_pick (pick_date, strategy_id, ts_code, stock_name,
                                    close, pct_chg, signal, action, score, rank_no,
                                    industry, reason, stop_price, target_price,
                                    indicators, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pick_date, strategy_id, ts_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                close = excluded.close,
                pct_chg = excluded.pct_chg,
                signal = excluded.signal,
                action = excluded.action,
                score = excluded.score,
                rank_no = excluded.rank_no,
                industry = excluded.industry,
                reason = excluded.reason,
                stop_price = excluded.stop_price,
                target_price = excluded.target_price,
                indicators = excluded.indicators,
                details = excluded.details
        """, rows)
        if replace:
            # 清理本次未出现的旧记录（stock_pick + 派生的 pick_tracking）
            codes = [r[2] for r in rows if r[2]]
            if codes:
                ph = ",".join("?" * len(codes))
                for tbl in ("stock_pick", "pick_tracking"):
                    conn.execute(
                        "DELETE FROM %s WHERE pick_date=? AND strategy_id=? "
                        "AND ts_code NOT IN (%s)" % (tbl, ph),
                        [pick_date, strategy_id] + codes)
    return len(rows)


def _f(v):
    try:
        return None if v is None or v == '' else float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return None if v is None or v == '' else int(v)
    except (TypeError, ValueError):
        return None


def _load_bars(ts_codes, start_date, end_date):
    if not os.path.exists(MARKET_DB_PATH):
        return {}
    out = {}
    conn = sqlite3.connect(MARKET_DB_PATH, timeout=30.0)
    try:
        conn.execute('PRAGMA busy_timeout = 30000')
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close FROM daily_cache "
            "WHERE trade_date >= ? AND trade_date <= ? AND close > 0",
            conn, params=(start_date, end_date))
    finally:
        conn.close()
    if df.empty:
        return {}
    codes = set(ts_codes)
    for code, g in df[df['ts_code'].isin(codes)].groupby('ts_code'):
        out[code] = g.sort_values('trade_date').reset_index(drop=True)
    return out


def _fx():
    """惰性初始化 FinchX：仅补所属概念时需要，避免模块级硬依赖。"""
    global _FX_CLIENT
    if _FX_CLIENT is None:
        from finchx import FinchX
        _FX_CLIENT = FinchX()
    return _FX_CLIENT


def _sector_rows(ts_code):
    """取个股所属板块，归一化为 (类型, 名称, tag, 涨跌幅%)。

    兼容 finchx 两套接口：新版 market.instrument_sector_snapshot 与
    3.1.x 的 sector.tx_sectors（腾讯 plateNew 同一数据源）。
    """
    fx = _fx()
    bare = str(ts_code).split('.')[0]
    snap = getattr(fx.market, 'instrument_sector_snapshot', None)
    if snap is not None:
        data = snap(bare).to_dicts()
        sectors = (data[0].get('sectors') or []) if data else []
        out = []
        for s in sectors:
            pct = _f(s.get('changePct'))
            out.append((s.get('sectorType'), s.get('sectorName'), s.get('tag'),
                        None if pct is None else pct * 100))
        return out
    out = []
    for s in fx.sector.tx_sectors(bare):
        out.append((s.get('sector_type'), s.get('sector_name'), s.get('tag'),
                    _f(s.get('change_percent'))))
    return out


def _pick_concept(ts_code):
    """腾讯板块快照中取所属概念：先剔除属性类标签，再在细分题材里取当日最热。

    宽泛大板块（人工智能/大数据等）打半折且不计涨停加成，避免盖过细分题材。
    取不到合适的返回空串（宁可不显示，也不要「沪股通」这类噪声）。
    """
    try:
        sectors = _sector_rows(ts_code)
    except Exception:
        return ''
    concepts = [(name, (tag or '').strip(), pct) for stype, name, tag, pct in sectors
                if stype == 'concept' and name and not CONCEPT_SKIP.search(name)]
    if not concepts:
        return ''
    # 腾讯自己标了「走势最相关」就直接用它
    for name, tag, _pct in concepts:
        if tag == CONCEPT_TAG:
            return name

    def heat(item):
        name, tag, pct = item
        pct = pct or 0.0
        if CONCEPT_BROAD.search(name) or CONCEPT_OTHER.search(name):
            return pct * CONCEPT_WEAK_FACTOR
        m = CONCEPT_HOT.search(tag)
        return pct + (int(m.group(1)) * 0.1 if m else 0.0)
    return max(concepts, key=heat)[0]


def _fill_concepts(codes):
    """给空概念或存量为噪声概念的选股重新补齐（同代码保留首次结果）。"""
    if not codes:
        return 0
    updates = []
    for code in codes:
        name = _pick_concept(code)
        if name:
            updates.append((name, code))
    if updates:
        with get_conn() as conn:
            conn.executemany("UPDATE stock_pick SET concept = ? WHERE ts_code = ?", updates)
    return len(updates)


def _calc_tracking(bars, pick_date, base_close, stop_price, target_price):
    if bars is None or bars.empty or not base_close:
        return None
    after = bars[bars['trade_date'] > pick_date].reset_index(drop=True)
    n = len(after)
    if n == 0:
        return None
    closes = after['close'].astype(float).values
    highs = after['high'].astype(float).values
    lows = after['low'].astype(float).values

    def ret_at(k):
        return round((closes[k - 1] / base_close - 1) * 100, 2) if n >= k else None

    max_high = float(highs.max()) if n else None
    max_low = float(lows.min()) if n else None
    max_gain = round((max_high / base_close - 1) * 100, 2) if max_high else None
    max_drawdown = round((1 - max_low / base_close) * 100, 2) if max_low else None

    status, hit_date = 'ACTIVE', None
    dates = after['trade_date'].values
    if stop_price:
        hit = after[after['low'].astype(float) <= stop_price]
        if len(hit):
            status, hit_date = 'STOP_HIT', str(hit['trade_date'].iloc[0])
    if status != 'STOP_HIT' and target_price:
        hit = after[after['high'].astype(float) >= target_price]
        if len(hit):
            status, hit_date = 'TARGET_HIT', str(hit['trade_date'].iloc[0])
    if status == 'ACTIVE' and n >= TRACK_WINDOW:
        status = 'EXPIRED'

    return {
        'base_close': base_close,
        'last_date': str(dates[-1]),
        'last_close': round(float(closes[-1]), 4),
        'ret_1d': ret_at(1), 'ret_3d': ret_at(3), 'ret_5d': ret_at(5),
        'ret_10d': ret_at(10), 'ret_20d': ret_at(20),
        'max_high': max_high, 'max_low': max_low,
        'max_gain': max_gain, 'max_drawdown': max_drawdown,
        'status': status, 'hit_date': hit_date,
    }


def update_tracking(lookback_days=120):
    window_start = (date.today() - timedelta(days=lookback_days)).strftime('%Y%m%d')
    with get_conn() as conn:
        _ensure_columns(conn)
        rows = conn.execute("""
            SELECT p.pick_date, p.strategy_id, p.ts_code, p.close,
                   p.stop_price, p.target_price, t.status
            FROM stock_pick p
            LEFT JOIN pick_tracking t
              ON t.pick_date = p.pick_date AND t.strategy_id = p.strategy_id
             AND t.ts_code = p.ts_code
            WHERE p.pick_date >= ?
        """, (window_start,)).fetchall()
        pairs = conn.execute(
            "SELECT DISTINCT ts_code, concept FROM stock_pick WHERE pick_date >= ?",
            (window_start,)).fetchall()
    pending = sorted({code for code, concept in pairs
                      if not concept or CONCEPT_SKIP.search(concept)})
    tasks = [r for r in rows if r[6] != 'EXPIRED']

    # 所属概念：腾讯板块快照（剔除属性类标签，取当日最热的概念）
    start = (date.today() - timedelta(days=12)).strftime('%Y%m%d')
    if tasks:
        start = min(start, min(r[0] for r in tasks))
    end = date.today().strftime('%Y%m%d')
    bars_map = _load_bars({r[2] for r in tasks} | set(pending), start, end)
    filled = _fill_concepts(pending)
    if filled:
        print(f'[所属概念] 已补 {filled} 只')

    if not tasks:
        return 0

    updates = []
    for pick_date, strategy_id, ts_code, close, stop_price, target_price, _ in tasks:
        tr = _calc_tracking(bars_map.get(ts_code), pick_date, _f(close),
                            _f(stop_price), _f(target_price))
        if tr is None:
            continue
        updates.append((
            pick_date, strategy_id, ts_code,
            tr['base_close'], tr['last_date'], tr['last_close'],
            tr['ret_1d'], tr['ret_3d'], tr['ret_5d'], tr['ret_10d'], tr['ret_20d'],
            tr['max_high'], tr['max_low'], tr['max_gain'], tr['max_drawdown'],
            tr['status'], tr['hit_date'],
        ))
    if updates:
        with get_conn() as conn:
            conn.executemany("""
                INSERT INTO pick_tracking (pick_date, strategy_id, ts_code, base_close,
                                           last_date, last_close, ret_1d, ret_3d, ret_5d,
                                           ret_10d, ret_20d, max_high, max_low,
                                           max_gain, max_drawdown, status, hit_date,
                                           updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        datetime('now','localtime'))
                ON CONFLICT(pick_date, strategy_id, ts_code) DO UPDATE SET
                    base_close = excluded.base_close,
                    last_date = excluded.last_date,
                    last_close = excluded.last_close,
                    ret_1d = excluded.ret_1d, ret_3d = excluded.ret_3d,
                    ret_5d = excluded.ret_5d, ret_10d = excluded.ret_10d,
                    ret_20d = excluded.ret_20d,
                    max_high = excluded.max_high, max_low = excluded.max_low,
                    max_gain = excluded.max_gain, max_drawdown = excluded.max_drawdown,
                    status = excluded.status, hit_date = excluded.hit_date,
                    updated_at = datetime('now','localtime')
            """, updates)
    return len(updates)


def get_picks(pick_date=None, strategy_id=None, ts_code=None, status=None,
              start_date=None, end_date=None, limit=200):
    conds, params = [], []
    if pick_date:
        conds.append('pick_date = ?'); params.append(pick_date)
    if start_date:
        conds.append('pick_date >= ?'); params.append(start_date)
    if end_date:
        conds.append('pick_date <= ?'); params.append(end_date)
    if strategy_id:
        conds.append('strategy_id = ?'); params.append(strategy_id)
    if ts_code:
        conds.append('ts_code = ?'); params.append(ts_code)
    if status:
        conds.append('status = ?'); params.append(status)
    where = f"WHERE {' AND '.join(conds)}" if conds else ''
    sql = f"SELECT * FROM v_pick_full {where} ORDER BY pick_date DESC, strategy_id, score DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_strategy_stats(start_date=None, end_date=None):
    conds, params = [], []
    if start_date:
        conds.append('pick_date >= ?'); params.append(start_date)
    if end_date:
        conds.append('pick_date <= ?'); params.append(end_date)
    where = f"WHERE {' AND '.join(conds)}" if conds else ''
    sql = f"SELECT * FROM v_pick_full {where}"
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return df
    recs = []
    for sid, g in df.groupby('strategy_id'):
        name = str(g['strategy_name'].iloc[0]) if 'strategy_name' in g.columns else ''
        rec = {'strategy_id': sid, 'strategy_name': name, 'total': len(g)}
        for h in HORIZONS:
            col = f'ret_{h}d'
            valid = g[g[col].notna()]
            rec[f'{col}_n'] = len(valid)
            rec[f'{col}_win'] = round((valid[col] > 0).mean() * 100, 1) if len(valid) else None
            rec[f'{col}_avg'] = round(valid[col].mean(), 2) if len(valid) else None
        rec['avg_max_gain'] = round(g['max_gain'].mean(), 2) if g['max_gain'].notna().any() else None
        rec['avg_max_dd'] = round(g['max_drawdown'].mean(), 2) if g['max_drawdown'].notna().any() else None
        rec['active'] = int((g['status'] == 'ACTIVE').sum())
        recs.append(rec)
    return pd.DataFrame(recs).sort_values('strategy_id').reset_index(drop=True)


def _demo():
    tmp = tempfile.mkdtemp(prefix='picks_demo_')
    set_db_path(os.path.join(tmp, 'demo_picks.db'))
    init_db()
    mconn = sqlite3.connect(MARKET_DB_PATH, timeout=30.0)
    try:
        last_date = mconn.execute(
            "SELECT MAX(trade_date) FROM daily_cache WHERE close > 0").fetchone()[0]
        dates = [r[0] for r in mconn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 6", [last_date]).fetchall()]
        pick_date = dates[-1]
        demo_codes = ['000001.SZ', '600519.SH', '300404.SZ']
        names = {'000001.SZ': '平安银行', '600519.SH': '贵州茅台', '300404.SZ': '博济医药'}
        bars = mconn.execute(
            "SELECT ts_code, close, pct_chg FROM daily_cache WHERE trade_date = ? "
            "AND ts_code IN (?, ?, ?)", [pick_date] + demo_codes).fetchall()
    finally:
        mconn.close()
    picks = [{'ts_code': c, 'stock_name': names.get(c, c), 'close': cl, 'pct_chg': pc,
              'signal': 'demo_signal', 'action': '演示-观望', 'score': 60 + i * 10,
              'rank_no': i + 1, 'ma20': round(cl * 0.95, 2), 'vol_ratio': 1.5 + i * 0.2}
             for i, (c, cl, pc) in enumerate(bars)]
    n = record_picks('demo', '演示策略', picks, pick_date=pick_date)
    print(f"[demo] 已写入 {n} 条演示选股 (pick_date={pick_date}, 临时库)")
    print(f"[demo] 回填跟踪: {update_tracking()} 条")
    print('\n== v_pick_full 明细 ==')
    df = get_picks(strategy_id='demo')
    cols = ['ts_code', 'stock_name', 'close', 'signal', 'action', 'score',
            'ret_1d', 'ret_3d', 'ret_5d', 'max_gain', 'max_drawdown', 'status']
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))
    print('\n== 策略胜率统计 ==')
    st = get_strategy_stats()
    print(st.to_string(index=False))
    set_db_path(os.environ.get('PICK_DB_PATH',
                os.path.join(BASE_DIR, 'picks_db', 'stock_picks.db')))
    shutil.rmtree(tmp, ignore_errors=True)
    print('\n[demo] 临时库已清理')


def main():
    parser = argparse.ArgumentParser(description='选股记录与跟踪数据库')
    parser.add_argument('cmd', choices=['init', 'tracking', 'stats', 'demo'],
                        help='init=建库 tracking=回填跟踪 stats=胜率统计 demo=演示')
    parser.add_argument('--days', type=int, default=30, help='stats 统计最近 N 天')
    args = parser.parse_args()
    if args.cmd == 'init':
        print(f'已建库: {init_db()}')
    elif args.cmd == 'tracking':
        n = update_tracking()
        print(f'已回填跟踪 {n} 条')
    elif args.cmd == 'stats':
        start = (date.today() - timedelta(days=args.days)).strftime('%Y%m%d')
        st = get_strategy_stats(start_date=start)
        if st.empty:
            print('暂无数据')
        else:
            print(st.to_string(index=False))
    elif args.cmd == 'demo':
        _demo()


if __name__ == '__main__':
    main()
