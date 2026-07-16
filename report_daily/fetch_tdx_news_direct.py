# -*- coding: utf-8 -*-
"""
TDX MCP 公告+研报抓取 - 直接使用缓存token
"""
import subprocess, json, datetime, os, re, urllib.request, urllib.parse

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"

def ps_run(cmd, timeout=60):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return r.returncode, r.stdout

def get_cached_token():
    """从 mcporter config 读取缓存token"""
    rc, out = ps_run("mcporter config get tdx-finance_qclaw")
    if rc != 0:
        return None
    for line in out.splitlines():
        if "Authorization: Bearer" in line:
            return line.split("Authorization: Bearer")[1].strip()
    return None

def mcp_call(tool, timeout=60, **kwargs):
    """直接HTTP调用TDX MCP"""
    args_parts = [f'{k}=\'{v}\'' for k, v in kwargs.items()]
    args_line = " ".join(args_parts)
    ps1 = os.path.join(os.environ.get("TEMP", "C:\\temp"), f"tdx_{os.getpid()}_{id(kwargs)}.ps1")
    try:
        with open(ps1, "w", encoding="utf-8") as f:
            f.write(f"mcporter call tdx-finance_qclaw.{tool} {args_line}; exit $LASTEXITCODE\n")
        rc, out = ps_run(f"& \"{ps1}\"", timeout=timeout)
    finally:
        try:
            os.remove(ps1)
        except:
            pass
    if rc != 0 or not out:
        return None
    text = out.strip()
    for prefix in ("[mcporter]", "[ERROR]", "[WARN]"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    try:
        return json.loads(text)
    except:
        return None

def extract_stocks():
    folder = r"D:\mystock\report_daily"
    files = [f for f in os.listdir(folder) if f.startswith("Final_Self_") and f.endswith(".md")]
    if not files:
        return []
    latest = max(files, key=lambda x: os.path.getmtime(os.path.join(folder, x)))
    with open(os.path.join(folder, latest), "r", encoding="utf-8") as f:
        content = f.read()
    result = {}
    for m in re.findall(r'\b(60\d{4})\.SH\b', content):
        result[m] = "1"
    for m in re.findall(r'\b(00\d{4}|30\d{4}|43\d{4})\.SZ\b', content):
        result[m] = "0"
    for code in re.findall(r'\b(\d{6})\b', content):
        if code.startswith(("60", "68", "00", "30", "43")) and code not in result:
            result[code] = "1" if code.startswith(("60", "68")) else "0"
    final = [(code, result[code]) for code in result]
    print(f"从 {latest} 提取到 {len(final)} 只股票")
    return final[:30]

def get_stock_name(code):
    resp = mcp_call("tdx_lookup_stock", query=code, range="AG")
    if not isinstance(resp, list):
        return ""
    for item in resp:
        if isinstance(item, dict) and item.get("code") == code:
            return item.get("name", "")
    return ""

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', "", text)
    for entity in ("&nbsp;", "&amp;", "&lt;", "&gt;", "&#39;", "&quot;"):
        text = text.replace(entity, " ")
    return re.sub(r'\s+', " ", text).strip()

def classify_title(title):
    good = ["回购", "增持", "中标", "订单", "业绩", "分红", "突破", "研发", "获奖",
            "增长", "盈利", "净利润", "营收", "超预期", "国产替代", "量产", "认证", "批准"]
    bad = ["减持", "风险", "亏损", "诉讼", "处罚", "整改", "警示", "ST", "立案调查",
           "终止", "取消", "下滑", "警告", "暂停上市"]
    if any(kw in title for kw in bad):
        return "利空"
    if any(kw in title for kw in good):
        return "利好"
    return "中性"

def is_target_stock_notice(item, code):
    if len(item) < 5:
        return False
    content = (item[4] or "") + (item[0] or "")
    codes = re.findall(r"证券代码[：:]\s*(\d{6})", content)
    return bool(codes) and code in codes

def is_this_stock_notice(item, code, name):
    if len(item) < 5:
        return False
    title = item[0] or ""
    if is_target_stock_notice(item, code):
        return True
    if name in title:
        return True
    return False

def fetch_notices_for_stock(code, name, days=90):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    items = []
    query_code = f"{code}|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|"
    resp = mcp_call("wenda_notice_query", query=query_code, pageSize="5")
    if resp and isinstance(resp, dict):
        data = resp.get("data", [])
        if isinstance(data, list) and len(data) > 1:
            for item in data[1:]:
                if isinstance(item, list) and len(item) >= 5:
                    if is_this_stock_notice(item, code, name):
                        items.append(item)
    if not items:
        query_name = f"{name}|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|"
        resp2 = mcp_call("wenda_notice_query", query=query_name, pageSize="5")
        if resp2 and isinstance(resp2, dict):
            data2 = resp2.get("data", [])
            if isinstance(data2, list) and len(data2) > 1:
                for item in data2[1:]:
                    if isinstance(item, list) and len(item) >= 5:
                        if is_this_stock_notice(item, code, name):
                            items.append(item)
    results = []
    for item in items[:5]:
        title = clean_text(item[0])
        notice_date = str(item[1])[:10] if item[1] else ""
        summary = clean_text(item[4])[:200]
        is_direct = is_target_stock_notice(item, code)
        results.append({
            "type": "公告",
            "code": code,
            "name": name,
            "title": title,
            "date": notice_date,
            "label": classify_title(title),
            "summary": summary,
            "is_direct": is_direct
        })
    return results

def fetch_reports_for_stock(code, name, days=60):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    query = f"{code}|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|"
    resp = mcp_call("wenda_report_query", query=query, pageSize="5")
    results = []
    if resp and isinstance(resp, dict):
        data = resp.get("data", [])
        if isinstance(data, list) and len(data) > 1:
            for item in data[1:]:
                if isinstance(item, list) and len(item) >= 5:
                    title = clean_text(item[0])
                    report_date = str(item[1])[:10] if item[1] else ""
                    summary = clean_text(item[4])[:200]
                    results.append({
                        "type": "研报",
                        "code": code,
                        "name": name,
                        "title": title,
                        "date": report_date,
                        "label": "中性",
                        "summary": summary,
                        "is_direct": True
                    })
    return results[:5]

def generate_markdown(stocks, all_items, output_file):
    today = datetime.datetime.now().strftime('%Y年%m月%d日')
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# 公告与研报速递 - {today}\n\n")
        f.write(f"共扫描 {len(stocks)} 只股票，收集到 {len(all_items)} 条公告/研报\n\n")
        # 按标签分组
        labels = {"利好": [], "中性": [], "利空": []}
        for item in all_items:
            labels[item["label"]].append(item)
        for label in ["利好", "中性", "利空"]:
            items = labels[label]
            if not items:
                continue
            f.write(f"## {label}（{len(items)}条）\n\n")
            for item in items:
                emoji = "📈" if label == "利好" else ("📉" if label == "利空" else "📊")
                direct_tag = "[本公司发布]" if item.get("is_direct") else ""
                f.write(f"- **{item['name']}({item['code']})** {emoji} {direct_tag}\n")
                f.write(f"  - 标题：{item['title']}\n")
                f.write(f"  - 日期：{item['date']}\n")
                if item["summary"]:
                    f.write(f"  - 摘要：{item['summary'][:150]}...\n")
                f.write("\n")
        f.write("---\n*由 QClaw 自动生成*\n")
    print(f"Markdown 已生成: {output_file}")

def main():
    print("=" * 50)
    print("TDX MCP 公告研报抓取 v5 (direct token)")
    print("=" * 50)
    
    token = get_cached_token()
    if not token:
        print("  无法获取 TDX MCP token，退出")
        return
    
    stocks = extract_stocks()
    if not stocks:
        print("  未找到股票列表，退出")
        return
    
    all_items = []
    for code, market in stocks:
        name = get_stock_name(code)
        if not name:
            name = code
        notices = fetch_notices_for_stock(code, name)
        reports = fetch_reports_for_stock(code, name)
        for item in notices + reports:
            if item not in all_items:
                all_items.append(item)
        print(f"  {name}({code}): {len(notices)}公告 {len(reports)}研报")
    
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    output_file = rf"D:\mystock\report_daily\announcement_analysis_{today_str}.md"
    generate_markdown(stocks, all_items, output_file)
    print(f"\n完成! 共 {len(all_items)} 条记录")

if __name__ == '__main__':
    main()
