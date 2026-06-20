    # =========================
    # ICPM 产业资金定价诊断（Top 10 开仓股）
    # =========================
    icpm_text = ""
    if _ICPM_AVAILABLE and icpm_top10_list:
        try:
            import importlib
            import yaml

            config_path = os.path.join(_MF_DIR, 'config.yaml')
            with open(config_path, 'r', encoding='utf-8') as f:
                icpm_config = yaml.safe_load(f)
            icpm_token_env = icpm_config.get('tushare', {}).get('token_env', 'TUSHARE_TOKEN')
            icpm_token = os.environ.get(icpm_token_env)
            if not icpm_token:
                for ep in [Path(__file__).resolve().parent.parent.parent / "config" / ".env",
                           Path(__file__).resolve().parent.parent / "config" / ".env"]:
                    if ep.exists():
                        with open(ep, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if not line or line.startswith('#'):
                                    continue
                                if '=' in line:
                                    k, v = line.split('=', 1)
                                    if k.strip() == icpm_token_env:
                                        icpm_token = v.strip().strip('"\'')
                                        break
                        break

            if icpm_token:
                # 延迟导入（避免循环引用：tushare_quant ↔ industry_pricing_model）
                df_mod = importlib.import_module('data_fetcher')
                DataFetcher = df_mod.DataFetcher
                ipm = importlib.import_module('industry_pricing_model')
                IndustryPricingModel = ipm.IndustryPricingModel
                extract_pricing_data = ipm.extract_pricing_data
                fetcher = DataFetcher(icpm_token, icpm_config)
                codes = [s['code'] for s in icpm_top10_list if s['code']]
                start_year = str(datetime.now().year - 3)
                financial_batch = fetcher.get_stock_financial_batch(codes, start_year=start_year, max_workers=5)

                daily_basic = fetcher.get_daily_basic(TRADE_DATE)
                moneyflow = fetcher.get_moneyflow(TRADE_DATE)
                daily_basic_idx = {r['ts_code']: r for _, r in daily_basic.iterrows()} if daily_basic is not None and not daily_basic.empty else {}
                moneyflow_idx = {r['ts_code']: r for _, r in moneyflow.iterrows()} if moneyflow is not None and not moneyflow.empty else {}

                model = IndustryPricingModel(icpm_config)
                icpm_lines = []

                # 生命周期/决策/资金映射
                _STAGE_CN = {
                    "ACCUMULATION": "资金建仓", "MAINLINE_ACCELERATION": "主升浪",
                    "DISTRIBUTION": "分歧/顶部震荡", "DECLINE": "衰退", "EARLY_STAGE": "产业萌芽",
                }
                _DECISION_CN = {"BUY": "买入", "HOLD": "观望", "REDUCE": "减仓", "EXIT": "清仓"}
                _CAPITAL_CN = {
                    "STRONG_INFLOW": "强流入", "WEAK_INFLOW": "弱流入",
                    "NEUTRAL": "中性", "OUTFLOW": "流出",
                }

                # 收集需要删除的股票代码（REDUCE 或 EXIT）
                icpm_exclude_codes = set()

                icpm_lines.append("=" * 72)
                icpm_lines.append("产业资金定价诊断（ICPM）- 整合评分Top30")
                icpm_lines.append("=" * 72)
                icpm_lines.append(f"{'股票':<16} {'生命周期':<12} {'主线强度':<8} {'资金状态':<8} {'决策':<6} {'整合评分':<8}")
                icpm_lines.append("-" * 72)

                for s in icpm_top10_list:
                    code = s['code']
                    name = s['name']
                    theme = s['theme']
                    if not code:
                        continue
                    kline = get_hist_data(code) if code else None
                    data = extract_pricing_data(
                        code, name, theme, '',
                        financial_batch,
                        daily_basic_idx.get(code),
                        moneyflow_idx.get(code),
                        kline_df=kline,
                    )
                    if data is None:
                        print(f"[ICPM] {name}({code}) → extract_pricing_data 返回 None（财务数据缺失）")
                        continue
                    result = model.diagnose(data)
                    stage_cn = _STAGE_CN.get(result.lifecycle_stage, result.lifecycle_stage)
                    decision_cn = _DECISION_CN.get(result.final_decision, result.final_decision)
                    capital_cn = _CAPITAL_CN.get(result.capital_flow_state, result.capital_flow_state)
                    # 诊断详情打印（保留英文缩写给日志）
                    print(f"[ICPM] {name}({code}) "
                          f"theme={theme} "
                          f"revenue_yoy={data.revenue_yoy:.2%} "
                          f"profit_yoy={data.profit_yoy:.2%} "
                          f"order_cl_yoy={data.contract_liability_yoy:.2%} "
                          f"order_score={result.order_explosion_score:.0f} "
                          f"exp_score={result.expectation_score:.0f} "
                          f"mainline={result.is_mainline} "
                          f"strength={result.mainline_strength:.2f} "
                          f"capital={result.capital_flow_state} "
                          f"→ stage={result.lifecycle_stage} "
                          f"decision={result.final_decision}")
                    icpm_lines.append(
                        f"{name+'('+code+')':<16} {stage_cn:<12} "
                        f"{result.mainline_strength:<8.2f} {capital_cn:<8} "
                        f"{decision_cn:<6} {s['open_score']:<8.1f}"
                    )

                    # 收集需要删除的股票（REDUCE 或 EXIT）
                    if result.final_decision in ("REDUCE", "EXIT"):
                        icpm_exclude_codes.add(code)

                icpm_lines.append("=" * 72)
                icpm_text = "\n".join(icpm_lines)
                print(icpm_text)
        except Exception as e:
            print(f"[ICPM] 诊断失败: {e}")
            import traceback
            traceback.print_exc()
            icpm_text = ""
