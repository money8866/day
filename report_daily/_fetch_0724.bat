@echo off
chcp 65001 >nul
set OUT=D:\mystock\report_daily
set SVC=hithink-finance-a-share

mcporter call %SVC%.get_a_share_prices_snapshot --args "{\"thscodes\":\"000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH\"}" --output json > "%OUT%\q_indices_0724.json" 2>&1
echo indices_done %errorlevel%

mcporter call %SVC%.get_a_share_special_data_limit_up_pool --args "{\"page\":1,\"size\":50}" --output json > "%OUT%\q_limitup_0724_1.json" 2>&1
echo lu1_done

mcporter call %SVC%.get_a_share_special_data_limit_up_pool --args "{\"page\":2,\"size\":50}" --output json > "%OUT%\q_limitup_0724_2.json" 2>&1
echo lu2_done

mcporter call %SVC%.get_a_share_special_data_limit_up_pool --args "{\"page\":3,\"size\":50}" --output json > "%OUT%\q_limitup_0724_3.json" 2>&1
echo lu3_done

mcporter call %SVC%.get_a_share_special_data_limit_down_pool --output json > "%OUT%\q_limitdown_0724.json" 2>&1
echo ldown_done

mcporter call %SVC%.get_a_share_special_data_hot_stock_list --args "{\"period\":\"day\"}" --output json > "%OUT%\q_hot_0724.json" 2>&1
echo hot_done

mcporter call %SVC%.get_a_share_special_data_dragon_tiger_list --output json > "%OUT%\q_dragon_0724.json" 2>&1
echo dragon_done

mcporter call %SVC%.get_a_share_prices_snapshot --args "{\"thscodes\":\"159516.SZ,159611.SZ,512480.SH,512760.SH,159865.SZ,515050.SH\"}" --output json > "%OUT%\q_pos_0724.json" 2>&1
echo pos_done
