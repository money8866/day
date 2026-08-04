# -*- coding: utf-8 -*-
"""
猎尾V3 - 股票角色识别引擎
==========================
将主题内股票分为四类角色:
A. 龙头 Leader    - 主题涨幅排名Top3 + 辨识度高 + 涨停历史 + 成交额靠前
B. 中军 Core      - 市值较大 + 成交额排名主题Top10 + 趋势稳定
C. 跟风 Follow    - 主题相关但无资金优势
D. 弱关联 Weak    - 概念弱 + 涨幅独立

评分: Leader=20, Core=12, Follow=0, Weak=-5

纯函数模块,无外部依赖,可独立用于回测和盘中模式。
"""


def detect_stock_role(ts_code, theme_name, theme_stocks, quotes, turnover_cache=None,
                      kline_cache=None, stock_mv=None, zt_first_time=None):
    """
    识别股票在主题中的角色并评分

    参数:
        ts_code:         股票代码
        theme_name:      所属主题名称
        theme_stocks:    主题成份股 {theme_name: [(code, name, layer), ...]}
        quotes:          行情数据 {ts_code: {price, pct_chg, vol, ...}}
        turnover_cache:  换手率缓存 {ts_code: float} (可选)
        kline_cache:     K线缓存 {ts_code: DataFrame} (可选)
        stock_mv:        市值缓存 {ts_code: float} (可选, 万元)
        zt_first_time:   涨停首次封板时间 {ts_code: str} (可选)

    返回:
        (role: str, role_score: int, detail: dict)
        role: 'leader' | 'core' | 'follow' | 'weak'
    """
    detail = {
        'theme': theme_name,
        'role_score': 0,
        'rank_in_theme': 0,
        'amount_rank_in_theme': 0,
        'is_leader': False,
        'is_core': False,
    }

    # 获取主题内所有股票的行情数据
    theme_members = theme_stocks.get(theme_name, [])
    if not theme_members:
        return 'weak', -5, detail

    # 获取当前股票行情
    q = quotes.get(ts_code)
    if not q or q.get('price', 0) <= 0:
        return 'weak', -5, detail

    pct = q.get('pct_chg', 0)
    amount = q.get('amount', 0)
    vol = q.get('vol', 0)

    # ── 计算主题内涨幅排名 ──
    rank_data = []
    for code, name, layer in theme_members:
        mq = quotes.get(code)
        if mq and mq.get('price', 0) > 0:
            mpct = mq.get('pct_chg', 0)
            mamount = mq.get('amount', 0)
            rank_data.append((code, name, layer, mpct, mamount))
    rank_data.sort(key=lambda x: -x[3])  # 按涨幅降序

    # 涨幅排名
    pct_rank = 0
    for i, (code, _, _, _, _) in enumerate(rank_data, 1):
        if code == ts_code:
            pct_rank = i
            break
    detail['rank_in_theme'] = pct_rank

    # 成交额排名
    amount_rank_data = sorted(rank_data, key=lambda x: -x[4])
    amount_rank = 0
    for i, (code, _, _, _, _) in enumerate(amount_rank_data, 1):
        if code == ts_code:
            amount_rank = i
            break
    detail['amount_rank_in_theme'] = amount_rank

    # ── 角色判断 ──
    # A. 龙头 Leader: 涨幅排名Top3 + 成交额主题内靠前
    if pct_rank <= 3 and pct_rank > 0 and pct > 0:
        # 进一步验证: 涨停历史或成交额Top10
        is_zt = False
        if zt_first_time and ts_code in zt_first_time:
            is_zt = True
        if not is_zt and kline_cache:
            # 检查最近是否有涨停
            kl = kline_cache.get(ts_code)
            if kl is not None and len(kl) >= 5:
                recent_pcts = kl['pct_chg'].tail(5).values
                for rp in recent_pcts:
                    limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
                    if float(rp) >= limit:
                        is_zt = True
                        break

        has_amount = amount_rank <= 10 and amount_rank > 0
        if is_zt or has_amount:
            detail['is_leader'] = True
            detail['role_score'] = 20
            return 'leader', 20, detail

    # B. 中军 Core: 市值较大 + 成交额排名Top10 + 非弱关联
    if stock_mv and ts_code in stock_mv:
        mv = stock_mv[ts_code]
        is_large_cap = mv > 1000000  # 100亿以上
    else:
        is_large_cap = False

    has_amount_top10 = amount_rank <= 10 and amount_rank > 0
    if (is_large_cap or has_amount_top10) and pct_rank <= 10:
        detail['is_core'] = True
        detail['role_score'] = 12
        return 'core', 12, detail

    # C. 跟风 Follow: 主题内非前10
    if pct_rank <= len(rank_data) and pct_rank > 0:
        return 'follow', 0, detail

    # D. 弱关联 Weak: 概念弱/涨幅独立
    return 'weak', -5, detail


def calc_stock_role_score_from_layer(layer, ts_code, theme_name, theme_stocks, quotes,
                                     turnover_cache=None, kline_cache=None,
                                     stock_mv=None, zt_first_time=None):
    """
    基于已有layer信息, 结合实时数据计算角色评分

    与 detect_stock_role 不同, 此函数优先使用已有的layer标记,
    但会结合实时数据做微调(如跟风股涨幅跃居Top3, 可临时升级)

    参数与 detect_stock_role 相同

    返回:
        (role: str, role_score: int, detail: dict)
    """
    # 如果layer已经是leader/middle, 用实时数据验证
    if layer == 'leader':
        detail = {'role': 'leader', 'based_on': 'config'}
        # 验证: 当前涨幅是否仍是主题Top3
        theme_members = theme_stocks.get(theme_name, [])
        if theme_members:
            q = quotes.get(ts_code)
            if q and q.get('price', 0) > 0:
                pct = q.get('pct_chg', 0)
                pcts = []
                for code, _, _ in theme_members:
                    mq = quotes.get(code)
                    if mq and mq.get('price', 0) > 0:
                        pcts.append((code, mq.get('pct_chg', 0)))
                pcts.sort(key=lambda x: -x[1])
                pct_rank = sum(1 for _, p in pcts if p > pct) + 1
                if pct_rank <= 3:
                    detail['role_score'] = 20
                    detail['pct_rank'] = pct_rank
                    return 'leader', 20, detail
                else:
                    # 跌幅但仍是龙头 → 降为中军
                    detail['role_score'] = 12
                    detail['pct_rank'] = pct_rank
                    detail['note'] = f'龙头但涨幅排名{pct_rank}'
                    return 'core', 12, detail
        return 'leader', 20, detail

    elif layer == 'middle':
        detail = {'role': 'core', 'based_on': 'config'}
        # 中军: 验证涨幅是否在Top10
        theme_members = theme_stocks.get(theme_name, [])
        if theme_members:
            q = quotes.get(ts_code)
            if q and q.get('price', 0) > 0:
                pct = q.get('pct_chg', 0)
                pcts = []
                for code, _, _ in theme_members:
                    mq = quotes.get(code)
                    if mq and mq.get('price', 0) > 0:
                        pcts.append((code, mq.get('pct_chg', 0)))
                pcts.sort(key=lambda x: -x[1])
                pct_rank = sum(1 for _, p in pcts if p > pct) + 1
                detail['pct_rank'] = pct_rank
                if pct_rank <= 3:
                    # 中军涨幅跃居Top3 → 升级为龙头
                    detail['role_score'] = 20
                    detail['note'] = '中军升级为龙头'
                    return 'leader', 20, detail
                elif pct_rank <= 10:
                    detail['role_score'] = 12
                    return 'core', 12, detail
                else:
                    detail['role_score'] = 0
                    detail['note'] = f'中军但涨幅排名{pct_rank}'
                    return 'follow', 0, detail
        return 'core', 12, detail

    else:
        # follower: 用实时数据判断是否可升级
        return detect_stock_role(ts_code, theme_name, theme_stocks, quotes,
                                 turnover_cache, kline_cache, stock_mv, zt_first_time)