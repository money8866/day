#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主题轮动 - 盘中实时监控
流程: 读取复盘计划 → 通达信行情 → 启动股预警 → Server酱

自动告诉你: "今天应该做哪个主题的第1只启动股"

用法:
  python theme_realtime_monitor.py              # 默认30秒轮询
  python theme_realtime_monitor.py --interval 15
  python theme_realtime_monitor.py --no-push
"""
import os
import sys
import time
import argparse
from datetime import datetime
from collections import defaultdict
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_rotation.config import (
    STARTER_PCT_THRESHOLD, STARTER_VOL_RATIO,
    ALERT_COOLDOWN_SEC, CHECK_INTERVAL_SEC,
)
from theme_rotation.database import (
    init_rotation_db, load_portfolio, get_daily_plan,
    get_theme_ranking, get_recent_alerts,
)
from theme_rotation.market_data import (
    TdxQuoteClient, ts_code_to_tdx, get_last_trade_date,
    is_trading_time, is_early_session,
)
from theme_rotation.notifier import push_starter_alert


class ThemeRealtimeMonitor:
    def __init__(self, push: bool = True):
        self.push = push
        self.tdx = TdxQuoteClient()
        self.trade_date = get_last_trade_date()
        self.last_alert_time = {}
        self.triggered_themes = set()
        self.plan = None
        self.watch_list = []

    def load_plan(self):
        init_rotation_db()
        self.plan = get_daily_plan(self.trade_date)

        portfolio = load_portfolio()
        theme_groups = defaultdict(list)
        for s in portfolio:
            theme_groups[s["theme_name"]].append(s)

        ranking = get_theme_ranking(self.trade_date, top_n=5)
        priority_themes = []
        if self.plan:
            if self.plan.get("mainline_theme"):
                priority_themes.append(self.plan["mainline_theme"])
            if self.plan.get("backup_theme"):
                priority_themes.append(self.plan["backup_theme"])
            if self.plan.get("starter_theme"):
                priority_themes.append(self.plan["starter_theme"])
        for r in ranking:
            if r["theme_name"] not in priority_themes:
                priority_themes.append(r["theme_name"])

        seen = set()
        self.watch_list = []
        for theme in priority_themes[:5]:
            stocks = theme_groups.get(theme, [])
            # 优先 leader/core 层
            stocks_sorted = sorted(
                stocks,
                key=lambda x: {"leader": 0, "core": 1, "follower": 2}.get(x.get("layer"), 3),
            )
            for s in stocks_sorted[:8]:
                if s["ts_code"] not in seen:
                    seen.add(s["ts_code"])
                    market, code = ts_code_to_tdx(s["ts_code"])
                    self.watch_list.append({
                        "ts_code": s["ts_code"],
                        "name": s["name"],
                        "theme_name": theme,
                        "layer": s.get("layer", ""),
                        "market": market,
                        "code": code,
                        "is_plan_starter": (
                            self.plan
                            and s["ts_code"] == self.plan.get("starter_ts_code")
                        ),
                    })

        return self.plan

    def _cooldown_ok(self, ts_code: str) -> bool:
        last = self.last_alert_time.get(ts_code)
        if not last:
            return True
        return (datetime.now() - last).total_seconds() >= ALERT_COOLDOWN_SEC

    def check_starter_signal(self, watch: dict, quote: dict) -> Optional[Dict]:
        pct = quote.get("pct_chg", 0)
        price = quote.get("price", 0)
        if price <= 0:
            return None

        is_starter_candidate = watch.get("is_plan_starter") or watch.get("layer") == "leader"
        if not is_starter_candidate and pct < STARTER_PCT_THRESHOLD:
            return None

        # 启动条件
        triggered = False
        reason = ""

        if pct >= 9.5:
            triggered = True
            reason = "涨停"
        elif pct >= STARTER_PCT_THRESHOLD:
            triggered = True
            reason = f"涨幅{pct:.1f}%"
        elif watch.get("is_plan_starter") and pct >= 3 and is_early_session():
            triggered = True
            reason = f"计划启动股早涨{pct:.1f}%"

        if not triggered:
            return None

        theme = watch["theme_name"]
        # 每个主题只报一次"第1启动"
        theme_key = f"{theme}_{datetime.now().strftime('%Y%m%d')}"
        if theme_key in self.triggered_themes and not watch.get("is_plan_starter"):
            return None

        return {
            "theme_name": theme,
            "name": watch["name"],
            "ts_code": watch["ts_code"],
            "pct_chg": pct,
            "price": price,
            "reason": reason,
            "is_plan_starter": watch.get("is_plan_starter", False),
            "theme_key": theme_key,
        }

    def run_once(self):
        if not self.watch_list:
            print("监控列表为空")
            return

        stock_list = [(w["market"], w["code"]) for w in self.watch_list]
        quotes = self.tdx.get_quotes(stock_list)
        if not quotes:
            print("获取行情失败")
            return

        quote_map = {q["code"]: q for q in quotes}
        now_str = datetime.now().strftime("%H:%M:%S")
        signals = []

        for w in self.watch_list:
            q = quote_map.get(w["code"])
            if not q:
                continue
            if not self._cooldown_ok(w["ts_code"]):
                continue

            sig = self.check_starter_signal(w, q)
            if sig:
                signals.append(sig)

        # 按主题分组，每个主题只取涨幅最高的作为"第1启动"
        by_theme = defaultdict(list)
        for sig in signals:
            by_theme[sig["theme_name"]].append(sig)

        for theme, theme_sigs in by_theme.items():
            best = max(theme_sigs, key=lambda x: x["pct_chg"])
            plan_mark = "⭐计划" if best.get("is_plan_starter") else ""
            print(
                f"[{now_str}] 🚀 {theme} 启动股: {best['name']} "
                f"{best['pct_chg']:+.1f}% {plan_mark} ({best['reason']})"
            )

            if self.push:
                push_starter_alert(
                    self.trade_date, theme, best["name"], best["ts_code"],
                    best["pct_chg"], best["price"],
                    alert_type="主题启动" if not best.get("is_plan_starter") else "计划启动股",
                )

            self.last_alert_time[best["ts_code"]] = datetime.now()
            self.triggered_themes.add(best["theme_key"])

    def run(self, interval: int = CHECK_INTERVAL_SEC):
        print("=" * 60)
        print("主题轮动 - 盘中实时监控")
        print("=" * 60)

        plan = self.load_plan()
        if plan and plan.get("starter_name"):
            print(f"\n📋 今日计划:")
            print(f"   主线: {plan.get('mainline_theme', '-')}")
            print(f"   启动股: {plan.get('starter_name')} ({plan.get('starter_ts_code', '')})")
            print(f"   主题: {plan.get('starter_theme', '-')}")
        else:
            print("\n⚠ 未找到今日复盘计划，请先运行 theme_post_review.py")
            print("   将按 portfolio 中 leader 层监控 TOP5 主题")

        print(f"\n监控 {len(self.watch_list)} 只股票, 间隔 {interval}s")
        for w in self.watch_list[:15]:
            mark = "⭐" if w.get("is_plan_starter") else f"[{w['layer']}]"
            print(f"  {mark} {w['name']:8s} {w['ts_code']} ({w['theme_name']})")

        if not self.tdx.connect():
            print("\n✗ 通达信连接失败，无法监控")
            return

        print(f"\n开始监控 (Ctrl+C 停止)\n")

        try:
            while True:
                now = datetime.now()
                if not is_trading_time(now):
                    time.sleep(5)
                    continue
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n停止监控")
        finally:
            self.tdx.disconnect()


def main():
    parser = argparse.ArgumentParser(description="主题轮动盘中监控")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL_SEC)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    monitor = ThemeRealtimeMonitor(push=not args.no_push)
    monitor.run(interval=args.interval)


if __name__ == "__main__":
    main()
