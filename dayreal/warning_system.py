from datetime import datetime
from technical_analysis import calculate_ma, detect_concept_breakout
import requests
import os
from dotenv import load_dotenv

load_dotenv()


class WarningSystem:
    def __init__(self, config):
        self.config = config
        self.notified_stocks = set()
        self.ma5_cache = {}
        self.last_update_time = None
        self.notified_concepts = {}

    def check_price_near_ma5(self, quote, ma5, threshold=None):
        if threshold is None:
            threshold = self.config.get('warning.ma5_threshold', 0.02)

        if not quote or 'price' not in quote or ma5 is None:
            return False

        price = quote['price']
        if price == 0 or ma5 == 0:
            return False

        diff_ratio = abs(price - ma5) / ma5
        return diff_ratio <= threshold

    def check_stock(self, quote, ma5):
        if not quote or not ma5:
            return None

        stock_code = quote.get('code', '')
        stock_name = quote.get('name', '')
        price = quote.get('price', 0)

        if self.check_price_near_ma5(quote, ma5):
            if stock_code not in self.notified_stocks:
                self.notified_stocks.add(stock_code)
                return {
                    'code': stock_code,
                    'name': stock_name,
                    'price': price,
                    'ma5': ma5,
                    'diff_ratio': abs(price - ma5) / ma5,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
        else:
            if stock_code in self.notified_stocks:
                self.notified_stocks.remove(stock_code)

        return None

    def check_concept_breakout(self, concept_analysis):
        if not concept_analysis:
            return None

        concept_name = concept_analysis['concept_name']
        current_time = datetime.now()

        last_notified = self.notified_concepts.get(concept_name)
        if last_notified:
            time_diff = (current_time - last_notified).total_seconds()
            if time_diff < 1800:
                return None

        is_breakout, signals = detect_concept_breakout(concept_analysis)
        if not is_breakout:
            return None

        self.notified_concepts[concept_name] = current_time

        leader = concept_analysis['leader']

        return {
            'concept_name': concept_name,
            'signals': signals,
            'avg_change': concept_analysis['avg_change'],
            'limit_up_count': concept_analysis['limit_up_count'],
            'up_ratio': concept_analysis['up_ratio'],
            'leader': leader,
            'top_5': concept_analysis['top_5'],
            'time': current_time.strftime('%Y-%m-%d %H:%M:%S')
        }

    def notify(self, warning_info):
        if not warning_info:
            return

        if 'concept_name' in warning_info:
            message = self._format_concept_warning(warning_info)
            title = f"【板块异动】{warning_info['concept_name']}"
        else:
            message = self._format_stock_warning(warning_info)
            title = f"【个股预警】{warning_info['name']}"

        print(message)

        if self.config.get('notification.enabled', False):
            method = self.config.get('notification.method', 'print')
            if method == 'log':
                self._log_notification(message)
            elif method == 'server':
                self._send_server_message(title, message)

    def _format_stock_warning(self, warning_info):
        return (
            f"【个股预警】\n"
            f"股票: {warning_info['name']} ({warning_info['code']})\n"
            f"当前价格: {warning_info['price']:.2f}\n"
            f"5日均线: {warning_info['ma5']:.2f}\n"
            f"偏离度: {warning_info['diff_ratio']*100:.2f}%\n"
            f"时间: {warning_info['time']}"
        )

    def _format_concept_warning(self, warning_info):
        leader = warning_info['leader']
        top5_str = '\n'.join([
            f"  {i+1}. {s['name']} ({s['code']}) {s['change_pct']:.2f}%"
            for i, s in enumerate(warning_info['top_5'])
        ])

        return (
            f"【板块异动】\n"
            f"概念: {warning_info['concept_name']}\n"
            f"信号: {', '.join(warning_info['signals'])}\n"
            f"板块平均: {warning_info['avg_change']:.2f}%\n"
            f"涨停: {warning_info['limit_up_count']}家 | 上涨: {warning_info['up_ratio']*100:.0f}%\n"
            f"龙头: {leader['name']} ({leader['code']}) {leader['change_pct']:.2f}%\n"
            f"前5:\n{top5_str}\n"
            f"时间: {warning_info['time']}"
        )

    def _log_notification(self, message):
        log_file = 'warning_log.txt'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(message + '\n' + '-'*50 + '\n')

    def _send_server_message(self, title, message):
        sckey = os.getenv("WECHAT_SCKEY")
        if not sckey:
            print("警告: 未配置 WECHAT_SCKEY 环境变量")
            return

        url = f"https://sctapi.ftqq.com/{sckey}.send"
        data = {
            "title": title,
            "desp": message
        }

        try:
            requests.post(url, data=data, timeout=10)
            print(f"[Server酱] 推送成功")
        except Exception as e:
            print(f"[Server酱] 推送失败: {e}")
