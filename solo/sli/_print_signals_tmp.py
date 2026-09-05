import pandas as pd

df = pd.read_csv('sli/output/f120_result_20260901.csv')
con = df[df['verdict'] == 'CONDITIONAL BUY'].copy()


def s(r, k, f='.2f'):
    v = r.get(k)
    if v is None:
        return '--'
    try:
        if pd.isna(v):
            return '--'
    except (TypeError, ValueError):
        pass
    try:
        return format(float(v), f)
    except (TypeError, ValueError):
        t = str(v)
        return '--' if t == 'nan' else t


def un(x, u=''):
    return '--' if x == '--' else x + u


for stg, title in [('ready', '第一类：买点已确认（ready，结构已出现）'),
                   ('wait', '第二类：等待触发（wait，需先满足触发条件）')]:
    g = con[con['stage'] == stg]
    print('=' * 86)
    print(f'### {title} —— 共 {len(g)} 只')
    print('=' * 86)
    for _, r in g.iterrows():
        can_up = str(r['setup']) in ('BREAKOUT_RETEST', 'FIRST_PULLBACK')
        up = '可升PRIMARY' if can_up else '评级上限=CONDITIONAL'
        print()
        print(f"◆ {r['name']}（{r['ts_code']}） {r['subsector']} ｜ 细分TOP{s(r, 'rank5', '.0f')} ｜ {up}")
        print(f"  生命周期={s(r, 'lifecycle', None)} ｜ 龙头属性={s(r, 'leader_type', None)} ｜ SLI_v2={s(r, 'sli_v2', '.1f')}")
        print(f"  评分: F120={s(r, 'F120', '.1f')} ｜ F={s(r, 'F', '.0f')} P={s(r, 'P', '.0f')} E={s(r, 'E', '.0f')} T={s(r, 'T', '.0f')} V={s(r, 'V', '.0f')}")
        print(f"  评级原因: {r['reason']}")
        print(f"  价格: 现价{s(r, 'cur')} ｜ BUY ZONE {s(r, 'zone_lo')}~{s(r, 'zone_hi')} ｜ 理想入场{s(r, 'ideal')} ｜ MA20={s(r, 'ma20')}")
        print(f"  风控: 止损{s(r, 'stop')} ｜ 目标{s(r, 'target')} ｜ R:R={s(r, 'rr')} ｜ 追涨上限{s(r, 'ceiling')}")
        print(f"  Trigger: {r['trigger']}")
        print(f"  中报: 营收{un(s(r, 'or_yoy', '.0f'), '%')} ｜ 归母{un(s(r, 'dt_yoy', '.0f'), '%')} ｜ Q2单季{un(s(r, 'q2_yoy', '.0f'), '%')}"
              f" ｜ ROE={un(s(r, 'roe', '.1f'), '%')} ｜ 净利率={un(s(r, 'npm', '.1f'), '%')} ｜ PE={s(r, 'pe_ttm', '.1f')} ｜ 主业纯度={un(s(r, 'product', '.1f'), '分')}")
        print(f"  动量: R120={un(s(r, 'r120', '.0f'), '%')}")

print()
print('注1：PRIMARY 仅可能由第一类中的 BREAKOUT_RETEST / FIRST_PULLBACK 评分补足后升级；')
print('注2：DEEP_PULLBACK / BASE_BREAKOUT 评级上限为 CONDITIONAL，按 BUY ZONE 分笔执行、止损纪律不变。')
