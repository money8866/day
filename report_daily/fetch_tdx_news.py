# -*- coding: utf-8 -*-
"""
TDX MCP 公告+研报抓取脚本 v5
使用通达信 MCP 的 wenda_notice_query 和 wenda_report_query 工具
关键发现: wenda_notice_query 用股票代码搜索才能命中本公司公告
  - 搜"龙蟠科技" → 返回他公司提及该名的公告（噪音）
  - 搜"603906" → 返回龙蟠科技自己发布的公告（正确）
策略: 优先用代码搜索，代码搜不到时用公司名兜底
"""
import subprocess, json, datetime, os, re

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"


def ps_run(cmd, timeout=60):
    r = subprocess.run(
        ["powershell", "-Command", cmd],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return r.returncode, r.stdout


def get_token():
    rc, out = ps_run(f'& "{SKILL_DIR}\\\\get-token.ps1"', timeout=15)
    return out.strip() if rc == 0 else None


def ensure_mcp_config(token):
    rc, out = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
    if rc == 0 and token in out:
        return
    ps_run("mcporter config remove tdx-finance_qclaw 2>$null")
    ps_run(
        f'mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" '
        f'--header "Authorization=Bearer {token}" '
        f'--header "Accept=application/json, text/event-stream" '
        f'--transport http --scope home'
    )


def mcp_call(tool, timeout=60, **kwargs):
    """调用 TDX MCP 工具，写 ps1 文件绕过 PowerShell 管道符转义问题"""
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for prefix in ("[mcporter]", "[ERROR]", "[WARN]"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        try:
            return json.loads(text)
        except:
            return None


def extract_stocks():
    """从 latest final*.md 文件提取股票代码"""
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
    """通过 tdx_lookup_stock 获取股票名称"""
    resp = mcp_call("tdx_lookup_stock", query=code, range="AG")
    if not isinstance(resp, list):
        return ""
    for item in resp:
        if isinstance(item, dict) and item.get("code") == code:
            return item.get("name", "")
    return ""


def clean_text(text):
    """清理 HTML 标签"""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', "", text)
    for entity in ("&nbsp;", "&amp;", "&lt;", "&gt;", "&#39;", "&quot;"):
        text = text.replace(entity, " ")
    return re.sub(r'\s+', " ", text).strip()


def classify_title(title):
    """关键词分类"""
    good = ["回购", "增持", "中标", "订单", "业绩", "分红", "突破", "研发", "获奖",
            "增长", "盈利", "净利润", "营收", "超预期", "国产替代", "量产", "认证", "批准"]
    bad = ["减持", "风险", "亏损", "诉讼", "处罚", "整改", "警示", "立案",
           "终止", "取消", "下滑", "警告", "ST", "立案调查"]
    if any(kw in title for kw in bad):
        return "利空"
    if any(kw in title for kw in good):
        return "利好"
    return "中性"


def is_target_stock_notice(item, code):
    """判断是否该股票自己发布的公告（内容含证券代码：code）"""
    if len(item) < 5:
        return False
    content = (item[4] or "") + (item[0] or "")
    codes = re.findall(r"证券代码[：:]\s*(\d{6})", content)
    return bool(codes) and code in codes


def is_this_stock_notice(item, code, name):
    """判断公告是否与该股票相关（自身发布 OR 代码匹配 OR 标题含公司名）"""
    if len(item) < 5:
        return False
    title = item[0] or ""
    if is_target_stock_notice(item, code):
        return True
    if name in title:
        return True
    return False


def fetch_notices_for_stock(code, name, days=90):
    """抓取公告 - 优先用代码搜索，其次用公司名兜底"""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    items = []

    # 方式1: 用股票代码搜索（命中率最高）
    query_code = f"{code}|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|"
    resp = mcp_call("wenda_notice_query", query=query_code, pageSize="5")
    if resp and isinstance(resp, dict):
        data = resp.get("data", [])
        if isinstance(data, list) and len(data) > 1:
            for item in data[1:]:
                if isinstance(item, list) and len(item) >= 5:
                    if is_this_stock_notice(item, code, name):
                        items.append(item)

    # 方式2: 用公司名兜底
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
    """抓取研报"""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    query = f"{name}|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|"
    resp = mcp_call("wenda_report_query", query=query, pageSize="3")
    if not resp or not isinstance(resp, dict):
        return []
    data = resp.get("data", [])
    if not isinstance(data, list) or len(data) <= 1:
        return []
    results = []
    for item in data[1:4]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        title = clean_text(item[0])
        report_date = str(item[1])[:10] if item[1] else ""
        results.append({
            "type": "研报",
            "code": code,
            "name": name,
            "title": title,
            "date": report_date,
            "label": classify_title(title),
            "summary": "",
            "is_direct": True
        })
    return results


def generate_report(items, today_str):
    """生成 Markdown 报告"""
    good = [x for x in items if x.get("label") == "利好"]
    bad = [x for x in items if x.get("label") == "利空"]
    neutral = [x for x in items if x.get("label") == "中性"]
    codes_with_data = set(x["code"] for x in items)

    lines = [
        "# 公告与研报每日速递",
        f"\n**日期**: {today_str} | **监测股票**: {len(codes_with_data)} 只 | **总条目**: {len(items)} 条",
        "\n---\n## 概要",
        "\n| 类型 | 数量 |",
        "|------|------|",
        f"| 利好 | **{len(good)}** |",
        f"| 利空 | **{len(bad)}** |",
        f"| 中性 | **{len(neutral)}** |",
        "\n---\n## 利好信息\n"
    ]

    if not good:
        lines.append("\n暂无利好公告\n")
    for item in good[:10]:
        marker = "" if item.get("is_direct") else " [关联公告]"
        lines.append(f"\n### [GOOD] {item['code']} {item['name']} [{item['date']}]{marker}\n\n**{item['title']}**\n")
        if item.get("summary"):
            lines.append(f"*{item['summary'][:100]}...*\n")

    lines.append("\n---\n## 利空信息\n")
    if not bad:
        lines.append("\n暂无利空公告\n")
    for item in bad[:10]:
        marker = "" if item.get("is_direct") else " [关联公告]"
        lines.append(f"\n### [BAD] {item['code']} {item['name']} [{item['date']}]{marker}\n\n**{item['title']}**\n")
        if item.get("summary"):
            lines.append(f"*{item['summary'][:100]}...*\n")

    lines.append("\n---\n## 中性信息\n")
    if not neutral:
        lines.append("\n暂无中性公告\n")
    for item in neutral[:20]:
        marker = "" if item.get("is_direct") else " [关联公告]"
        lines.append(f"- **{item['code']} {item['name']}** [{item['type']} {item['date']}]{marker}: {item['title']}\n")

    lines.append(f"\n---\n*数据来源: 通达信MCP | 生成时间: {today_str} 00:01*\n")

    output_path = os.path.join(os.path.dirname(__file__), f"announcement_analysis_{today_str.replace('-','').replace('年','').replace('月','').replace('日','')}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已生成: {output_path}")
    return output_path, len(good), len(bad), len(neutral)


def main():
    print("=" * 50)
    print("TDX MCP 公告研报抓取 v5")
    print("=" * 50)

    print("\n[1/5] 初始化 TDX MCP...")
    token = get_token()
    if not token:
        print("  Token 获取失败，退出")
        return
    ensure_mcp_config(token)
    print("MCP 配置完成")

    print("\n[2/5] 提取股票列表...")
    stocks = extract_stocks()

    print("\n[3/5] 获取股票名称...")
    name_map = {}
    for code, _ in stocks:
        name = get_stock_name(code)
        if name:
            name_map[code] = name
            print(f"  {code} -> {name}")
        else:
            print(f"  {code} -> (未知)")
            name_map[code] = ""

    print(f"\n[4/5] 抓取 {len(stocks)} 只股票的公告和研报...")
    all_items = []
    for i, (code, _) in enumerate(stocks, 1):
        name = name_map.get(code, "")
        if not name:
            continue
        try:
            notices = fetch_notices_for_stock(code, name, days=90)
        except Exception as e:
            notices = []
            print(f"  [{i}/{len(stocks)}] {code} {name}: 公告超时({e}), 跳过")
        try:
            reports = fetch_reports_for_stock(code, name, days=60)
        except Exception as e:
            reports = []
            print(f"  [{i}/{len(stocks)}] {code} {name}: 研报超时({e}), 跳过")
        all_items.extend(notices)
        all_items.extend(reports)
        print(f"  [{i}/{len(stocks)}] {code} {name}: {len(notices)} 公告, {len(reports)} 研报")

    print("\n[5/5] 生成报告...")
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    path, g, b, n = generate_report(all_items, today_str)
    print(f"利好: {g} 条 | 利空: {b} 条 | 中性: {n} 条")
    print("\n完成!")


if __name__ == "__main__":
    main()
