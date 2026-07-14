# -*- coding: utf-8 -*-
"""
实时主题+个股预警系统 v2
数据源策略:
  - 实时行情: 新浪财经批量接口 (<1秒获取100+只)
  - 历史K线/公告: TDX MCP (通达信MCP)
  - 基本面数据: Tushare

功能:
1. 新浪批量获取实时行情（股票+指数，毫秒级）
2. 主题强度分析（成分股涨幅排序）
3. 个股异动检测（放量/急涨/涨停预警）
4. 持仓止损/止盈监控
5. 市场情绪评分（趋势+情绪）
6. 微信推送预警 (Server酱)

运行: python realtime_monitor_tdx.py
依赖: requests, tushare, mcporter (TDX MCP)
"""
import os, sys, time, json, re, subprocess, threading
from datetime import datetime, timedelta
from collections import defaultdict, deque

# ── 环境配置 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

try:
    from dotenv import load_dotenv
    for _p in [os.path.join(BASE_DIR, '.env'),
               os.path.join(BASE_DIR, '..', 'config', '.env'),
               r'D:\mystock\config\.env']:
        if os.path.exists(_p):
            load_dotenv(_p)
            break
except: pass

# ═══════════════════════════════════════════════
# TDX MCP (仅用于K线/公告等非实时数据)
# ═══════════════════════════════════════════════
def _parse_tdx_json(raw_text):
    """从mcporter stdout提取并修复JSON（末尾不完整字段）"""
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start < 0 or end <= start:
        return None
    text = raw_text[start:end+1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = text.split('\n')
        for i in range(len(lines)-1, -1, -1):
            ls = lines[i].strip()
            if ls.endswith('},') or ls.endswith('}') or ls.endswith('],') or ls.endswith(']'):
                break
            if re.match(r'^\s*"[^"]+"\s*:', ls) and not re.search(r':\s*[0-9"{]', ls):
                lines[i] = ''
            elif ls == '"' or ls == '':
                lines[i] = ''
        text2 = '\n'.join(lines).rstrip().rstrip(',').rstrip() + '\n}'
        try:
            return json.loads(text2)
        except:
            return None

def tdx_mcp(tool, **params):
    """调用通达信MCP (仅用于K线/历史数据/公告)"""
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mcp_rtm.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    try:
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1],
            capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace'
        )
        try: os.remove(ps1)
        except: pass
        if result.returncode != 0:
            return None
        return _parse_tdx_json(result.stdout)
    except Exception:
        try: os.remove(ps1)
        except: pass
        return None

# ═══════════════════════════════════════════════
# 新浪财经批量行情（毫秒级，所有实时数据走这里）
# ═══════════════════════════════════════════════
import requests as _req

_SINA_HEADERS = {
    'Referer': 'https://finance.sina.com.cn',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

# 新浪指数代码映射
_SINA_INDEX_CODES = {
    '上证指数': 'sh000001',
    '深证成指': 'sz399001',
    '创业板指': 'sz399006',
    '沪深300': 'sh000300',
    '中证500': 'sh000905',
    '科创50': 'sh000688',
}

def fetch_sina_quotes(codes_or_list):
    """通过新浪财经批量接口获取实时行情
    输入: ['000001.SH', '159516.SZ', ...] 或 ['sh000001', 'sz399001', ...]
    返回: {
        '000001.SH': {price, pct_chg, high, low, vol, amount, prev_close, name},
        '上证指数': {price, pct_chg, ...},
        ...
    }
    """
    if not codes_or_list:
        return {}

    # 转换格式
    sina_list = []
    code_map = {}
    for item in codes_or_list:
        # 判断是否已经是sina格式
        if item.startswith('sh') or item.startswith('sz'):
            sc = item
            # 映射回标准格式
            if sc.startswith('sh'):
                ts = sc[2:].zfill(6) + '.SH'
            else:
                ts = sc[2:].zfill(6) + '.SZ'
            code_map[sc] = ts
            sina_list.append(sc)
        else:
            # 标准格式
            if item.endswith('.SH'):
                sc = 'sh' + item.replace('.SH', '')
                ts = item
            elif item.endswith('.SZ'):
                sc = 'sz' + item.replace('.SZ', '')
                ts = item
            else:
                continue
            code_map[sc] = ts
            sina_list.append(sc)

    # 加入指数
    idx_codes = []
    for idx_name, idx_sina in _SINA_INDEX_CODES.items():
        if idx_sina not in sina_list:
            sina_list.append(idx_sina)
            code_map[idx_sina] = idx_name
        idx_codes.append(idx_sina)

    result = {}
    for offset in range(0, len(sina_list), 200):
        batch = sina_list[offset:offset+200]
        url = 'https://hq.sinajs.cn/list=' + ','.join(batch)
        try:
            resp = _req.get(url, headers=_SINA_HEADERS, timeout=5)
            resp.encoding = 'gbk'
            for line in resp.text.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line:
                    continue
                try:
                    var_part = line.split('=', 1)[1]
                    if var_part.count('"') < 2:
                        continue
                    fields = var_part.split('"')[1].split(',')
                    if len(fields) < 32:
                        continue
                    var_name = line.split('hq_str_')[1].split('=')[0]
                    ts_code = code_map.get(var_name, var_name)

                    prev_close = float(fields[2])
                    price = float(fields[3])
                    pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                    result[ts_code] = {
                        'price': price,
                        'pct_chg': round(pct, 2),
                        'high': float(fields[4]),
                        'low': float(fields[5]),
                        'open': float(fields[1]),
                        'vol': int(fields[8]),
                        'amount': float(fields[9]),
                        'prev_close': prev_close,
                        'name': fields[0],
                    }
                except (IndexError, ValueError):
                    continue
        except Exception as e:
            print('[Sina batch error]', e)

    return result

# ═══════════════════════════════════════════════
# Tushare (基本面/历史K线)
# ═══════════════════════════════════════════════
try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN', '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'))
    TS_AVAILABLE = True
except:
    TS_AVAILABLE = False

# ═══════════════════════════════════════════════
# 主题配置（可从 theme_portfolio.db 加载）
# ═══════════════════════════════════════════════
DEFAULT_THEMES = {
    '医药产业链': ['688710.SH', '301507.SZ', '002550.SZ'],
    '半导体制造': ['688584.SH', '688036.SH', '688012.SH'],
    '半导体材料': ['688126.SH', '688396.SH', '688019.SH'],
    '创新药': ['688266.SH', '688578.SH', '002821.SZ'],
    '券商': ['601162.SH', '601555.SH', '600958.SH'],
}


# ═══════════════════════════════════════════════
# 主监控类
# ═══════════════════════════════════════════════
class RealtimeMonitor:
    def __init__(self):
        self.quotes = {}           # code -> {price, pct_chg, high, low, vol, amount, prev_close, name}
        self.prev_quotes = {}      # 上一轮快照（用于判断加速）
        self.index_quotes = {}     # 指数 {name -> q}
        self.theme_stocks = DEFAULT_THEMES.copy()
        self.stock_themes = {}    # code -> [theme_name, ...]
        self.positions = {}        # 持仓

        # 历史（平滑）
        self.score_history = deque(maxlen=30)

        # 冷却（秒）
        self.cooldown = {
            'theme_surge': 900,    # 主题异动 15分钟
            'stock_surge': 300,    # 个股急涨 5分钟
            'limit_up': 600,       # 涨停预警 10分钟
            'stop_loss': 3600,     # 止损 1小时
            'take_profit': 3600,   # 止盈 1小时
            'market': 1800,        # 市场情绪 30分钟
        }
        self.last_alert = defaultdict(float)

        # 持仓初始化（硬编码 + DB加载）
        self.positions = {
            '159516.SZ': {'name': '半导体设备ETF', 'entry': 1.142, 'qty': 10000},
        }
        self._load_positions()
        self._init_stock_themes()

    def _init_stock_themes(self):
        """构建股票→主题映射"""
        for theme, stocks in self.theme_stocks.items():
            for code in stocks:
                if code not in self.stock_themes:
                    self.stock_themes[code] = []
                if theme not in self.stock_themes[code]:
                    self.stock_themes[code].append(theme)

    def _load_positions(self):
        """从数据库加载持仓"""
        db_path = r'C:\Users\kongx\.qclaw\workspace\mystock-reports\etf_result.db'
        if os.path.exists(db_path):
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("SELECT code, name, entry_price FROM portfolio WHERE status='holding'")
                for row in cur.fetchall():
                    code, name, entry = row
                    self.positions[code] = {'name': name, 'entry': float(entry) if entry else 0, 'qty': 10000}
                conn.close()
            except: pass

    # ═══════════════════════════════════════
    # 1. 行情获取
    # ═══════════════════════════════════════
    def fetch_all(self):
        """获取所有行情（新浪批量，<1秒）"""
        all_codes = list(self.stock_themes.keys()) + list(self.positions.keys())
        all_codes = list(dict.fromkeys(all_codes))  # 去重

        self.prev_quotes = self.quotes.copy()
        all_quotes = fetch_sina_quotes(all_codes)

        # 分离指数和个股
        self.index_quotes = {}
        self.quotes = {}
        for key, val in all_quotes.items():
            if key in _SINA_INDEX_CODES or key in _SINA_INDEX_CODES.values():
                # 这是指数
                name = key if key in _SINA_INDEX_CODES else _SINA_INDEX_CODES.get(key, key)
                self.index_quotes[name] = val
            else:
                self.quotes[key] = val

        return bool(self.quotes)

    # ═══════════════════════════════════════
    # 2. 分析引擎
    # ═══════════════════════════════════════
    def analyze_themes(self):
        """主题强度分析"""
        results = {}
        for theme, stocks in self.theme_stocks.items():
            pct_list = []
            vol_list = []
            up_count = 0
            amount = 0
            for code in stocks:
                q = self.quotes.get(code)
                if not q:
                    continue
                pct = q.get('pct_chg')
                if pct is not None:
                    pct_list.append(pct)
                    if pct > 0:
                        up_count += 1
                    amount += q.get('amount', 0)

            if len(pct_list) >= 1:
                results[theme] = {
                    'avg_pct': round(sum(pct_list) / len(pct_list), 2),
                    'max_pct': round(max(pct_list), 2),
                    'min_pct': round(min(pct_list), 2),
                    'up_count': up_count,
                    'total': len(pct_list),
                    'up_ratio': round(up_count / len(pct_list) * 100, 1),
                    'amount_yi': round(amount / 1e8, 2),
                    'stocks': [(code, self.quotes[code]['pct_chg']) for code in stocks if code in self.quotes],
                }
        return results

    def analyze_positions(self):
        """持仓盈亏分析"""
        result = []
        for code, pos in self.positions.items():
            q = self.quotes.get(code)
            if not q:
                continue
            entry = pos.get('entry', 0)
            cur = q.get('price', 0)
            pct = (cur - entry) / entry * 100 if entry > 0 else 0
            pct_today = q.get('pct_chg', 0)
            result.append({
                'code': code,
                'name': pos.get('name', code),
                'entry': entry,
                'cur': cur,
                'pct': round(pct, 2),
                'pct_today': pct_today,
            })
        return result

    def calc_market_score(self):
        """市场情绪评分（0-100）"""
        if not self.index_quotes:
            return 50, '未知', 50
        scores = []
        for name, q in self.index_quotes.items():
            pct = q.get('pct_chg', 0)
            if pct >= 2: s = 80
            elif pct >= 1: s = 70
            elif pct >= 0: s = 60
            elif pct >= -1: s = 45
            elif pct >= -2: s = 30
            else: s = 15
            scores.append(s)
        avg = sum(scores) / len(scores) if scores else 50
        avg = round(avg, 1)

        if avg >= 75: status, pos = '强趋势', 80
        elif avg >= 60: status, pos = '震荡偏强', 60
        elif avg >= 45: status, pos = '震荡', 40
        elif avg >= 30: status, pos = '弱势', 20
        else: status, pos = '主跌段', 10

        return avg, status, pos

    def detect_anomalies(self):
        """异常信号检测"""
        alerts = []
        now = time.time()

        # ── 持仓止损/止盈 ──
        for code, pos in self.positions.items():
            q = self.quotes.get(code)
            if not q:
                continue
            entry = pos.get('entry', 0)
            cur = q.get('price', 0)
            pct = (cur - entry) / entry * 100 if entry > 0 else 0
            name = pos.get('name', code)

            if pct <= -5 and now - self.last_alert.get(f'stop_loss_{code}', 0) > self.cooldown['stop_loss']:
                alerts.append({
                    'type': 'stop_loss', 'level': '🔴',
                    'title': '止损预警 [' + name + ']',
                    'msg': name + '(' + code + ') 亏损' + ('%.1f' % pct) + '%，触发止损！\n入场价' + ('%.3f' % entry) + ' → 现价' + ('%.3f' % cur),
                })
                self.last_alert[f'stop_loss_{code}'] = now

            elif pct >= 20 and now - self.last_alert.get(f'take_profit_{code}', 0) > self.cooldown['take_profit']:
                alerts.append({
                    'type': 'take_profit', 'level': '🟢',
                    'title': '止盈预警 [' + name + ']',
                    'msg': name + '(' + code + ') 盈利' + ('%.1f' % pct) + '%，已达止盈线！',
                })
                self.last_alert[f'take_profit_{code}'] = now

        # ── 主题异动（板块平均涨幅≥4%，且与上轮相比加速） ──
        theme_data = self.analyze_themes()
        sorted_themes = sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True)

        for theme, data in sorted_themes[:3]:
            avg = data['avg_pct']
            cooldown_key = 'theme_' + theme
            if avg >= 4 and now - self.last_alert[cooldown_key] > self.cooldown['theme_surge']:
                # 找龙头
                stocks_sorted = sorted(data['stocks'], key=lambda x: x[1], reverse=True)
                top_str = ' '.join([('%s(%.1f%%)' % (c, p)) for c, p in stocks_sorted[:3]])

                alerts.append({
                    'type': 'theme_surge', 'level': '🔥',
                    'title': '主题异动 [' + theme + ']',
                    'msg': theme + ' 板块平均涨幅 ' + ('%.1f' % avg) + '%\n龙头: ' + top_str,
                })
                self.last_alert[cooldown_key] = now

        # ── 个股异动 ──
        for code, themes in self.stock_themes.items():
            q = self.quotes.get(code)
            if not q:
                continue

            pct = q.get('pct_chg', 0)
            prev_pct = self.prev_quotes.get(code, {}).get('pct_chg', 0)

            # 急涨: 涨幅>5% 且 本轮较上轮加速>2%
            if pct > 5 and (pct - prev_pct) > 2 and now - self.last_alert['stock_surge_' + code] > self.cooldown['stock_surge']:
                theme_str = '/'.join(themes)
                alerts.append({
                    'type': 'stock_surge', 'level': '⚡',
                    'title': '个股急涨 [' + code + ']',
                    'msg': code + ' 涨幅 ' + ('%.1f' % pct) + '%（+' + ('%.1f' % (pct-prev_pct)) + ' 加速）\n所属: ' + theme_str,
                })
                self.last_alert['stock_surge_' + code] = now

            # 涨停预警
            if pct >= 9.5 and now - self.last_alert.get('limit_up_' + code, 0) > self.cooldown['limit_up']:
                theme_str = '/'.join(themes)
                alerts.append({
                    'type': 'limit_up', 'level': '🚀',
                    'title': '涨停预警 [' + code + ']',
                    'msg': code + ' 接近涨停 ' + ('%.1f' % pct) + '%！\n所属: ' + theme_str,
                })
                self.last_alert['limit_up_' + code] = now

        return alerts

    # ═══════════════════════════════════════
    # 3. 推送
    # ═══════════════════════════════════════
    def push(self, title, content):
        """Server酱微信推送"""
        print('  [Push] ' + title[:40])
        try:
            import requests
            sckey = os.getenv('WECHAT_SCKEY')
            if not sckey:
                print('    (未配置WECHAT_SCKEY，跳过)')
                return
            url = 'https://sctapi.ftqq.com/' + sckey + '.send'
            # 归一化文本
            lines = []
            for line in content.split('\n'):
                ls = line.strip()
                if not ls:
                    continue
                ls = re.sub(r'\*\*(.+?)\*\*', r'\1', ls)
                ls = re.sub(r'^#{1,3}\s*', '', ls)
                lines.append(ls + '  ')
            resp = requests.post(url, data={'title': title, 'desp': '\n'.join(lines)}, timeout=10)
            print('    状态: ' + str(resp.status_code))
        except Exception as e:
            print('    推送失败: ' + str(e))

    # ═══════════════════════════════════════
    # 4. 主循环
    # ═══════════════════════════════════════
    def is_trading_time(self):
        """是否交易时段"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        h, m = now.hour, now.minute
        if (h == 9 and m >= 30) or h == 10 or (h == 11 and m <= 30):
            return True
        if h == 12:
            return False
        if h in (13, 14):
            return True
        return False

    def run(self):
        print("=" * 60)
        print("  实时主题+个股预警系统 v2")
        print("  实时行情: 新浪财经批量接口 (毫秒级)")
        print("  监控: " + str(len(self.theme_stocks)) + ' 个主题 / ' + str(len(self.positions)) + ' 只持仓')
        print("  主题: " + ', '.join(self.theme_stocks.keys()) if hasattr(self, 'theme_stocks') else '无')
        print("=" * 60)

        cycle = 0
        last_full_minute = -1

        try:
            while True:
                now = datetime.now()

                # 15:05 收盘退出
                if now.hour == 15 and now.minute >= 5:
                    print("\n[" + now.strftime('%H:%M:%S') + "]  收盘，退出")
                    break

                if not self.is_trading_time():
                    if cycle % 5 == 0:
                        print("[" + now.strftime('%H:%M') + "] 非交易时段...")
                    time.sleep(30)
                    cycle += 1
                    continue

                cycle += 1
                ts = now.strftime('%H:%M:%S')

                # 获取行情
                t0 = time.time()
                ok = self.fetch_all()
                fetch_time = time.time() - t0

                if not ok:
                    print("[" + ts + "] 行情获取失败，等待重试...")
                    time.sleep(30)
                    continue

                # 检测预警
                alerts = self.detect_anomalies()
                for alert in alerts:
                    self.push(alert['title'], alert['msg'])

                # 每5分钟推送完整摘要
                if now.minute % 5 == 0 and now.minute != last_full_minute:
                    last_full_minute = now.minute
                    self.push_summary()

                # 控制台输出
                self.console_print(ts, fetch_time, alerts)

                # 对齐到整分钟
                sleep_sec = 60 - datetime.now().second
                time.sleep(max(1, sleep_sec))

        except KeyboardInterrupt:
            print("\n  已停止 (Ctrl+C)")
        finally:
            print("  监控结束")

    def push_summary(self):
        """推送完整摘要"""
        now = datetime.now()
        score, status, pos = self.calc_market_score()

        lines = [
            '📊 盘中摘要 ' + now.strftime('%H:%M'),
            '---',
            '【市场】评分 ' + str(score) + ' ' + status + ' 建议仓位 ' + str(pos) + '%',
            '---',
            '【指数】',
        ]

        for name, q in sorted(self.index_quotes.items()):
            pct = q.get('pct_chg', 0)
            e = '↑' if pct >= 0 else '↓'
            lines.append('  ' + name + ' ' + e + ' ' + ('%.2f' % abs(pct)) + '%')

        theme_data = self.analyze_themes()
        sorted_themes = sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True)
        if sorted_themes:
            lines.append('---')
            lines.append('【主题 TOP3】')
            for i, (theme, d) in enumerate(sorted_themes[:3], 1):
                e = '+' if d['avg_pct'] >= 0 else ''
                lines.append('  ' + str(i) + '. ' + theme + ' ' + e + ('%.1f' % d['avg_pct']) + '%')

        positions = self.analyze_positions()
        if positions:
            lines.append('---')
            lines.append('【持仓】')
            for p in positions:
                e = '🟢' if p['pct'] >= 0 else '🔴'
                lines.append('  ' + p['name'] + ' ' + e + ('%.1f' % p['pct']) + '%')

        self.push('📊 盘中摘要 ' + now.strftime('%H:%M'), '\n'.join(lines))

    def console_print(self, ts, fetch_time, alerts):
        """控制台输出"""
        score, status, pos = self.calc_market_score()

        print('')
        print('─── [' + ts + '] fetch=' + ('%.1fs' % fetch_time) + ' ───')
        for name, q in sorted(self.index_quotes.items()):
            pct = q.get('pct_chg', 0)
            e = '↑' if pct >= 0 else '↓'
            print('  ' + name + ' ' + e + ' ' + ('%.2f' % abs(pct)) + '%')

        theme_data = self.analyze_themes()
        sorted_themes = sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True)[:5]
        theme_str = ' | '.join([(t + '(' + ('%+.1f' % d['avg_pct']) + '%)') for t, d in sorted_themes])
        print('  主题: ' + theme_str)

        positions = self.analyze_positions()
        for p in positions:
            e = '🟢' if p['pct'] >= 0 else '🔴'
            print('  持仓 ' + p['name'] + ': ' + e + ('%.1f' % p['pct']) + '%')

        if alerts:
            for a in alerts:
                print('  >>> ' + a['level'] + ' ' + a['title'])


if __name__ == '__main__':
    monitor = RealtimeMonitor()
    monitor.run()
