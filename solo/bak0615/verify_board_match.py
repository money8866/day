"""快速验证：东财概念板块能否覆盖 theme2.json 的 industry/concept 名称"""
import requests, time, json

BASE_URL = "http://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
UT = "bd1d9ddb04089700cf9c27f6f7426281"

def fetch_board_list(fs, page_size=5000):
    """拉东财板块列表，返回 {板块名: 板块代码(BKxxxx)}"""
    params = {
        "pn": 1, "pz": page_size, "po": 1, "np": 1,
        "ut": UT, "fltt": 2, "invt": 2, "fid": "f62",
        "fs": fs, "fields": "f12,f14", "_": int(time.time()*1000)
    }
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    d = r.json()
    diff = d.get("data", {}).get("diff") or []
    return {x["f14"]: x["f12"] for x in diff}

# 概念板块 (m:90+t:2)
concept_map = fetch_board_list("m:90+t:2")
print(f"概念板块总数: {len(concept_map)}")

# 加载 theme2.json 中的所有 industry/concept
with open("theme2.json", "r", encoding="utf-8") as f:
    theme2 = json.load(f)
cats = theme2["CATEGORIES"]

all_industries = set()
all_concepts = set()
for cat in cats.values():
    for tname, tcfg in cat.get("themes", {}).items():
        for x in tcfg.get("industry", []) or []:
            all_industries.add(x)
        for x in tcfg.get("concept", []) or []:
            all_concepts.add(x)

def match_board(name, mapping):
    if name in mapping: return mapping[name]
    clean = name.replace("Ⅱ","").strip()
    if clean in mapping: return mapping[clean]
    # 简单包含匹配
    for k, v in mapping.items():
        if name.startswith(k[:4]) or k.startswith(name[:4]):
            return f"{v}(?{k})"
    return None

print("\n--- industry 命中情况 ---")
missed_inds = []
for ind in sorted(all_industries):
    hit = match_board(ind, concept_map)
    status = "✓" if hit and "?" not in hit else ("~" if hit else "✗")
    print(f"  {status} {ind:20s} -> {hit}")
    if not hit: missed_inds.append(ind)

print("\n--- concept 命中情况 ---")
for conc in sorted(all_concepts):
    hit = match_board(conc, concept_map)
    status = "✓" if hit and "?" not in hit else ("~" if hit else "✗")
    print(f"  {status} {conc:20s} -> {hit}")

print(f"\n未命中的 industry: {missed_inds}")
print(f"概念板块总数: {len(concept_map)}")
