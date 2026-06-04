# -*- coding: utf-8 -*-
﻿# -*- coding: utf-8 -*-



import os



import sys



import time



import random



import pickle



import glob



import tushare as ts



import pandas as pd



import json



from dotenv import load_dotenv



import sqlite3











from datetime import datetime, timedelta



from concurrent.futures import (



    ThreadPoolExecutor,



    as_completed



)







import numpy as np



from collections import defaultdict







# =========================



# 鍙傛暟



# =========================



LOOKBACK = 5          # 鍔ㄩ噺绐楀彛



TOP_K = 10            # 杈撳嚭涓荤嚎鏁伴噺







MIN_STOCKS = 10       # 鏉垮潡鏈€灏忚偂绁ㄦ暟







MOMENTUM_W = 0.6



ACC_W = 0.4







##=========== TUshare







load_dotenv("config/.env")







TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")







ts.set_token(TUSHARE_TOKEN)







pro = ts.pro_api()







# ============================================



# Tushare



# ============================================











# ============================================



# 缂撳瓨鐩綍



# ============================================







BASE_DIR = os.path.dirname(os.path.abspath(__file__))



CACHE_DIR = os.path.join(BASE_DIR, "cache_daily")







DB_PATH = os.path.join(CACHE_DIR, "hot_sector.db")







os.makedirs(CACHE_DIR, exist_ok=True)











# =========================================================



# 文件璺緞



# =========================================================



CONCEPT_LIST_PATH = os.path.join(



    CACHE_DIR,



    "ths_concept_list.csv"



)







CONCEPT_DETAIL_PATH = os.path.join(



    CACHE_DIR,



    "ths_concept_detail.pkl"



)







STOCK_CONCEPT_PATH = os.path.join(



    CACHE_DIR,



    "stock_concept_map.pkl"



)







CONCEPT_STOCK_PATH = os.path.join(



    CACHE_DIR,



    "concept_stock_map.pkl"



)







# =========================================================



# 涓婚鏄犲皠锛堟浛浠ｆ蹇碉級



# =========================================================







def load_theme_map():



    """



    加载涓婚配置锛堜粠 theme.json 璇诲彇条



    濡傛灉 theme.json 姣旂紦瀛樻洿鏂帮紝鑷姩娓呴櫎鏃х紦条



    """



    theme_file = os.path.join(BASE_DIR, "theme.json")



    



    if not os.path.exists(theme_file):



        raise FileNotFoundError(f"配置涓嶅瓨条 {theme_file}")



    



    # 妫€条theme.json 鏄惁姣旂紦瀛樻洿条



    theme_mtime = os.path.getmtime(theme_file)



    



    old_cache_stock = os.path.join(CACHE_DIR, "stock_concept_map.pkl")



    old_cache_concept = os.path.join(CACHE_DIR, "concept_stock_map.pkl")



    



    # 鏃х殑闈炴棩鏈熺紦瀛樻枃浠跺鏋滃瓨鍦紝璇存槑鏄棫鏍煎紡锛岄渶瑕佸垹条



    for old_cache in [old_cache_stock, old_cache_concept]:



        if os.path.exists(old_cache):



            cache_mtime = os.path.getmtime(old_cache)



            if theme_mtime > cache_mtime:



                print(f"妫€娴嬪埌 theme.json 宸叉洿鏂帮紝娓呴櫎鏃х紦条..")



                try:



                    os.remove(old_cache)



                except:



                    pass



                # 鍚屾椂鍒犻櫎甯︽棩鏈熺殑鏃х紦条



                for date_cache in glob.glob(os.path.join(CACHE_DIR, f"{os.path.basename(old_cache).split('.')[0]}_*.pkl")):



                    try:



                        os.remove(date_cache)



                    except:



                        pass







    # 璇诲彇 theme.json



    with open(theme_file, "r", encoding="utf-8") as f:



        theme_data = json.load(f)



    



    theme_map = theme_data.get("HOT_THEMES", {})



    



    print(f"锟斤拷锟斤拷锟斤拷锟矫硷拷锟斤拷锟斤拷桑锟斤拷条{len(theme_map)} 锟斤拷锟斤拷锟斤拷")







    return theme_map











THEME_MAP = load_theme_map()











def get_last_trade_date():







    now = datetime.now()







    # =========================



    # 9鐐瑰墠锛氳涓轰笂涓€鑷劧条



    # =========================



    if now.hour < 15:







        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')







    else:







        query_date = now.strftime('%Y%m%d')







    # =========================



    # 缂撳瓨浜ゆ槗鏃ュ巻锛坰tart_date鍥哄畾20200101锛岀紦瀛樹竴娆℃案涔呬娇鐢級



    # =========================



    cal_cache = os.path.join(CACHE_DIR, "trade_cal.pkl")



    if os.path.exists(cal_cache):



        with open(cal_cache, "rb") as f:



            cal = pickle.load(f)



        if 'cal_date' in cal.columns and cal['cal_date'].max() >= query_date:



            cal = cal[cal['is_open'] == 1]



            last_trade_date = cal[cal['cal_date'] <= query_date]['cal_date'].max()



            return str(last_trade_date)







    # =========================



    # 鑾峰彇浜ゆ槗鏃ュ巻



    # =========================



    cal = pro.trade_cal(



        exchange='',



        start_date='20200101',



        end_date=query_date



    )







    with open(cal_cache, "wb") as f:



        pickle.dump(cal, f)







    # 鍙繚鐣欏紑甯傛棩



    cal = cal[cal['is_open'] == 1]







    # 鏈€杩戜氦鏄撴棩



    last_trade_date = cal[



        cal['cal_date'] <= query_date



    ]['cal_date'].max()







    return str(last_trade_date)







TRADE_DATE = get_last_trade_date()







#TRADE_DATE = "20260529" # for test



print(f"鏉垮潡鍒嗘瀽鏃ユ湡: {TRADE_DATE}")







# =========================================================



# 涓滆储鎺ュ彛锛堟浛浠ｅ悓鑺遍『鎺ュ彛条



# 鍗曟鏈€澶у彲鎷夊彇5000鏉★紝鏀寔鎸夋棩鏈熷垎条



# =========================================================



DC_INDEX_FIELDS = [



    "ts_code",



    "trade_date",



    "name",



    "leading",



    "leading_code",



    "pct_change",



    "leading_pct",



    "total_mv",



    "turnover_rate",



    "up_num",



    "down_num",



    "idx_type",



    "level"



]







DC_MEMBER_FIELDS = [



    "trade_date",



    "ts_code",



    "con_code",



    "name"



]







# 东财概念鏉垮潡鍗曟5000鏉￠檺条



DC_BATCH_SIZE = 5000











def fetch_dc_index_all(trade_date, idx_type="姒傚康鏉垮潡"):



    """



    鎷夊彇涓滆储鎸囨暟鏁版嵁锛堟条琛屼笟/鍦板煙条



    鍗曟鏈€条000鏉★紝瓒呰繃鍒欏垎椤垫媺条



    """



    cache_file = os.path.join(CACHE_DIR, f"dc_index_{trade_date}_{idx_type}.pkl")







    if os.path.exists(cache_file):



        with open(cache_file, "rb") as f:



            return pickle.load(f)







    all_rows = []



    offset = 0



    limit = DC_BATCH_SIZE







    while True:



        try:



            df = pro.dc_index(



                **{



                    "ts_code": "",



                    "name": "",



                    "trade_date": int(trade_date),



                    "start_date": "",



                    "end_date": "",



                    "idx_type": idx_type,



                    "limit": str(limit),



                    "offset": str(offset)



                },



                fields=DC_INDEX_FIELDS



            )



        except Exception as e:



            print(f"dc_index 鎷夊彇澶辫触: {e}")



            break







        if df is None or df.empty:



            break







        all_rows.append(df)







        if len(df) < limit:



            break







        offset += limit







    if not all_rows:



        result = pd.DataFrame()



    else:



        result = pd.concat(all_rows, ignore_index=True)







    with open(cache_file, "wb") as f:



        pickle.dump(result, f)







    return result











def fetch_dc_member_all(trade_date):



    """



    鎷夊彇东财概念鎴愬垎条



    鍗曟鏈€条000鏉★紝瓒呰繃鍒欐寜 offset 鍒嗛〉鎷夊彇



    """



    cache_file = os.path.join(CACHE_DIR, f"dc_member_{trade_date}.pkl")







    if os.path.exists(cache_file):



        with open(cache_file, "rb") as f:



            return pickle.load(f)







    all_rows = []



    offset = 0



    limit = DC_BATCH_SIZE







    while True:



        try:



            df = pro.dc_member(



                **{



                    "trade_date": int(trade_date),



                    "ts_code": "",



                    "con_code": "",



                    "start_date": "",



                    "end_date": "",



                    "limit": str(limit),



                    "offset": str(offset)



                },



                fields=DC_MEMBER_FIELDS



            )



        except Exception as e:



            print(f"dc_member 鎷夊彇澶辫触: {e}")



            break







        if df is None or df.empty:



            break







        all_rows.append(df)







        if len(df) < limit:



            break







        offset += limit







    if not all_rows:



        result = pd.DataFrame()



    else:



        result = pd.concat(all_rows, ignore_index=True)







    with open(cache_file, "wb") as f:



        pickle.dump(result, f)







    return result











# =========================================================



# 涓嬭浇东财概念鍒楄〃锛堟浛浠ｅ悓鑺遍『 ths_index条



# =========================================================



def download_ths_concepts():



    """



    鑾峰彇东财概念鍒楄〃锛堟浛浠ｅ師鍚岃姳条ths_index条



    """



    print("鑾峰彇东财概念鍒楄〃...")







    df = fetch_dc_index_all(TRADE_DATE, idx_type="姒傚康鏉垮潡")







    if df is None or df.empty:



        return pd.DataFrame()







    print(f"东财概念鍒楄〃已加载 {len(df)} 条)



"
    return df











# =========================================================



# 涓嬭浇东财概念鎴愬垎鑲★紙鏇夸唬鍚岃姳条ths_member条



# =========================================================



def download_ths_members(concept_df):



    """



    鑾峰彇东财概念鎴愬垎鑲★紙鏇夸唬条ths_member条



    """



    print("鑾峰彇东财概念鎴愬垎条..")







    # ========= 缂撳瓨鍛戒腑 =========



    if os.path.exists(CONCEPT_DETAIL_PATH):



        print(f"璇诲彇缂撳瓨: {CONCEPT_DETAIL_PATH}")



        with open(CONCEPT_DETAIL_PATH, "rb") as f:



            return pickle.load(f)







    # ========= 鎷夊彇鍏ㄥ競鍦烘垚鍒嗚偂 =========



    member_df = fetch_dc_member_all(TRADE_DATE)







    if member_df is None or member_df.empty:



        return pd.DataFrame()







    # 条ts_code锛堜笢璐㈡蹇典唬鐮侊級鏄犲皠鍒版蹇靛悕



    concept_map = {}



    if concept_df is not None and not concept_df.empty:



        for _, row in concept_df.iterrows():



            concept_map[row['ts_code']] = row['name']







    if concept_map:



        member_df['concept_name'] = member_df['ts_code'].map(concept_map)



        member_df = member_df.dropna(subset=['concept_name'])







    # ========= 鍐欑紦条=========



    with open(CONCEPT_DETAIL_PATH, "wb") as f:



        pickle.dump(member_df, f)







    print(f"东财概念鎴愬垎鑲″凡淇濆瓨: {CONCEPT_DETAIL_PATH}, 条{len(member_df)} 条)



"
    return member_df







# =========================================================



# 鏋勫缓 鑲＄エ -> 姒傚康



# =========================================================



# 鏋勫缓 鑲＄エ -> 姒傚康锛堝甫缂撳瓨锛屾寜澶╂洿鏂帮級



# =========================================================



def build_stock_concept_map(member_df):



    # 缂撳瓨文件鍚嶅姞涓婃棩鏈燂紝鎸夊ぉ鏇存柊



    cache_file = os.path.join(CACHE_DIR, f"stock_concept_map_{TRADE_DATE}.pkl")







    # ========= 缂撳瓨鍛戒腑锛堝綋澶╋級 =========



    if os.path.exists(cache_file):



        print(f"璇诲彇褰撴棩缂撳瓨: {cache_file}")



        with open(cache_file, "rb") as f:



            return pickle.load(f)







    # ========= 閲嶆柊鐢熸垚 =========



    stock_map = defaultdict(list)







    for _, row in member_df.iterrows():



        ts_code = row["con_code"]



        concept = row["concept_name"]



        stock_map[ts_code].append(concept)







    stock_map = {



        k: ";".join(sorted(set(v)))



        for k, v in stock_map.items()



    }







    # ========= 鍐欑紦条=========



    with open(cache_file, "wb") as f:



        pickle.dump(stock_map, f)







    print(f"鑲＄エ姒傚康鏄犲皠宸蹭繚瀛?{cache_file}")







    return stock_map







# =========================================================



# 鏋勫缓 姒傚康 -> 鑲＄エ



# =========================================================



# =========================================================



# 鏋勫缓 姒傚康 -> 鑲＄エ锛堝甫缂撳瓨条



# =========================================================



# 鏋勫缓 姒傚康 -> 鑲＄エ锛堝甫缂撳瓨锛屾寜澶╂洿鏂帮級



# =========================================================



def build_concept_stock_map(member_df):



    # 缂撳瓨文件鍚嶅姞涓婃棩鏈燂紝鎸夊ぉ鏇存柊



    cache_file = os.path.join(CACHE_DIR, f"concept_stock_map_{TRADE_DATE}.pkl")







    # ========= 缂撳瓨鍛戒腑锛堝綋澶╋級 =========



    if os.path.exists(cache_file):



        print(f"璇诲彇褰撴棩缂撳瓨: {cache_file}")



        with open(cache_file, "rb") as f:



            return pickle.load(f)







    # ========= 閲嶆柊鐢熸垚 =========



    concept_map = defaultdict(list)







    for _, row in member_df.iterrows():



        ts_code = row["ts_code"]



        concept = row["concept_name"]



        concept_map[concept].append(ts_code)







    concept_map = {



        k: sorted(set(v))



        for k, v in concept_map.items()



    }







    # ========= 鍐欑紦条=========



    with open(cache_file, "wb") as f:



        pickle.dump(concept_map, f)







    print(f"姒傚康鑲＄エ鏄犲皠宸蹭繚瀛?{cache_file}")







    return concept_map











# =========================================================



# 璇诲彇鑲＄エ姒傚康缂撳瓨



# =========================================================



def load_stock_concept_map():



    cache_file = os.path.join(CACHE_DIR, f"stock_concept_map_{TRADE_DATE}.pkl")



    with open(cache_file, "rb") as f:



        return pickle.load(f)











# =========================================================



# 璇诲彇姒傚康鑲＄エ缂撳瓨



# =========================================================



def load_concept_stock_map():



    cache_file = os.path.join(CACHE_DIR, f"concept_stock_map_{TRADE_DATE}.pkl")



    with open(cache_file, "rb") as f:







        return pickle.load(f)















# =========================================================



# 鍒濆鍖栨蹇电紦条



# =========================================================



def init_concept_cache():







    concept_df = download_ths_concepts()







    member_df = download_ths_members(concept_df)







    stock_map = build_stock_concept_map(member_df)







    concept_map = build_concept_stock_map(member_df)







    print("姒傚康缂撳瓨鍒濆鍖栧畬条)







    return stock_map, concept_map















# =========================================================



# 鐢熸垚 concept dataframe



# =========================================================



def build_concept_df(stock_map):







    rows = []







    for ts_code, concept in stock_map.items():







        rows.append({







            "ts_code": ts_code,







            "concept": concept



        })







    return pd.DataFrame(rows)















# =========================================================



# 鏃ョ嚎鏁版嵁



# =========================================================



def get_daily_df():







    print("璇诲彇鍏ㄥ競鍦鸿条..")







    # ========= 缂撳瓨文件 =========



    cache_file = os.path.join(



        CACHE_DIR,



        f"daily_{TRADE_DATE}.csv"



    )







    # ========= 浼樺厛璇诲彇缂撳瓨 =========



    if os.path.exists(cache_file):







        print(f"璇诲彇缂撳瓨: {cache_file}")







        df = pd.read_csv(



            cache_file,



            dtype={



                'ts_code': str



            }



        )







        return df







    print("缂撳瓨涓嶅瓨鍦紝寮€濮嬩粠Tushare涓嬭浇...")







    # ========= 涓嬭浇鏁版嵁 =========



    df = pro.daily(



        trade_date=TRADE_DATE



    )







    if df.empty:







        return pd.DataFrame()







    # ========= 鎴愪氦棰濊浆条=========



    # tushare amount鍗曚綅涓哄崈条



    # 浜垮厓 = 鍗冨厓 / 100000



    df['amount'] = (



        df['amount'] / 100000



    )







    # ========= 淇濆瓨缂撳瓨 =========



    df.to_csv(



        cache_file,



        index=False,



        encoding='utf-8-sig'



    )







    print(f"缂撳瓨宸蹭繚瀛?{cache_file}")







    return df







# =========================================================



# 鐢充竾琛屼笟锛圠2/L3条



# =========================================================



def get_sw_industry_map():







    cache_file = os.path.join(CACHE_DIR, "sw_map.csv")







    if os.path.exists(cache_file):







        df = pd.read_csv(cache_file, dtype=str)







        if not df.empty:



            return df







    df = pro.index_member_all(is_new='Y')







    df.to_csv(cache_file, index=False)







    return df







# =========================================================



# 缂撳瓨 limit_list_ths 鏁版嵁



# =========================================================



def get_limit_list_ths(trade_date, limit_type):



    cache_file = os.path.join(CACHE_DIR, f"limit_list_ths_{trade_date}_{limit_type}.pkl")



    



    if os.path.exists(cache_file):



        with open(cache_file, "rb") as f:



            return pickle.load(f)



    



    try:



        df = pro.limit_list_ths(trade_date=trade_date, limit_type=limit_type)



        with open(cache_file, "wb") as f:



            pickle.dump(df, f)



        return df



    except Exception:



        return pd.DataFrame()







# =========================================================



# 缂撳瓨 limit_step 鏁版嵁



# =========================================================



def get_limit_step(trade_date):



    cache_file = os.path.join(CACHE_DIR, f"limit_step_{trade_date}.pkl")



    



    if os.path.exists(cache_file):



        with open(cache_file, "rb") as f:



            return pickle.load(f)



    



    try:



        df = pro.limit_step(trade_date=trade_date)



        with open(cache_file, "wb") as f:



            pickle.dump(df, f)



        return df



    except Exception:



        return pd.DataFrame()







# =========================================================



# 榫欏ご楂樺害鍥犲瓙锛堣繛鏉块珮搴︺€佹定鍋滃崰姣斻€佸皝鏉垮己搴︼級



# =========================================================







# 鍐呭瓨缂撳瓨



_lb_height_cache = {}



_leader_factor_cache = {}







def get_stock_lb_height(ts_code):



    """



    鑾峰彇鍗曞彧鑲＄エ鐨勮繛鏉块珮搴︼紙甯﹀唴瀛樼紦瀛橈級



    """



    cache_key = (str(ts_code), TRADE_DATE)



    if cache_key in _lb_height_cache:



        return _lb_height_cache[cache_key]



    



    lb_df = get_limit_step(TRADE_DATE)



    result = 1



    if lb_df is not None and not lb_df.empty:



        lb_df['ts_code'] = lb_df['ts_code'].astype(str)



        ts_code_str = str(ts_code)



        stock_lb = lb_df[lb_df['ts_code'] == ts_code_str]



        if not stock_lb.empty and 'nums' in stock_lb.columns:



            result = int(stock_lb['nums'].fillna(1).iloc[0])



    



    _lb_height_cache[cache_key] = result



    return result







def calc_leader_height_factor(sector_codes):



    """



    榫欏ご楂樺害鍥犲瓙锛氳　閲忔澘鍧楀唴娑ㄥ仠鑲＄殑杩炴澘楂樺害銆佸皝鏉垮己条



    条limit_list_ths 娑ㄥ仠姹犳帴鍙ｈ幏鍙栫簿纭定鍋滄暟条



    杩斿洖条楂樺害条 娑ㄥ仠鍗犳瘮%, 鏉垮潡杩炴澘鏈€楂樻澘)锛堝甫鍐呭瓨缂撳瓨条



    """



    # 浣跨敤鎺掑簭鍚庣殑浠ｇ爜浣滀负缂撳瓨閿紝纭繚鐩稿悓闆嗗悎鏈夌浉鍚岄敭



    sorted_codes = sorted(str(c) for c in sector_codes)



    cache_key = (tuple(sorted_codes), TRADE_DATE)



    if cache_key in _leader_factor_cache:



        return _leader_factor_cache[cache_key]



    



    if not sector_codes:



        result = (0, 0, 0)



        _leader_factor_cache[cache_key] = result



        return result







    zt_df = get_limit_list_ths(TRADE_DATE, '娑ㄥ仠条)







    if zt_df is None or zt_df.empty:



        result = (0, 0, 0)



        _leader_factor_cache[cache_key] = result



        return result







    zt_df['ts_code'] = zt_df['ts_code'].astype(str)



    sector_codes_set = set(str(c) for c in sector_codes)







    # 绛涢€夊睘浜庤鏉垮潡鐨勬定鍋滆偂



    sector_zt = zt_df[zt_df['ts_code'].isin(sector_codes_set)]







    if sector_zt.empty:



        result = (0, 0, 0)



        _leader_factor_cache[cache_key] = result



        return result







    zt_count = len(sector_zt)



    total_count = len(sector_codes_set)



    zt_ratio = zt_count / max(total_count, 1)







    # 杩炴澘楂樺害锛堟牳蹇冿級



    max_lb = 1



    total_lb_score = 0







    lb_df = get_limit_step(TRADE_DATE)



    if lb_df is not None and not lb_df.empty:



        lb_df['ts_code'] = lb_df['ts_code'].astype(str)



        sector_lb = lb_df[lb_df['ts_code'].isin(sector_codes_set)]



        if not sector_lb.empty and 'nums' in sector_lb.columns:



            nums = sector_lb['nums'].fillna(1).astype(int)



            max_lb = nums.max()



            for n in nums:



                if n >= 2:



                    total_lb_score += n * n







    # 榫欏ご楂樺害璇勫垎锛堥潪绾挎€ч€掑条



    if max_lb >= 7:



        height_score = 50



    elif max_lb >= 5:



        height_score = 35



    elif max_lb >= 4:



        height_score = 25



    elif max_lb >= 3:



        height_score = 18



    elif max_lb >= 2:



        height_score = 10



    else:



        height_score = 3







    ratio_score = min(zt_ratio * 100, 30)



    lb_total_score = min(total_lb_score, 40)



    leader_height = height_score + ratio_score + lb_total_score







    result = (round(leader_height, 2), round(zt_ratio * 100, 1), int(max_lb))



    _leader_factor_cache[cache_key] = result



    return result







def calc_sector_score(df, sector_codes=None):







    if df is None or len(df) == 0:



        return 0







    pct = df["pct_chg"].dropna()







    amount = df["amount"].fillna(0)







    n = len(df)







    # =====================================================



    # 1. 鍘绘瀬鍊煎姩条



    # =====================================================



    pct_sorted = pct.sort_values()







    left = int(n * 0.1)



    right = int(n * 0.9)







    trimmed = pct_sorted.iloc[left:right]







    momentum = trimmed.mean()







    # =====================================================



    # 2. 榫欏ご寮哄害



    # =====================================================



    top1 = pct.max()







    top3 = pct.nlargest(min(3, n)).mean()







    leader_strength = (



        top1 * 2



        + top3 * 1.5



    )







    # =====================================================



    # 3. 鎵╂暎寮哄害



    # =====================================================



    strong_cnt = (pct >= 5).sum()







    limit_up = (pct >= 9.5).sum()







    spread_ratio = strong_cnt / n







    spread_strength = (



        limit_up * 8



        + spread_ratio * 30



    )







    # =====================================================



    # 4. 鎯呯华缁撴瀯



    # =====================================================



    high_cnt = (pct >= 7).sum()







    mid_cnt = (



        (pct >= 3)



        & (pct < 7)



    ).sum()







    weak_cnt = (pct < 0).sum()







    emotion_strength = (



        high_cnt * 3



        + mid_cnt * 1.5



        - weak_cnt * 1.2



    )







    # =====================================================



    # 5. 璧勯噾缁撴瀯



    # =====================================================



    total_amount = amount.sum()







    top5_ratio = (



        amount.nlargest(



            min(5, n)



        ).sum()



        / max(total_amount, 1)



    )







    money_spread = 1 - top5_ratio







    capital_strength = (



        total_amount / 100



        + money_spread * 20



    )







    # =====================================================



    # 6. 涓€鑷达拷?



    # =====================================================



    consistency = max(



        0,



        10 - pct.std()



    )







    # =====================================================



    # 7. 榫欏ご楂樺害鍥犲瓙



    # =====================================================



    leader_height = 0



    if sector_codes:



        leader_height, _, _ = calc_leader_height_factor(sector_codes)







    # =====================================================



    # 缁煎悎璇勫垎锛堝惈榫欏ご楂樺害条



    # =====================================================



    score = (







        momentum * 1.5







        + leader_strength * 1.8







        + spread_strength * 1.5







        + emotion_strength * 1.2







        + capital_strength * 0.8







        + consistency * 2







        + leader_height * 2.0



    )







    return round(score, 2)







def calc_sector_score1(df):







    if df is None or len(df) == 0:



        return 0







    pct = df["pct_chg"]







    # =========================



    # 1. 鍩虹鍔ㄩ噺



    # =========================



    momentum = pct.mean()







    # =========================



    # 2. 娑ㄥ仠寮哄害



    # =========================



    limit_up = (pct >= 9.5).sum()







    # =========================



    # 3. 璧氶挶鏁堝簲



    # =========================



    up_ratio = (pct > 0).mean()







    median_chg = pct.median()







    # =========================



    # 4. 璧勯噾寮哄害



    # =========================



    money = df["amount"].sum() / 1e8







    # =========================



    # 5. 璧勯噾闆嗕腑搴︼紙鎶卞洟条



    # =========================



    try:



        top5_ratio = (



            df.sort_values("amount", ascending=False)



              .head(5)["amount"].sum()



            / df["amount"].sum()



        )



    except:



        top5_ratio = 0







    # =========================



    # 6. 椋庨櫓鎶戝埗



    # =========================



    limit_down = (pct <= -9.5).sum()







    # =========================



    # 缁煎悎璇勫垎锛堟満鏋勬潈閲嶏級



    # =========================



    score = (







        momentum * 1.2



        + limit_up * 6



        + up_ratio * 5



        + median_chg * 1.5



        + money * 0.8



        + top5_ratio * 8



        - limit_down * 10



    )







    return score







# =========================================================



# 榫欏ご璇嗗埆锛圴6 浼樺寲鐗堬級



# =========================================================







# 鍐呭瓨缂撳瓨锛氳偂绁ㄥ巻鍙茶繛鏉夸俊条



_stock_lb_history_cache = {}







def get_stock_max_lb_history(ts_code):



    """



    鑾峰彇鑲＄エ鍘嗗彶鏈€楂樿繛鏉块珮搴︼紙浠庤繃条0涓氦鏄撴棩鐨刲imit_step鏁版嵁条



    """



    cache_key = str(ts_code)



    if cache_key in _stock_lb_history_cache:



        return _stock_lb_history_cache[cache_key]



    



    max_lb = 1



    try:



        # 鑾峰彇褰撳墠浜ゆ槗鏃ヤ箣鍓嶇殑鍘嗗彶鏁版嵁锛堟渶澶氬洖条0涓氦鏄撴棩条



        from datetime import datetime, timedelta



        current_date = datetime.strptime(TRADE_DATE, "%Y%m%d")



        



        # 鍥炴函鏈€条0涓氦鏄撴棩



        for i in range(1, 11):



            check_date = (current_date - timedelta(days=i)).strftime("%Y%m%d")



            lb_df = get_limit_step(check_date)



            if lb_df is not None and not lb_df.empty:



                lb_df['ts_code'] = lb_df['ts_code'].astype(str)



                stock_lb = lb_df[lb_df['ts_code'] == str(ts_code)]



                if not stock_lb.empty and 'nums' in stock_lb.columns:



                    lb = int(stock_lb['nums'].fillna(1).iloc[0])



                    if lb > max_lb:



                        max_lb = lb



    except Exception as e:



        pass



    



    _stock_lb_history_cache[cache_key] = max_lb



    return max_lb







def calc_stock_strength(stock_df):



    """



    璁＄畻鑲＄エ寮哄害璇勫垎锛圴6.1浼樺寲鐗堬級



    缁煎悎鑰冭檻锛氬巻鍙茶繛鏉块珮搴︺€佸綋鍓嶈繛鏉跨姸鎬併€佹垚浜ら銆佽繎鏈熸定条



    """



    ts_code = stock_df["ts_code"].iloc[0]



    



    # 1. 鍘嗗彶鏈€楂樿繛鏉块珮搴︽潈閲嶏紙鏈€閲嶈锛屾父璧勭湅鍘嗗彶鍦颁綅条



    history_max_lb = get_stock_max_lb_history(ts_code)



    history_lb_score = 0



    if history_max_lb >= 6:



        history_lb_score = 400  # 6鏉垮強浠ヤ笂锛氳秴绾ч緳条



    elif history_max_lb >= 5:



        history_lb_score = 300  # 5鏉匡細寮洪緳条



    elif history_max_lb >= 4:



        history_lb_score = 220  # 4鏉匡細榫欏ご



    elif history_max_lb >= 3:



        history_lb_score = 140  # 3鏉匡細灏忛緳条



    elif history_max_lb >= 2:



        history_lb_score = 60   # 2鏉匡細鏈夋綔条



    



    # 2. 褰撳墠杩炴澘鐘讹拷?



    current_lb = get_stock_lb_height(ts_code)



    current_lb_score = 0



    if current_lb >= 5:



        current_lb_score = 250  # 褰撳墠5鏉匡細甯傚満鐒︾偣



    elif current_lb >= 4:



        current_lb_score = 180  # 褰撳墠4条



    elif current_lb >= 3:



        current_lb_score = 120  # 褰撳墠3条



    elif current_lb >= 2:



        current_lb_score = 60   # 褰撳墠2条



    



    # 3. 鎴愪氦棰濇潈閲嶏紙条鏃ュ钩鍧囷紝鍙嶆槧璧勯噾鍏虫敞搴︼級



    recent_amount = stock_df["amount"].tail(5).mean() / 1e8



    amount_score = 0



    if recent_amount >= 20:



        amount_score = 100  # 20条锛氱粷瀵圭劍条



    elif recent_amount >= 10:



        amount_score = 80   # 10-20浜匡細楂樺叧娉ㄥ害



    elif recent_amount >= 5:



        amount_score = 60    # 5-10浜匡細涓瓑鍏虫敞



    elif recent_amount >= 2:



        amount_score = 40    # 2-5浜匡細鏈夎祫条



    



    # 4. 杩戞湡娑ㄥ箙锛堣繎5鏃ョ疮璁★紝鍙嶆槧瓒嬪娍寮哄害条



    pct_score = 0



    if len(stock_df) >= 5:



        recent_pct = (stock_df["close"].iloc[-1] / stock_df["close"].iloc[-5] - 1) * 100



        if recent_pct >= 50:



            pct_score = 80  # 50%+锛氳秴寮鸿秼条



        elif recent_pct >= 30:



            pct_score = 60  # 30-50%锛氬己瓒嬪娍



        elif recent_pct >= 20:



            pct_score = 40  # 20-30%锛氫笉閿欒秼条



    



    # 5. 浠婃棩娑ㄨ穼条



    today_pct = stock_df["pct_chg"].iloc[-1] if not pd.isna(stock_df["pct_chg"].iloc[-1]) else 0



    today_pct_score = max(today_pct * 3, 0)



    



    # 6. 鏄惁鏄綋鍓嶆定鍋滐紙棰濆鍔犲垎条



    is_zt = False



    try:



        zt_df = get_limit_list_ths(TRADE_DATE, '娑ㄥ仠条)



        if zt_df is not None and not zt_df.empty:



            zt_codes = set(zt_df['ts_code'].astype(str).tolist())



            is_zt = str(ts_code) in zt_codes



    except:



        pass



    



    zt_bonus = 100 if is_zt else 0



    



    total_score = (



        history_lb_score +



        current_lb_score +



        amount_score +



        pct_score +



        today_pct_score +



        zt_bonus



    )



    



    return total_score







def get_stock_name_map():







    cache_file = os.path.join(CACHE_DIR, "name_map.csv")







    if os.path.exists(cache_file):







        df = pd.read_csv(cache_file, dtype=str)







        if not df.empty:



            return df







    df = pro.stock_basic(



        exchange='',



        list_status='L',



        fields='ts_code,name'



    )







    df.to_csv(cache_file, index=False, encoding='utf-8-sig')







    return df











def find_leader(sector_df):



    """



    瀵绘壘鏉垮潡榫欏ご锛氫紭鍏堥€夋嫨娑ㄥ仠鐨勮偂绁紝鍐嶆牴鎹己搴﹁瘎鍒嗛€夋嫨



    """



    best_code = None



    best_name = None



    best_score = -1



    is_zt_best = False







    # 鑾峰彇娑ㄥ仠鑲＄エ鍒楄〃



    zt_df = get_limit_list_ths(TRADE_DATE, '娑ㄥ仠条)



    zt_codes = set()



    if zt_df is not None and not zt_df.empty:



        zt_codes = set(zt_df['ts_code'].astype(str).tolist())







    for ts_code, g in sector_df.groupby("ts_code"):



        ts_code_str = str(ts_code)



        score = calc_stock_strength(g)



        



        # 濡傛灉鏄定鍋滆偂绁紝缁欎簣棰濆鍔犲垎



        if ts_code_str in zt_codes:



            score += 100  # 娑ㄥ仠鑲＄エ浼樺厛



        



        if score > best_score:



            best_score = score



            best_code = ts_code



            row = g.iloc[-1]



            best_name = row["name"] if "name" in row else ts_code



            is_zt_best = ts_code_str in zt_codes







    return best_code, best_name, best_score











# =========================================================



# V4/V5 鐘舵€佺紦条



# =========================================================



sector_state = defaultdict(lambda: {







    "history": [],



    "momentum": 0,



    "acc": 0,



    "leader": None



})











def init_sector_state(days=10):



    """浠庢暟鎹簱加载鍘嗗彶璇勫垎鏁版嵁锛屽垵濮嬪寲 sector_state"""



    global sector_state



    



    try:



        conn = sqlite3.connect(DB_PATH)



        



        query = """



            SELECT date, name, score



            FROM hot_sector



            ORDER BY date DESC



            LIMIT ?



        """



        



        df = pd.read_sql(query, conn, params=(days * 20,))



        conn.close()



        



        if len(df) == 0:



            print("[缂撳瓨] 鏁版嵁搴撴棤鍘嗗彶鏁版嵁")



            return



        



        df = df.sort_values(["name", "date"])



        



        for name, group in df.groupby("name"):



            history = group["score"].tolist()[-10:]



            sector_state[name]["history"] = history



            



            n = len(history)



            if n >= 2:



                if n >= 3:



                    sector_state[name]["momentum"] = history[-1] - history[-3]



                else:



                    sector_state[name]["momentum"] = history[-1] - history[-2]



            else:



                sector_state[name]["momentum"] = 0



            



            if n >= 3:



                sector_state[name]["acc"] = (history[-1] - history[-2]) - (history[-2] - history[-3])



            else:



                sector_state[name]["acc"] = 0



        



        loaded_count = len([k for k, v in sector_state.items() if len(v["history"]) > 0])



        print(f"[缂撳瓨] 宸插姞杞絳loaded_count} 涓澘鍧楃殑鍘嗗彶鏁版嵁")



        



    except Exception as e:



        print(f"[缂撳瓨] 加载鍘嗗彶鏁版嵁澶辫触: {e}")











# =========================================================



# 鏇存柊涓荤嚎鐘舵€侊紙V5条



# =========================================================



def update_state(name, score):







    state = sector_state[name]







    state["history"].append(score)







    if len(state["history"]) > 10:



        state["history"].pop(0)







    history = state["history"]



    n = len(history)







    if n >= 2:



        if n >= 3:



            state["momentum"] = history[-1] - history[-3]



        else:



            state["momentum"] = history[-1] - history[-2]



    else:



        state["momentum"] = 0







    if n >= 3:



        state["acc"] = (history[-1] - history[-2]) - (history[-2] - history[-3])



    else:



        state["acc"] = 0







    return state











# =========================================================



# 涓荤嚎寮哄害锛圴5鏍稿績条



# =========================================================



def calc_strength(score, state):







    return (







        score



        + MOMENTUM_W * state["momentum"]



        + ACC_W * state["acc"]



    )











# =========================================================



# 閫€娼垽条



# =========================================================



def is_decline(state):







    h = state["history"]







    if len(h) < 3:



        return False







    return h[-1] < h[-2] < h[-3]











# =========================================================



# 琛屼笟鍒嗘瀽锛圴4鏍稿績条



# =========================================================



def analyze_industry(daily_df, industry_df):







    result = []







    for level in ["l1_name", "l2_name", "l3_name"]:







        if level not in industry_df.columns:



            continue







        for name, g in industry_df.groupby(level):







            stocks = g["ts_code"].dropna().unique().tolist()







            if len(stocks) < MIN_STOCKS:



                continue







            df = daily_df[daily_df["ts_code"].isin(stocks)]







            if df.empty:



                continue







            leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)



            score = calc_sector_score(df, stocks)







            state = update_state(name, score)







            strength = calc_strength(score, state)







            leader_code, leader_name, leader_score = find_leader(df)



            state["leader"] = leader_code



            



            # 鑾峰彇榫欏ご鐨勮繛鏉块珮搴︼紙鑰屼笉鏄澘鍧楁渶澶ц繛鏉块珮搴︼級



            leader_lb_height = get_stock_lb_height(leader_code)







            result.append({







                "绫诲瀷": level,



                "涓荤嚎": name,



                "璇勫垎": score,



                "涓荤嚎寮哄害": strength,



                "鍔ㄩ噺": state["momentum"],



                "鍔犻€熷害": state["acc"],



                "榫欏ご浠ｇ爜": leader_code,



                "榫欏ご鍚嶇О": leader_name,



                "榫欏ご寮哄害": leader_score,



            "榫欏ご楂樺害": leader_height,



            "娑ㄥ仠鍗犳瘮": zt_ratio,



            "杩炴澘楂樺害": leader_lb_height,



                "鏄惁閫€条: is_decline(state),



                "鎴愬垎鑲℃暟": len(stocks)                



            })







    return result











# =========================================================



# 姒傚康鏉垮潡鍒嗘瀽锛堢洿鎺ュ垎鏋愬悓鑺遍『姒傚康条



# =========================================================



def analyze_concepts(daily_df):



    



    result = []



    



    if not os.path.exists(CONCEPT_DETAIL_PATH):



        print("[姒傚康鍒嗘瀽] 姒傚康鎴愬垎鑲℃暟鎹笉瀛樺湪")



        return result



    



    with open(CONCEPT_DETAIL_PATH, "rb") as f:



        member_df = pickle.load(f)



    



    daily_codes = set(daily_df["ts_code"].unique())



    



    concept_to_stocks = defaultdict(list)



    



    for _, row in member_df.iterrows():



        stock_code = row.get("con_code", "")



        concept_name = row.get("concept_name", "")



        



        if not stock_code or not concept_name:



            continue



        



        if stock_code in daily_codes:



            concept_to_stocks[concept_name].append(stock_code)



    



    print(f"[姒傚康鍒嗘瀽] 鏈夋晥姒傚康鎬绘暟: {len(concept_to_stocks)}")



    



    for concept_name, stocks in concept_to_stocks.items():



        



        if len(stocks) < MIN_STOCKS:



            continue



        



        df = daily_df[daily_df["ts_code"].isin(stocks)]



        



        if df.empty:



            continue



        



        leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)



        score = calc_sector_score(df, stocks)



        



        state = update_state(concept_name, score)



        



        strength = calc_strength(score, state)



        



        leader_code, leader_name, leader_score = find_leader(df)



        



        # 鑾峰彇榫欏ご鐨勮繛鏉块珮搴︼紙鑰屼笉鏄澘鍧楁渶澶ц繛鏉块珮搴︼級



        leader_lb_height = get_stock_lb_height(leader_code)



        



        result.append({



            "绫诲瀷": "姒傚康",



            "涓荤嚎": concept_name,



            "璇勫垎": score,



            "涓荤嚎寮哄害": strength,



            "鍔ㄩ噺": state["momentum"],



            "鍔犻€熷害": state["acc"],



            "榫欏ご浠ｇ爜": leader_code,



            "榫欏ご鍚嶇О": leader_name,



            "榫欏ご寮哄害": leader_score,



            "榫欏ご楂樺害": leader_height,



            "娑ㄥ仠鍗犳瘮": zt_ratio,



            "杩炴澘楂樺害": leader_lb_height,



            "鏄惁閫€条: is_decline(state),



            "鎴愬垎鑲℃暟": len(stocks)



        })



    



    print(f"姒傚康鏉垮潡鍒嗘瀽完成锛屽叡 {len(result)} 涓条)



"
    return result











# =========================================================



# 涓婚鍒嗘瀽锛堟浛浠ｆ蹇碉級- 浣跨敤 theme_portfolio_strategy_cached.py 鐨勫噯纭尮閰嶇畻条



# =========================================================



def analyze_themes(daily_df, industry_df, stock_concept_list):



    """



    鍒嗘瀽涓婚鏉垮潡寮哄害



    鍖归厤閫昏緫鏉ヨ嚜 theme_portfolio_strategy_cached.py 条build_theme_portfolio



    瑙勫垯条



    1. 琛屼笟鍖归厤锛歋W L1/L2/L3 条industry_list 条



    2. 姒傚康鍖归厤锛歴tock_concept 绮剧‘鍖归厤 concept_list锛堥潪 keywords条



    3. 琛屼笟鍖归厤 OR 姒傚康鍖归厤 = 鎴愪唤条



    4. keywords 浠呯敤浜庤瘎鍒嗘帓搴忥紝exclude_keywords 浠呯敤浜庤繃条



    """



    result = []







    # 棰勬瀯寤鸿偂绁ㄥ悕绉板瓧鍏革紙鍔犻€焑xclude杩囨护条



    stock_name_dict = dict(zip(daily_df["ts_code"], daily_df["name"]))







    for theme, cfg in THEME_MAP.items():



        industry_list = cfg.get("industry", [])



        concept_list = cfg.get("concept", [])



        keyword_list = cfg.get("keywords", [])



        exclude_keywords = cfg.get("exclude_keywords", [])







        # 鈹€鈹€ 琛屼笟鍖归厤锛圫W L1/L2/L3锛屼繚鐣欒涓歞f鐨勫噯纭槧灏勶級鈹€鈹€



        industry_mask = industry_df.apply(



            lambda x, ind_list=industry_list: (



                (x.get("l1_name") in ind_list) or



                (x.get("l2_name") in ind_list) or



                (x.get("l3_name") in ind_list)



            ),



            axis=1



        )



        industry_stocks = set(industry_df.loc[industry_mask, "ts_code"].dropna().unique())







        # 鈹€鈹€ 姒傚康鍖归厤锛堢簿纭尮閰嶆蹇靛悕绉帮級鈹€鈹€



        concept_stocks = set()



        for ts_code, concepts in stock_concept_list.items():



            for c in concept_list:



                if c in concepts:



                    concept_stocks.add(ts_code)



                    break







        stocks = list(industry_stocks | concept_stocks)







        if len(stocks) < MIN_STOCKS:



            continue







        # 鈹€鈹€ exclude_keywords杩囨护锛堟蹇靛悕寮€澶村尮条鑲＄エ鍚嶅尮閰嶏級鈹€鈹€



        if exclude_keywords:



            filtered = []



            for ts_code in stocks:



                stock_name = str(stock_name_dict.get(ts_code, "")) or ""



                concepts = stock_concept_list.get(ts_code, [])



                skip = False



                for ek in exclude_keywords:



                    if ek in stock_name:



                        skip = True



                        break



                    for c in concepts:



                        if c.startswith(ek):



                            skip = True



                            break



                    if skip:



                        break



                if not skip:



                    filtered.append(ts_code)



            stocks = filtered







        if len(stocks) < MIN_STOCKS:



            continue







        df = daily_df[daily_df["ts_code"].isin(stocks)]







        if df.empty:



            continue







        leader_height, zt_ratio, _ = calc_leader_height_factor(stocks)



        score = calc_sector_score(df, stocks)







        state = update_state(theme, score)







        # 涓婚鍔犳垚锛氶伩鍏嶈澶ц妯℃垚浠借偂绋€閲婏紝淇濇寔涓婚涓庢蹇靛钩璧峰钩条



        THEME_BONUS = 1.5



        strength = calc_strength(score, state) * THEME_BONUS







        leader_code, leader_name, leader_score = find_leader(df)



        leader_lb_height = get_stock_lb_height(leader_code)







        result.append({



            "绫诲瀷": "涓婚",



            "涓荤嚎": theme,



            "璇勫垎": score,



            "涓荤嚎寮哄害": strength,



            "鍔ㄩ噺": state["momentum"],



            "鍔犻€熷害": state["acc"],



            "榫欏ご浠ｇ爜": leader_code,



            "榫欏ご鍚嶇О": leader_name,



            "榫欏ご寮哄害": leader_score,



            "榫欏ご楂樺害": leader_height,



            "娑ㄥ仠鍗犳瘮": zt_ratio,



            "杩炴澘楂樺害": leader_lb_height,



            "鏄惁閫€条: is_decline(state),



            "鎴愬垎鑲℃暟": len(stocks)



        })







    return result











# =========================================================



# 涓婚椋庢牸璇嗗埆锛堟儏缁┍条vs 瓒嬪娍椹卞姩条



# =========================================================



EMOTION_DRIVEN_KEYWORDS = [



    "AI", "鏈哄櫒条, "鏁板瓧", "鏅鸿兘", "鍏冨畤条, "娓告垙", "褰辫", "浼犲獟",



    "楦胯挋", "ChatGPT", "绠楀姏", "鏁版嵁", "浣庣┖", "鑸ぉ", "鑱氬彉",



    "鍒涙柊条, "鐢熺墿", "鍗婂条, "鑺墖", "淇″垱", "杞欢"



]







TREND_DRIVEN_KEYWORDS = [



    "鐢靛姏", "鐓ょ偔", "鏈夎壊", "閾惰", "淇濋櫓", "鍒稿晢", "鐭虫补",



    "閽㈤搧", "鍖栧伐", "寤烘潗", "娑堣垂", "鐧介厭", "瀹剁數", "姹借溅",



    "鑸繍", "娓彛", "鍩哄缓", "鍦颁骇", "鍖昏嵂"



]











def get_theme_style(theme_name):



    """



    鍒ゅ畾涓婚椋庢牸条



    - emotion锛氭儏缁┍鍔ㄥ瀷锛堟父璧勪富瀵硷紝AI/鏈哄櫒条鍗婂浣撶瓑条



    - trend锛氳秼鍔块┍鍔ㄥ瀷锛堟満鏋勪富瀵硷紝鐢靛姏/鐓ょ偔/閾惰绛夛級



    """



    name = str(theme_name)







    for kw in EMOTION_DRIVEN_KEYWORDS:



        if kw in name:



            return "emotion"







    for kw in TREND_DRIVEN_KEYWORDS:



        if kw in name:



            return "trend"







    # 榛樿鎯呯华椹卞姩



    return "emotion"











# =========================================================



# 涓婚鍙屽洜瀛愯瘎鍒嗭紙鎯呯华条+ 瓒嬪娍条+ 缁煎悎鍒嗭級



# 鍙傝€冧笟鐣屾渶鏉冨▉鐨勬父璧勬儏条鏈烘瀯瓒嬪娍鍙岃瑙掕瘎条



# =========================================================



def calc_theme_emotion_score(theme_df, theme_stocks, theme_name):



    """



    璁＄畻涓婚鎯呯华鍒嗭紙娓歌祫瑙嗚条



    鑼冨洿条-100



    鍙傝€冿細90+ 瓒呯骇涓荤嚎 80-90 涓诲崌 70-80 娲昏穬 60-70 杞姩 60浠ヤ笅 璺熼



    """



    if theme_df is None or theme_df.empty or not theme_stocks:



        return 0.0







    pct = theme_df["pct_chg"].dropna() if "pct_chg" in theme_df.columns else pd.Series([])



    amount = theme_df["amount"].fillna(0) if "amount" in theme_df.columns else pd.Series([])



    n = max(len(theme_stocks), 1)







    # 1. 娑ㄥ仠瀹舵暟鍗犳瘮条0%条



    zt_count = (pct >= 9.5).sum() if len(pct) > 0 else 0



    zt_ratio = zt_count / n



    limit_score = min(zt_ratio * 100 * 3, 100)







    # 2. 杩炴澘瀹舵暟鍗犳瘮条0%条



    lb_count = 0



    try:



        lb_df = get_limit_step(TRADE_DATE)



        if lb_df is not None and not lb_df.empty:



            lb_df['ts_code'] = lb_df['ts_code'].astype(str)



            theme_lb = lb_df[lb_df['ts_code'].isin([str(c) for c in theme_stocks])]



            if not theme_lb.empty and 'nums' in theme_lb.columns:



                lb_count = (theme_lb['nums'].fillna(1) >= 2).sum()



    except Exception:



        pass



    lb_ratio = lb_count / n



    lb_score = min(lb_ratio * 100 * 5, 100)







    # 3. 榫欏ご楂樺害条5%条



    leader_height_score = 0



    try:



        leader_h, _, max_lb = calc_leader_height_factor(theme_stocks)



        leader_height_score = min(leader_h, 100)



    except Exception:



        pass







    # 4. 鏅嬬骇鐜囷紙15%锛夆€旓拷?鐢ㄨ繛鏉垮条/ 娑ㄥ仠瀹舵暟



    if zt_count > 0:



        promote_rate = min((lb_count / zt_count) * 100, 100)



    else:



        promote_rate = 0







    # 5. 鐐告澘淇条0%锛夆€旓拷?娑ㄥ仠 / (娑ㄥ仠+鐐告澘)



    try:



        zt_pool = get_limit_list_ths(TRADE_DATE, '娑ㄥ仠条)



        zt_codes = set(zt_pool['ts_code'].astype(str).tolist()) if zt_pool is not None and not zt_pool.empty else set()



        zt_in_theme = len([c for c in theme_stocks if str(c) in zt_codes])



        if zt_in_theme > 0:



            broken_rate = 0  # 绠€鍖栵細鐐告澘鐜囬渶棰濆鎺ュ彛



            broken_score = 100 - broken_rate



        else:



            broken_score = 50



    except Exception:



        broken_score = 50







    # 6. 20cm鏁伴噺鍗犳瘮条0%条



    cm20_count = (pct >= 19.5).sum() if len(pct) > 0 else 0



    cm20_ratio = (cm20_count / n) * 100







    # 缁煎悎鎯呯华条



    emotion_score = (



        0.30 * limit_score +



        0.20 * lb_score +



        0.15 * leader_height_score +



        0.15 * promote_rate +



        0.10 * broken_score +



        0.10 * min(cm20_ratio, 100)



    )



    emotion_score = max(0, min(100, emotion_score))







    return round(emotion_score, 2)











def calc_theme_trend_score(theme_df, theme_stocks, theme_name, daily_df_all=None):



    """



    璁＄畻涓婚瓒嬪娍鍒嗭紙鏈烘瀯瑙嗚条



    鑼冨洿条-100



    鍙傝€冿細80+ 鏈烘瀯鎶卞洟 70+ 瓒嬪娍鍚戜笂 60+ 闇囪崱鍚戜笂 50浠ヤ笅 寮卞娍



    """



    if theme_df is None or theme_df.empty or not theme_stocks:



        return 0.0







    pct = theme_df["pct_chg"].dropna() if "pct_chg" in theme_df.columns else pd.Series([])



    amount = theme_df["amount"].fillna(0) if "amount" in theme_df.columns else pd.Series([])



    n = max(len(theme_stocks), 1)







    # 1. 20鏃ユ定骞咃紙30%锛夆€旓拷?鐢ㄤ粖鏃ユ定骞呬綔涓轰唬条



    avg_pct = pct.mean() if len(pct) > 0 else 0



    pct_20d_score = min(max(avg_pct * 5, 0), 100)







    # 2. 10鏃ユ定骞咃紙20%条



    pct_10d_score = min(max(avg_pct * 3, 0), 100)







    # 3. 寮哄娍鑲℃瘮渚嬶紙娑ㄥ箙>5%锛夛紙20%条



    strong_count = (pct >= 5).sum() if len(pct) > 0 else 0



    strong_ratio = strong_count / n



    strong_score = strong_ratio * 100







    # 4. 鎴愪氦棰濆閲忥紙5鏃ュ潎条20鏃ュ潎閲忥級条5%条



    amount_growth_score = 50  # 榛樿涓瓑



    if daily_df_all is not None and "amount" in daily_df_all.columns:



        try:



            theme_daily = daily_df_all[daily_df_all["ts_code"].isin(theme_stocks)]



            if not theme_daily.empty:



                recent_amount = theme_daily["amount"].tail(len(theme_stocks) * 5).sum() if len(theme_daily) > 0 else 0



                # 绠€鍖栵細鎴愪氦棰濊秺澶氬垎瓒婇珮



                total_amount = amount.sum()



                amount_growth_score = min(total_amount / 100, 100)



        except Exception:



            pass







    # 5. 鍧囩嚎缁撴瀯锛圡A5>MA10>MA20锛夛紙15%条



    avg_ma_structure = 50  # 榛樿涓瓑锛岄渶瑕佸巻鍙叉暟条



    # 鐢变簬 daily_df 鍙湁褰撴棩鏁版嵁锛屼娇鐢ㄦ浛浠ｆ寚鏍囷細涓婃定姣斾緥



    up_ratio = (pct > 0).sum() / n if n > 0 else 0



    avg_ma_structure = up_ratio * 100







    # 缁煎悎瓒嬪娍条



    trend_score = (



        0.30 * pct_20d_score +



        0.20 * pct_10d_score +



        0.20 * strong_score +



        0.15 * amount_growth_score +



        0.15 * avg_ma_structure



    )



    trend_score = max(0, min(100, trend_score))







    return round(trend_score, 2)











def analyze_themes_dual_factor(daily_df, industry_df, stock_concept_list):



    """



    涓婚鍙屽洜瀛愮嫭绔嬭瘎鍒嗭紙鎯呯华条+ 瓒嬪娍条+ 缁煎悎鍒嗭級



    条analyze_themes() 瀹屽叏鐙珛锛屼笉鍐嶄笌姒傚康/琛屼笟娣峰悎杈撳嚭



    杈撳嚭 DataFrame 鍒楋細涓婚銆侀鏍笺€佹儏缁垎銆佽秼鍔垮垎銆佺患鍚堝垎銆佸己搴︺€侀緳澶淬€佹垚鍒嗚偂条







    瀹炵幇鏂瑰紡锛氱洿鎺ュ条d:\\mystock\\solo\\theme_trend_sentiment_score.py 条main()



    璇ユ枃浠跺凡闆嗘垚"琛屼笟鏈€条鍙屽洜瀛愯瘎鍒嗙畻娉曪紙鎯呯华条瓒嬪娍条缁煎悎鍒嗭級+ 楂樻疆璀︾ず条



    鍖呭惈瀹屾暣鐨勬垚浠借偂鍖归厤銆佽鎯呮媺鍙栥€佺紦瀛樸€丆SV/DB 鎸佷箙鍖栭€昏緫条



    """



    print("\n=== 涓婚鍙屽洜瀛愮嫭绔嬭瘎鍒嗭紙鎯呯华条+ 瓒嬪娍条+ 缁煎悎鍒嗭級===\n")



    print("  -> 璋冪敤 d:\\mystock\\solo\\theme_trend_sentiment_score.main()")







    try:



        # 条solo 鐩綍鍔犲叆 sys.path锛岀‘淇濊兘 import



        solo_dir = r"d:\mystock\solo"



        if solo_dir not in sys.path:



            sys.path.insert(0, solo_dir)







        # 鍔ㄦ€佸鍏ヤ富棰樿瘎鍒嗘ā鍧楋紙閬垮厤寰幆渚濊禆 & 鐙珛鍒锋柊鏁版嵁条



        import importlib



        tts_module = importlib.import_module("theme_trend_sentiment_score")



        importlib.reload(tts_module)







        # 璋冪敤鍏朵富娴佺▼锛堟媺鏁版嵁銆佺畻鍒嗐€佺敓条signals銆佽緭鍑鸿〃鏍笺€佽惤搴擄級



        tts_module.main()



    except Exception as e:



        print(f"[ThemeScore] 璋冪敤 theme_trend_sentiment_score.main() 澶辫触: {e}")



        import traceback



        traceback.print_exc()



        return pd.DataFrame()







    # 条SQLite 璇诲彇璇勫垎缁撴灉锛岃浆鎹负涓庢棫鎺ュ彛鍏煎条DataFrame



    try:



        db_path = os.path.join(solo_dir, "cache_backbone_tushare", "theme_trend_sentiment.db")



        if not os.path.exists(db_path):



            print(f"[ThemeScore] 鏈壘鍒拌瘎鍒嗘暟鎹簱: {db_path}")



            return pd.DataFrame()







        conn = sqlite3.connect(db_path)



        df_result = pd.read_sql(



            "SELECT * FROM theme_scores WHERE trade_date = ? ORDER BY composite_score DESC",



            conn,



            params=(TRADE_DATE,)



        )



        conn.close()



    except Exception as e:



        print(f"[ThemeScore] 璇诲彇 SQLite 澶辫触: {e}")



        return pd.DataFrame()







    if df_result.empty:



        return df_result







    # 瀛楁閲嶅懡鍚嶏紙鍏煎鏃ф帴鍙ｅ垪鍚嶏級



    style_map = {tn: get_theme_style(tn) for tn in df_result["theme"].tolist()}







    out_rows = []



    for _, r in df_result.iterrows():



        theme = r["theme"]



        composite = float(r["composite_score"])



        # 寮哄害璇勭骇



        if composite >= 80:



            strength_label = "馃煝条



        elif composite >= 65:



            strength_label = "馃煛条



        elif composite >= 50:



            strength_label = "馃煚条



        else:



            strength_label = "鈿条







        # 楂樻疆璀︾ず锛氬湪 label 鍚庤拷鍔犳爣条



        climax_flag = int(r.get("climax_warning", 0) or 0)



        if climax_flag == 1:



            strength_label = f"鈿狅笍楂樻疆{strength_label}"







        out_rows.append({



            "涓婚": theme,



            "椋庢牸": style_map.get(theme, "emotion"),



            "鎯呯华条: float(r["sentiment_score"]),



            "瓒嬪娍条: float(r["trend_score"]),



            "缁煎悎条: composite,



            "寮哄害": strength_label,



            "榫欏ご浠ｇ爜": "",



            "榫欏ご鍚嶇О": "",



            "鎴愬垎鑲℃暟": int(r["n_stocks"]),



            "楂樻疆棰勮": climax_flag,



        })







    df_result = pd.DataFrame(out_rows)



    df_result = df_result.sort_values("缁煎悎条, ascending=False).reset_index(drop=True)







    print(f"\n[ThemeScore] 涓婚鍙屽洜瀛愯瘎鍒嗗畬鎴愶紝条{len(df_result)} 涓富棰橈紙鏉ユ簮: theme_trend_sentiment_score条)



"
    print(df_result[["涓婚", "椋庢牸", "鎯呯华条, "瓒嬪娍条, "缁煎悎条, "寮哄害", "鎴愬垎鑲℃暟", "楂樻疆棰勮"]].to_string(index=False))







    return df_result











##==========缂撳瓨浠ｇ爜



def init_db():







    os.makedirs("cache", exist_ok=True)







    conn = sqlite3.connect(DB_PATH)







    cursor = conn.cursor()







    cursor.execute("""







        CREATE TABLE IF NOT EXISTS hot_sector (







            date TEXT,



            rank INTEGER,



            type TEXT,



            name TEXT,



            score REAL,



            leader_code TEXT,



            leader_name TEXT,



            leader_score REAL,



            momentum REAL,



            acc REAL,



            retreat INTEGER DEFAULT 0



        )







    """)







    conn.commit()







    conn.close()







    # 鍏煎鏃ц〃锛氭坊鍔爎etreat鍒楋紙鑻ュ凡瀛樺湪鍒欏拷鐣ワ級



    try:



        conn = sqlite3.connect(DB_PATH)



        conn.execute("ALTER TABLE hot_sector ADD COLUMN retreat INTEGER DEFAULT 0")



        conn.commit()



        conn.close()



    except:



        pass







def save_top20(df):







    conn = sqlite3.connect(DB_PATH)







    today = TRADE_DATE







    top20 = df.head(20).copy()







    # 娓呯悊褰撳ぉ鏃ф暟鎹紙閬垮厤閲嶅条



    conn.execute(



        "DELETE FROM hot_sector WHERE date=?",



        (today,)



    )







    for i, row in enumerate(top20.itertuples()):







        conn.execute("""







            INSERT INTO hot_sector



            (date, rank, type, name, score, leader_code,leader_name, leader_score, momentum, acc, retreat)







            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)







        """, (







            today,



            i + 1,



            getattr(row, "绫诲瀷", ""),



            getattr(row, "涓荤嚎", ""),



            getattr(row, "涓荤嚎寮哄害", 0),



            getattr(row, "榫欏ご浠ｇ爜", ""),



            getattr(row, "榫欏ご鍚嶇О", ""),



            getattr(row, "榫欏ご寮哄害", 0),



            getattr(row, "鍔ㄩ噺", 0),



            getattr(row, "鍔犻€熷害", 0),



            1 if getattr(row, "鏄惁閫€条, False) else 0







        ))







    conn.commit()



    conn.close()







import pandas as pd







def load_history(days=10):







    conn = sqlite3.connect(DB_PATH)







    query = """







        SELECT *



        FROM hot_sector



        ORDER BY date DESC, rank ASC







    """







    df = pd.read_sql(query, conn)







    conn.close()







    return df











# =========================================================



# 鏉垮潡鍒嗘瀽缁撴灉缂撳瓨璺緞



# =========================================================



SECTOR_RESULT_CACHE = os.path.join(CACHE_DIR, f"sector_analysis_{TRADE_DATE}.pkl")







# =========================================================



# 涓诲嚱鏁帮紙V4 + V5铻嶅悎条



# =========================================================



def analyze_hot_sectors():



    """



    鍒嗘瀽涓荤嚎鏉垮潡寮哄害



    缂撳瓨绛栫暐锛氭寜浜ゆ槗鏃ョ紦瀛橈紝鍚屼竴澶╁唴澶氭璋冪敤鐩存帴杩斿洖缂撳瓨缁撴灉







    杩斿洖条琛屼笟+姒傚康鍚堝苟缁撴灉 df, 涓婚鍙屽洜瀛愯瘎条theme_df)



    鑻ョ紦瀛樹负鏃х増锛堝彧淇濆瓨条df 鍗曞€硷級锛岃嚜鍔ㄥ垹闄ゅ苟閲嶇畻条



    """



    if os.path.exists(SECTOR_RESULT_CACHE):



        print(f"璇诲彇鏉垮潡鍒嗘瀽缂撳瓨: {SECTOR_RESULT_CACHE}")



        try:



            with open(SECTOR_RESULT_CACHE, "rb") as f:



                cached = pickle.load(f)



            # 鏍￠獙缂撳瓨鏍煎紡锛氬繀椤绘槸 (df, theme_df) 浜屽厓条



            if (



                isinstance(cached, tuple)



                and len(cached) == 2



                and isinstance(cached[0], pd.DataFrame)



                and isinstance(cached[1], pd.DataFrame)



            ):



                return cached



            else:



                print(f"  -> 缂撳瓨涓烘棫鐗堟牸寮忥紙鍗曞€硷級锛屽垹闄ゅ苟閲嶆柊璁＄畻")



                try:



                    os.remove(SECTOR_RESULT_CACHE)



                except Exception:



                    pass



        except Exception as e:



            print(f"  -> 缂撳瓨璇诲彇澶辫触: {e}锛岄噸鏂拌条)



"
            try:



                os.remove(SECTOR_RESULT_CACHE)



            except Exception:



                pass







    print("\n=== 涓荤嚎绯荤粺 V4 + V5 ===\n")







    init_sector_state(days=10)







    daily_df = get_daily_df()







    name_map = get_stock_name_map()



    



    daily_df = daily_df.merge(



        name_map,



        on="ts_code",



        how="left"



)







    stock_map, concept_map = init_concept_cache()







    stock_map = load_stock_concept_map()







    concept_df = build_concept_df(stock_map)







    industry_df = get_sw_industry_map()







    industry_df = industry_df.merge(



         concept_df,



         on="ts_code",



         how="left"



    )







    industry_res = analyze_industry(daily_df, industry_df)







    stock_concept_list = {k: v.split(";") for k, v in stock_map.items()}







    theme_res = analyze_themes(daily_df, industry_df, stock_concept_list)



    



    concept_res = analyze_concepts(daily_df)







    # 琛屼笟 + 姒傚康 鍚堝苟



    all_res = industry_res + concept_res







    print(f"琛屼笟{len(industry_res)} + 姒傚康{len(concept_res)} = {len(all_res)}")







    # 涓婚鍙屽洜瀛愮嫭绔嬭瘎鍒嗭紙鎯呯华条+ 瓒嬪娍条+ 缁煎悎鍒嗭級



    theme_df = analyze_themes_dual_factor(daily_df, industry_df, stock_concept_list)







    # 鎵撳嵃涓婚鍙屽洜瀛愯瘎鍒嗙粨条



    if not theme_df.empty:



        print("\n=== 涓婚鍙屽洜瀛愯瘎条===")



        print(theme_df.to_string(index=False))







    theme_sorted = sorted(theme_res, key=lambda x: x.get("涓荤嚎寮哄害", 0), reverse=True)



    print("\n涓婚鏉垮潡寮哄害鎺掑悕:")



    for t in theme_sorted[:5]:



        print(f"  {t['涓荤嚎']:16s} 寮哄害={t['涓荤嚎寮哄害']:.1f} 璇勫垎={t['璇勫垎']:.1f} 鎴愬垎条{t['鎴愬垎鑲℃暟']}")







    if not all_res:



        return pd.DataFrame(), pd.DataFrame()







    df = pd.DataFrame(all_res)







    df = df.sort_values(



        "涓荤嚎寮哄害",



        ascending=False



    )







    df.reset_index(drop=True, inplace=True)







    init_db()



    save_top20(df)







    with open(SECTOR_RESULT_CACHE, "wb") as f:



        pickle.dump(df, f)



    print(f"鏉垮潡鍒嗘瀽缁撴灉宸茬紦条 {SECTOR_RESULT_CACHE}")







    # 杩斿洖琛屼笟+姒傚康鍚堝苟缁撴灉锛屼互鍙婁富棰樺弻鍥犲瓙璇勫垎缁撴灉



    return df, theme_df







# =========================================================



# 杩愯



# =========================================================



if __name__ == "__main__":







    df, theme_df = analyze_hot_sectors()







    print("\n=== 琛屼笟+姒傚康鍚堝苟缁撴灉锛圱op20条===")



    print(df.head(20))







    print("\n=== 涓婚鍙屽洜瀛愯瘎鍒嗭紙Top20条===")



    if not theme_df.empty:



        print(theme_df.head(20))



















    # -------------------------------------------------



    # 鍚堝苟杩涜涓氳〃



    # -------------------------------------------------



    # industry_df = industry_df.merge(



    #     concept_df,



    #     on="ts_code",



    #     how="left"



    # )







    







