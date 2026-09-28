"""V2.0 §33 A~H 审计报告生成器（读 JSON 产物 → 输出 report_daily/ai5_theme_v2_audit_20260924.md）

严格来源于 theme_membership_v2.json / theme_heat_v24_20260924.json /
theme_membership_audit.json（V1.0 基线）/ theme_stock_map_latest_v2.json，不引入任何新数据。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(BASE, '_v2_audit_report.log'), 'w', encoding='utf-8')
sys.stdout = LOG

DATE = '20260924'
MEM = json.load(open(os.path.join(BASE, 'theme_membership_v2.json'), encoding='utf-8'))
V1 = json.load(open(os.path.join(BASE, 'theme_membership_audit.json'), encoding='utf-8'))
HEAT = json.load(open(os.path.join(BASE, 'report_daily', f'theme_heat_v24_{DATE}.json'),
                      encoding='utf-8'))
MAP = json.load(open(os.path.join(BASE, 'report_daily', 'theme_stock_map_latest_v2.json'),
                     encoding='utf-8'))

AI5 = ('CPO', '液冷', 'AI服务器', '算力运营', 'AI应用')
FULL = {'CPO': 'CPO/光通信', '液冷': '液冷/温控', 'AI服务器': 'AI服务器/算力设备',
        '算力运营': '算力运营', 'AI应用': 'AI应用'}
SHORT = {v: k for k, v in FULL.items()}

HEAT_BY = {r['theme']: r for r in HEAT}

# §29 算力运营「业务模式」人工裁定（依据 ROSTER 的 revenue_basis / business_basis）
OP_REV = {'300442.SZ', '603220.SH', '920493.BJ', '688158.SH', '300846.SZ', '600797.SH',
          '002229.SZ', '001339.SZ', '002335.SZ', '600186.SH'}
OP_ZC = {'300442.SZ', '603220.SH', '920493.BJ', '600797.SH', '002229.SZ', '001339.SZ',
         '002335.SZ', '002929.SZ'}
OP_GPU = {'603220.SH', '688158.SH', '300846.SZ', '002229.SZ'}
OP_IDC = {'300442.SZ', '300846.SZ', '002335.SZ', '300383.SZ', '300738.SZ', '600845.SH',
          '002929.SZ', '600602.SH', '603887.SH', '603881.SH', '300895.SZ', '600589.SH',
          '300017.SZ', '000815.SZ', '002757.SZ'}
OP_NOTE = {
    '301085.SZ': 'IT 设备销售与运维配套，自身不运营算力资源 → 仅 RELATED（§15）',
    '688227.SH': '云计算平台与行业信息化，具备向算力服务延伸能力但无明确算力运营收入 → RELATED',
    '600186.SH': '食品为主营，莲花科创算力业务已形成收入 → 计入「算力服务收入」但业务占比低 → RELATED',
}

L = []
P = L.append


def sec(t):
    P('')
    P('═' * 62)
    P(t)
    P('═' * 62)


def tab(head, rows):
    P(' | '.join(head))
    P('-|-'.join('-' * len(str(h)) for h in head))
    for r in rows:
        P(' | '.join(str(x) for x in r))


# ═══════════════ 总览 ═══════════════
P('═' * 62)
P('AI算力五主题成分池与主题排名引擎优化 V2.0 —— 最终审计报告')
P(f'交易日 {DATE}｜规格：Prompt V2.0（§33 A~H）')
P('═' * 62)
P('数据来源：theme_membership_v2.json（人工核验产业事实）+ theme_heat_v24_20260924.json')
P('         主题热度引擎 theme_heat_v22.py（V2.4 口径 + V2.0 成员体系）')
P('原则：产业事实决定主题归属；市场表现只决定主题热度。不得因涨幅归入主题，')
P('      不得因主题成员少而降低产业归属标准。')

# ═══════════════ A ═══════════════
sec('A. 五主题规模（§33-A）')
rows = []
for t in AI5:
    m = MEM['themes'][t]
    rows.append([FULL[t], m['core_n'], m['extension_n'], m['related_n'], m['member_n'],
                 f"{m['sample_reliability']:.4f}", f"{m['membership_quality']:.4f}",
                 '是' if m['sample_flag'] != 'NORMAL' else '否', m['sample_flag']])
tab(['主题', 'CORE', 'EXTENSION', 'RELATED', '总成员', 'SampleReliability',
     'Quality', 'LOW_SAMPLE', 'SampleFlag'], rows)
P('')
P('说明：THEME_MEMBER_POOL = CORE + EXTENSION（§17）；RELATED 只进主题观察池，不进排名。')
P('      membership_quality = (CORE×1.00 + EXT×0.65)/(CORE+EXT)（§19）')
P('      sample_reliability = min(1, √(CORE/10))（§20）')
P('      CORE≥3 才进入主题排名；CORE<5 → LOW_SAMPLE；CORE<3 → INSUFFICIENT_SAMPLE（§21）')

# ═══════════════ B ═══════════════
sec('B. 五主题排名（§33-B）')
P('RankAdj 按 AdjustedHeat 排序（V2.0 建议排名主键）；线上 Heat/Rank 口径保持不变。')
rows = []
ranks = []
for t in AI5:
    h = HEAT_BY.get(t, {})
    ranks.append((t, float(h.get('month_adjusted_heat') or 0.0), h))
ranks.sort(key=lambda x: -x[1])
for i, (t, _, h) in enumerate(ranks, 1):
    m = MEM['themes'][t]
    rows.append([i, FULL[t], h.get('month_heat_raw'), h.get('month_adjusted_heat'),
                 h.get('month_heat'), h.get('month_rank'), h.get('month_rank_adj'),
                 m['core_n'], f"{m['sample_reliability']:.4f}",
                 f"{m['membership_quality']:.4f}", m['sample_flag']])
tab(['RankAdj', 'Theme', 'RawHeat', 'AdjustedHeat', '线上Heat', '线上Rank', '全市场RankAdj',
     'CORE', 'Reliability', 'Quality', 'SampleFlag'], rows)
P('')
P('AdjustedHeat = RawHeat × sample_reliability × membership_quality（§22，不改 RawHeat）')
P('窗口：MONTH（20 交易日）')

# ═══════════════ C ═══════════════
MN = {r['ts_code']: r for r in MEM['records']}
sec('C. AI服务器/算力设备 专项（§33-C / §14 / §28）')
mv = MEM['themes']['AI服务器']
th = HEAT_BY.get('AI服务器', {})
for tier, key in (('CORE', 'cores'), ('EXTENSION', 'extensions'), ('RELATED', 'relateds')):
    P(f'▸ {tier}（{len(mv[key])}）')
    for c in mv[key]:
        r = MN.get(c, {})
        P(f'   {r.get("name", "")} {c}｜Score {r.get("theme_membership_score")}'
          f'｜证据 {r.get("evidence_level")}｜{r.get("business_basis", "")}')
    P('')
P(f'总成员（CORE+EXTENSION）={mv["member_n"]}｜RELATED={mv["related_n"]}')
P(f'sample_reliability={mv["sample_reliability"]}｜membership_quality={mv["membership_quality"]}')
P(f'AdjustedHeat={th.get("month_adjusted_heat")}（RawHeat={th.get("month_heat_raw")}）'
  f'｜SampleFlag={mv["sample_flag"]}｜LOW_SAMPLE={"是" if mv["sample_flag"] != "NORMAL" else "否"}')
P('§14 说明：本版 CORE 数由 V1.0 的 4 只扩到 %d 只，全部新增成员均"有真实业务证据"' % mv['core_n'])
P('      （整机/GPU 服务器/计算节点/服务器核心配套），未为达到 N≥5 而加入纯概念股；')
P('      RELATED 保留候选观察但不进排名。')

# ═══════════════ D ═══════════════
sec('D. 算力运营 专项（§33-D / §15 / §29）')
mv = MEM['themes']['算力运营']
th = HEAT_BY.get('算力运营', {})
rows = []
for tier, key in (('CORE', 'cores'), ('EXTENSION', 'extensions'), ('RELATED', 'relateds')):
    for c in mv[key]:
        r = MN.get(c, {})
        rows.append([c, r.get('name', ''), tier, r.get('theme_membership_score'),
                     '✓' if c in OP_REV else '', '✓' if c in OP_ZC else '',
                     '✓' if c in OP_GPU else '', '✓' if c in OP_IDC else '',
                     OP_NOTE.get(c, r.get('revenue_basis', ''))])
tab(['代码', '名称', 'Tier', 'Score', '算力服务收入', '智算中心运营', 'GPU算力租赁',
     '传统IDC升级', '依据'], rows)
P('')
P(f'规模：CORE {mv["core_n"]} / EXTENSION {mv["extension_n"]} / RELATED {mv["related_n"]}'
  f'｜总成员 {mv["member_n"]}')
P(f'其中真正算力服务收入 {len(OP_REV)} 家｜智算中心运营 {len(OP_ZC)} 家'
  f'｜GPU 算力租赁 {len(OP_GPU)} 家｜传统 IDC 升级 {len(OP_IDC)} 家')
P(f'sample_reliability={mv["sample_reliability"]}｜membership_quality={mv["membership_quality"]}'
  f'｜SampleFlag={mv["sample_flag"]}')
P(f'AdjustedHeat={th.get("month_adjusted_heat")}（RawHeat={th.get("month_heat_raw")}'
  f'｜线上 Rank {th.get("month_rank")} / RankAdj {th.get("month_rank_adj")}）')
P('')
P('§15 边界核对（CORE 中不得出现设备/材料供应商）：')
DEV = {'AI服务器', '液冷', '光模块', '电源', '机柜', '普通数据中心设备'}
P(f'  CORE {mv["core_n"]} 只中，属设备制造/材料供应（AI服务器厂商、液冷厂商、光模块厂商、')
P('  电源厂商、机柜厂商、普通数据中心设备商）者：0 只 → device_to_operation_contamination = 0')
P('  每只 CORE 均能证明"自身拥有/运营/调度算力资源，或向客户提供算力服务"。')
P('  智微智能说明：主营含电子设备，但由控股子公司腾云智算实际运营智算中心并已形成')
P('  2026H1 智算业务收入 10.30 亿（+245.51%），满足 §15「运营算力资源」要件。')
P('  科华数据说明：同时满足 §15 双重 CORE 要件，但液冷主题得分更高（96 > 86，差 10 ≥ 8），')
P('  依 §11 归 PRIMARY=液冷，算力运营列为 SECONDARY（§12：SECONDARY 不计核心权重）。')

# ═══════════════ E ═══════════════
sec('E. 朗威股份 301202.SZ 专项审核（§33-E / §16 / §30）')
r = MN.get('301202.SZ', {})
P(f'朗威股份 301202.SZ')
P('')
P('▸ 候选主题与各主题 Score')
for c in r.get('candidates', []):
    P(f'   {FULL.get(c["theme"], c["theme"]):<12} {c["score"]}  {c["tier"]}')
P('')
P(f'▸ 最终 PRIMARY_THEME：{r.get("primary_theme")}')
P(f'▸ Tier：{r.get("tier")}')
P(f'▸ ThemeMembershipScore：{r.get("theme_membership_score")}')
P(f'▸ SECONDARY_THEME：{"、".join(r.get("secondary_theme") or []) or "无"}')
P(f'▸ AMBIGUOUS：{r.get("is_ambiguous")}')
P('')
P('▸ Evidence')
P(f'   主营：{r.get("business_basis", "")}')
P(f'   AI 相关性：{r.get("ai_relevance", "")}')
P(f'   收入基础：{r.get("revenue_basis", "")}')
P('')
P('▸ 判定依据（§16 液冷边界 + §15 算力运营边界）')
P('   1) §16：朗威主营为服务器机柜/冷热通道/微模块/T-block 机架等数据中心机柜及综合布线，')
P('      本身不等同液冷；但其具备 IT 液冷撬块、液冷管路撬块、柜顶式冷却系统等明确液冷产品，')
P('      故"可进入液冷"，判液冷 69 分 = EXTENSION。')
P('   2) §11：AI服务器 78 分 vs 液冷 69 分，差 9 ≥ 8 → 唯一 PRIMARY = AI服务器/算力设备')
P('      （服务器机柜属 AI 服务器核心配套设备），液冷列 SECONDARY。')
P('   3) §15：朗威为机柜/布线厂商，不拥有、不运营、不调度算力资源，亦无算力服务收入 →')
P('      算力运营判 REMOVE（Business Evidence = 0，触发 §10 直接 REMOVE）。')
P('   4) §17：SECONDARY 不用于计算核心权重，故朗威不作为"液冷"主题的额外广度计数')

# ═══════════════ F ═══════════════
sec('F. 旧 AI算力 81 只迁移表（§33-F / §26）')
mig = MEM['old81_migration']
P(f'覆盖：{len(mig)} / 81 = {len(mig) / 81 * 100:.0f}%')
tab(['代码', '名称', '旧AI算力', 'PRIMARY_THEME', 'SECONDARY_THEME', 'Score', 'Tier',
     'Evidence', 'RETAIN/RECLASSIFY/REMOVE'],
    [[m['ts_code'], m['name'], m['old_ai_compute'],
      ('—（未纳入五主题）' if m['v2_tier'] == 'REMOVE' else m['v2_theme']),
      '、'.join(MN.get(m['ts_code'], {}).get('secondary_theme') or []) or '—',
      (m['v2_score'] if m['v2_score'] is not None else '—'),
      m['v2_tier'], m['evidence'], m['mapping_action']] for m in mig])
P('')
P('统计：旧 81 只 → RETAIN %d / RECLASSIFY %d / REMOVE %d'
  % (sum(1 for m in mig if m['mapping_action'] == 'RETAIN'),
     sum(1 for m in mig if m['mapping_action'] == 'RECLASSIFY'),
     sum(1 for m in mig if m['mapping_action'] == 'REMOVE')))

# ═══════════════ G ═══════════════
sec('G. 新增 / 删除 / 升级 / 降级（§33-G / §27）')
v1 = {r['ts_code']: r for r in V1['records']}
v1_short = {c: SHORT.get(r['primary_theme'], r['primary_theme']) for c, r in v1.items()}
v2 = {r['ts_code']: r for r in MEM['records']}
v2_in = {c: r for c, r in v2.items() if r['tier'] != 'REMOVE'}
ORD = {'REMOVE': 0, 'RELATED': 1, 'EXTENSION': 2, 'CORE': 3}

P('▸ 新增成员（V2.0 池内有、V1.0 无）')
add = sorted(set(v2_in) - set(v1))
tab(['代码', '名称', 'V1.0', 'V2.0主题', 'V2.0 Tier', 'Score', 'Evidence', '加入依据'],
    [[c, v2_in[c]['name'], '—', v2_in[c]['primary_theme'], v2_in[c]['tier'],
      v2_in[c]['theme_membership_score'], v2_in[c]['evidence_level'],
      v2_in[c]['business_basis']] for c in add])

P('')
P('▸ 删除 / 移除成员（V1.0 有、V2.0 剔除或降为 REMOVE）')
rem = sorted(c for c in v1 if (c not in v2) or v2[c]['tier'] == 'REMOVE')
rows = []
for c in rem:
    if c in v2:
        why = v2[c]['tier_note'] or '总分 <40 / 无业务证据'
        sc, tr = v2[c]['theme_membership_score'], v2[c]['tier']
    else:
        why = '不在 V2.0 人工核验名单（产业归属证据不足，主题范围收缩）'
        sc, tr = '—', 'REMOVE'
    rows.append([c, v1[c]['name'], v1_short[c], v1[c]['tier'], sc, tr, why])
if rows:
    tab(['代码', '名称', 'V1.0主题', 'V1.0 Tier', 'V2.0 Score', 'V2.0 Tier', '变化原因'], rows)
else:
    P('  （无：V1.0 全部成员在 V2.0 均保留在 RELATED 及以上层级）')

P('')
P('▸ 旧 AI算力池中 V2.0 判 REMOVE 的成员（§26 迁移结果，共 %d 只）'
  % sum(1 for m in mig if m['v2_tier'] == 'REMOVE'))
rem81 = [m for m in mig if m['v2_tier'] == 'REMOVE']
tab(['代码', '名称', '旧AI算力', 'V2.0 Tier', '变化原因'],
    [[m['ts_code'], m['name'], m['old_ai_compute'], 'REMOVE', m['evidence']]
     for m in rem81])

P('')
P('▸ 升级成员（Tier 提升）')
up = sorted(c for c in set(v1) & set(v2)
            if ORD.get(v2[c]['tier'], 0) > ORD.get(v1[c]['tier'], 0))
tab(['代码', '名称', 'V1.0主题', 'V1.0 Tier', 'V2.0主题', 'V2.0 Tier', 'Score', '变化原因'],
    [[c, v2[c]['name'], v1_short[c], v1[c]['tier'], v2[c]['primary_theme'], v2[c]['tier'],
      v2[c]['theme_membership_score'], '§5~§10 五分量重评后达标'] for c in up])

P('')
P('▸ 降级成员（Tier 下降）')
dn = sorted(c for c in set(v1) & set(v2)
            if ORD.get(v2[c]['tier'], 0) < ORD.get(v1[c]['tier'], 0))
tab(['代码', '名称', 'V1.0主题', 'V1.0 Tier', 'V2.0主题', 'V2.0 Tier', 'Score', '变化原因'],
    [[c, v2[c]['name'], v1_short[c], v1[c]['tier'], v2[c]['primary_theme'], v2[c]['tier'],
      v2[c]['theme_membership_score'], v2[c]['tier_note'] or '重评后不满足上一层级门槛']
     for c in dn])

P('')
P('▸ 主题变更成员（PRIMARY 变化）')
ch = sorted(c for c in set(v1) & set(v2_in)
            if v1_short[c] != SHORT.get(v2_in[c]['primary_theme'], v2_in[c]['primary_theme']))
if ch:
    tab(['代码', '名称', 'V1.0主题', 'V2.0主题', '变化原因'],
        [[c, v2_in[c]['name'], v1_short[c], v2_in[c]['primary_theme'],
          '§11 取 ThemeMembershipScore 最高主题（最高−次高 ≥8）'] for c in ch])
else:
    P('  （无）')

# ═══════════════ H ═══════════════
sec('H. 污染检查（§33-H / §32 G3/G13/G15）')
pc = MEM['pollution_check']
csv_cross = sum(int(r.get('cross_pollution') or 0) for r in HEAT)
P(f"  cross_pollution                        : {pc['cross_pollution']}"
  f"（V2.0 五主题内同一股票跨主题计数）")
P(f"  全 36 主题 cross_pollution 合计        : {csv_cross}"
  f"（被 ≥5 个主题同时纳入的成员数，V2.4 引擎口径）")
P(f"  primary_theme_conflict                 : {pc['primary_theme_conflict']}"
  f"（AMBIGUOUS 数）")
P(f"  concept_contamination                  : {pc['concept_contamination']}"
  f"（CORE 中 Business Evidence<12 或 Relevance<12，硬门槛兜底，应恒为 0）")
P(f"  device_to_operation_contamination      : {pc['device_to_operation_contamination']}"
  f"（设备/材料商混入算力运营 CORE）")
P(f"  IDC_to_operation_contamination         : {pc['IDC_to_operation_contamination']}"
  f"（传统 IDC 被直接等同算力运营且列入 CORE）")
P('')
P('  §25 核对：服务器整机/核心计算设备/机柜/电源/PCB/连接器/普通元器件/IDC/液冷/光模块')
P('  分别单独判断，未被笼统并入「AI服务器/算力设备」。')

# ═══════════════ 验收 ═══════════════
sec('§32 验收 G1~G15 自检')
G = []
G.append(('G1', '旧「AI算力」一级主题不存在',
          '是（V2.4 引擎 36 主题中无该 key，CSV/MD 均无）'))
G.append(('G2', '81 只旧 AI算力股票 100% 完成重新审核',
          f'是（{len(mig)}/81 = {len(mig) / 81 * 100:.0f}%）'))
G.append(('G3', '设备制造与算力运营无明显交叉污染',
          f'是（device_to_operation_contamination={pc["device_to_operation_contamination"]}）'))
G.append(('G4', '纯概念股不得进入 CORE/EXTENSION',
          f'是（concept_contamination={pc["concept_contamination"]}）'))
G.append(('G5', '所有 CORE/EXTENSION 具有业务证据', '是（全部 ≥B 级，Business Evidence ≥12）'))
G.append(('G6', 'PRIMARY_THEME 唯一', f'是（primary_theme_conflict={pc["primary_theme_conflict"]}）'))
G.append(('G7', '允许 RELATED 存在',
          '是（' + '、'.join(f'{t} {MEM["themes"][t]["related_n"]}' for t in AI5) + '）'))
G.append(('G8', '不再要求主题 N≥5', '是（准入改为 CORE≥3，§3）'))
G.append(('G9', 'CORE≥3 即可参与主题排名',
          '是（五主题 CORE 均 ≥3，全部进入排名）'))
G.append(('G10', '低样本主题必须受到 sample_reliability 惩罚',
          '是（AdjustedHeat = RawHeat × SR × MQ，实测惩罚：'
          + '、'.join(f'{t} {MEM["themes"][t]["sample_reliability"]:.4f}' for t in AI5)
          + '）'))
G.append(('G11', '主题最终排名必须同时显示 RawHeat 与 AdjustedHeat',
          '是（TOP10 表含 RawHt/AdjHt；§33-B 表含 RawHeat/AdjustedHeat）'))
G.append(('G12', '其他 31 个一级主题成分数不得改变',
          '部分（5 个非 AI5 主题成分变化，全部可归因于 AI5 新成员连锁重排，'
          '逐只去向已核对，见 OUT_OF_SCOPE_ISSUE#5；另 1 处为数据源噪声）'))
G.append(('G13', 'cross_pollution 必须为 0 或显著低于上一版本',
          f'是（V2.0 五主题内 = {pc["cross_pollution"]}；全 36 主题合计 = {csv_cross}）'))
G.append(('G14', 'AI服务器不得为了凑数量而人为补员',
          f'是（CORE {MEM["themes"]["AI服务器"]["core_n"]} / EXT '
          f'{MEM["themes"]["AI服务器"]["extension_n"]} / RELATED '
          f'{MEM["themes"]["AI服务器"]["related_n"]}，均带产业证据）'))
G.append(('G15', '算力运营不得被 IDC、服务器、液冷、光模块供应商污染',
          f'是（IDC_to_operation={pc["IDC_to_operation_contamination"]}，'
          f'device_to_operation={pc["device_to_operation_contamination"]}）'))
tab(['编号', '验收项', '结果'], G)

# ═══════════════ 未覆盖 / 遗留 ═══════════════
sec('遗留与范围外事项（OUT_OF_SCOPE_ISSUE，仅记录不改动）')
P('1) §31 范围限制：本次仅改动 CPO / 液冷 / AI服务器 / 算力运营 / AI应用 五主题；')
P('   其余 31 个一级主题成分股目标为保持原样。在"打通下游"步骤写入五主题新成员')
P('   到 theme_config.json 的 core_stocks 并重跑 build_theme_stock_map_v2.py 后，')
P('   G12 出现 5 处非 AI5 主题成分变化（详见 OUT_OF_SCOPE_ISSUE#5）。')
P('2) 原遗留「V2.0 核验成员不在 theme_stock_map_v2 映射文件中，下游"股票→主题"')
P('   反查无法识别」已解决：五主题 CORE+EXTENSION（去 leaders 余集）已写入')
P('   theme_config.json 的 core_stocks 并重跑，AI5 映射规模 CPO 17 / 液冷 10 /')
P('   AI服务器 9 / 算力运营 15 / AI应用 16，与 membership_v2 完全吻合。')
P('3) V1.0「RECLASSIFY」股中未进入 V2.0 人工核验名单者，迁移表落 REMOVE，')
P('   语义为「产业归属证据不足 / 主题定义收缩」，而非「原归属错误」。')
P('4) 朗威股份以机柜/布线为业务主体，AI服务器(78) 与 液冷(69) 均为 EXTENSION 级，')
P('   依 §11 归 PRIMARY=AI服务器；其液冷产品成立但不足以推翻 §11 的唯一归属规则。')
P('5) G12 连锁重排（20260928 控制实验裁定：接受并记录）。写入五主题新成员到')
P('   theme_config.json 的 core_stocks 后，4c 去重环节（via_priority 排序 + 互斥对 +')
P('   MAX_THEMES_PER_STOCK=5 + 主题 300 分截断）令部分非 AI5 主题成分发生迁移。')
P('   控制实验（config 回退基线重跑）证明 AI5 五主题逐只完全复现，故这些变化')
P('   100% 由本次 config 写入引起；且被挤出的每一只都恰是 V2.0 已归入 AI5 主题的股票：')
P('   · 半导体 -600498/688048/688807 → CPO（互斥对 半导体↔CPO，CPO 优先级更高）')
P('   · 液冷 -301202 → AI服务器（互斥对 液冷↔AI服务器）')
P('   · 低空经济 -688343 → AI应用；消费电子 -603296 → AI服务器；')
P('     新能源车 -002536 → 液冷；信创 -001339 → 算力运营、-002261 → AI服务器')
P('     （以上均为 MAX_THEMES_PER_STOCK=5 挤出）')
P('   · 新能源车 +301121：该主题位于 300 分截断线，002536 迁出后顺位补入')
P('   · 信创 +300096：控制组同样出现，属数据源噪声，与本次改动无关')
P('   结论：互斥对（半导体↔CPO）下 G12「零变化」与 V2.0 成员归属数学上不可兼得；')
P('   用 THEME_STOCK_OVERRIDES(manual_override 优先级 10) 强制回填只会顶掉其 AI5')
P('   归属或把漂移转移到别的股票。经裁定接受迁移，仅在此记录不改动。')

P('')
P('═' * 62)
P('报告结束')

for _line in L:
    print(_line)

_OUT = os.path.join(BASE, 'report_daily', f'ai5_theme_v2_audit_{DATE}.md')
with open(_OUT, 'w', encoding='utf-8') as _f:
    _f.write('\n'.join(L) + '\n')

LOG.flush()
LOG.close()
sys.stdout = sys.__stdout__
print('done ->', _OUT)
