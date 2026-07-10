# -*- coding: utf-8 -*-
"""
探索个股研报获取渠道
"""
import subprocess, json, datetime, os, sys

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

SKILL_DIR = r"C:\Users\kongx\.qclaw\skills\tongdaxin-mcp"

def get_token():
    rc, out = ps_run('& "%s\\get-token.ps1"' % SKILL_DIR, timeout=15)
    return out.strip() if rc == 0 else None

def ensure_mcp():
    token = get_token()
    if not token:
        return None
    rc, out = ps_run("mcporter config get tdx-finance_qclaw 2>$null")
    if rc == 0 and token in out:
        return token
    ps_run("mcporter config remove tdx-finance_qclaw 2>$null")
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)
    return token

def mcp_raw(tool, **kwargs):
    args = " ".join(["%s='%s'" % (k,v) for k,v in kwargs.items()])
    ps1 = os.path.join(os.environ.get("TEMP","C:\\temp"), "ex.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s; exit $LASTEXITCODE\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=60)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None


def test_eastmoney_reports():
    """东方财富个股研报 API"""
    print("\n=== 东方财富个股研报 API ===")

    codes = [
        ("603906", "龙蟠科技"),
        ("600584", "长电科技"),
    ]

    for code, name in codes:
        # 方式1: datacenter API
        url1 = (
            "https://datacenter.eastmoney.com/securities/api/data/v1/get"
            "?reportName=RPT_STOCK_RESEARCH"
            "&columns=SECURITY_CODE,REPORT_DATE,RESEARCH_TYPE,INSTITUTION_NAME,TITLE,SECURITY_NAME_ABBR"
            "&filter=(SECURITY_CODE%3D%22" + code + "%22)"
            "&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=REPORT_DATE"
            "&source=DataCenter&client=PC"
        )

        r = subprocess.run(
            ["powershell", "-Command", "irm '%s' -TimeoutSec 15" % url1],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20
        )
        if r.returncode == 0 and r.stdout.strip():
            try:
                data = json.loads(r.stdout)
                result = data.get("result", {})
                if result:
                    items = result.get("data", [])
                    print("  [%s] datacenter API: %d 条" % (name, len(items)))
                    for item in items[:3]:
                        print("    - %s | %s | %s" % (
                            str(item.get("REPORT_DATE",""))[:10],
                            item.get("INSTITUTION_NAME",""),
                            item.get("TITLE","")[:50]
                        ))
                else:
                    print("  [%s] datacenter API: 无数据" % name)
            except Exception as e:
                print("  [%s] datacenter 解析失败: %s" % (name, e))
                print("    raw: %s" % r.stdout[:200])

        # 方式2: reportapi
        url2 = (
            "https://reportapi.eastmoney.com/report/list?"
            "cb=datatable&industryCode=*&pageSize=5&pageNum=1"
            "&code=" + code + "&endDate=&startDate=&columnCode=*&pageNo=1&qType=0"
        )

        r2 = subprocess.run(
            ["powershell", "-Command", "irm '%s' -TimeoutSec 15" % url2],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20
        )
        if r2.returncode == 0:
            text = r2.stdout.strip()
            try:
                if text.startswith("datatable("):
                    text = text[len("datatable("):-1]
                data2 = json.loads(text)
                items2 = data2.get("data", []) or []
                print("  [%s] reportapi: %d 条" % (name, len(items2)))
                for item in items2[:3]:
                    print("    - %s | %s | %s" % (
                        str(item.get("publishDate",""))[:10],
                        item.get("orgName",""),
                        item.get("title","")[:50]
                    ))
            except Exception as e:
                print("  [%s] reportapi 解析失败: %s" % (name, e))


def test_tdx_mcp():
    """测试 TDX MCP 各工具"""
    print("\n=== TDX MCP 研报工具 ===")
    token = ensure_mcp()
    if not token:
        print("Token获取失败")
        return

    tests = [
        ("wenda_report_query", {"query": "龙蟠科技|20260601|20260710|", "pageSize": "3"}),
        ("tdx_report_query", {"code": "603906", "count": "3"}),
        ("gg_search", {"query": "龙蟠科技 研究报告 个股", "count": "5"}),
    ]

    for tool, params in tests:
        print("\n  [%s]" % tool)
        resp = mcp_raw(tool, **params)
        if resp:
            print("    raw keys: %s" % list(resp.keys()) if isinstance(resp, dict) else "type=%s" % type(resp))
            if isinstance(resp, dict):
                data = resp.get("data", resp.get("result", resp))
                if isinstance(data, list):
                    for item in data[:3]:
                        if isinstance(item, list):
                            print("    - %s" % str(item[0])[:80])
                        elif isinstance(item, dict):
                            print("    - %s" % item)
                else:
                    print("    data: %s" % str(data)[:200])
            else:
                print("    %s" % str(resp)[:300])
        else:
            print("    无返回")


def test_tushare():
    """Tushare 研报接口"""
    print("\n=== Tushare 研报接口 ===")
    try:
        import tushare as ts
        pro = ts.pro_api("1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34")
        # 研报
        try:
            df = pro.ths_news(art_type='report', start_date='20260601', end_date='20260710', limit=5)
            print("  ths_news(report): %d 条" % len(df))
            print(df.head(3).to_string())
        except Exception as e:
            print("  ths_news 失败: %s" % e)

        try:
            df2 = pro.ths_news(art_type='research', start_date='20260601', end_date='20260710', limit=5)
            print("\n  ths_news(research): %d 条" % len(df2))
        except:
            pass

        try:
            # 东方财富研报
            df3 = pro.news_notice(symbol='603906', start_date='20260601', end_date='20260710')
            print("\n  news_notice: %d 条" % len(df3))
        except Exception as e:
            print("  news_notice 失败: %s" % e)

    except Exception as e:
        print("  Tushare 导入/调用失败: %s" % e)


if __name__ == "__main__":
    print("=" * 60)
    print("个股研报渠道探索")
    print("=" * 60)
    test_eastmoney_reports()
    test_tdx_mcp()
    test_tushare()
    print("\n完成!")
