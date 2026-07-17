"""清理 prompt 中的联网搜索要求，供无联网能力的模型（DeepSeek）使用"""
import re


def strip_web_search_requirements(prompt):
    """从 prompt 中移除/替换所有联网搜索相关要求，避免模型凭空编造数据

    返回清理后的 prompt，适用于无联网能力的模型
    策略：行级扫描 + 段落级替换，不依赖具体编号和格式
    """
    lines = prompt.split('\n')
    result_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # ===== 跳过整段的联网要求 =====
        # 第3部分：【联网风险与舆情核查-强制】段落
        if '【联网风险与舆情核查' in line and line.strip().startswith('- '):
            # 跳过这一段，直到遇到下一个顶层指令（以 "- <span" 或 "- 如遇" 或 "- 【重要提醒" 或 空行 开头）
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if (next_line.strip().startswith('- <span') or
                    next_line.strip().startswith('- 如遇') or
                    next_line.strip().startswith('- 【重要提醒') or
                    (next_line.strip() == '' and i + 1 < len(lines) and
                     not lines[i + 1].strip().startswith('* '))):
                    break
                i += 1
            continue

        # ETF 成份股联网核查段落
        if '【ETF成份股联网风险核查' in line and line.strip().startswith('- '):
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if next_line.strip().startswith('- 【') or next_line.strip() == '':
                    # 检查空行后是否是新的指令段
                    if next_line.strip() == '' and i + 1 < len(lines):
                        n2 = lines[i + 1]
                        if n2.strip().startswith('- 【') or n2.strip().startswith('**【'):
                            break
                    if next_line.strip().startswith('- 【'):
                        break
                i += 1
            continue

        # ETF 本身风险核查
        if '【ETF本身风险核查】' in line:
            i += 1
            continue

        # 量能爆发池的第5行风险舆情说明
        if '第5行：风险舆情' in line and line.strip().startswith('- '):
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if ('格式示例' in next_line or
                    (next_line.strip() == '' and i + 1 < len(lines) and '格式示例' in lines[i + 1])):
                    break
                i += 1
            continue

        # ===== 行级替换 =====
        # 替换"实时风险与热点扫描"标题（不依赖编号）
        if '【实时风险与热点扫描】' in line and '必须调用联网搜索' in line:
            line = re.sub(
                r'(\d+、\*\*【实时风险与热点扫描】\*\*)（必须调用联网搜索[^）]*）',
                r'\1（基于提供的数据进行分析，不得编造外部信息）',
                line
            )

        # 替换风险扫描标题
        if '【风险扫描' in line and '联网核查' in line:
            line = re.sub(r'\*\*【风险扫描[^】]*】\*\*（联网[^）]*）',
                          '**【风险扫描-汇总】**（基于提供的数据判断）', line)

        # 替换热点追踪标题
        if '【热点追踪】' in line and '联网搜索' in line:
            line = line.replace('热点追踪', '热点复盘')
            line = re.sub(r'（联网搜索[^）]*）',
                          '（基于提供的数据分析，不得编造外部信息）', line)

        # 替换各子项中的"联网搜索"措辞
        if '- 【个股利空】' in line and '联网' in line:
            line = '- 【个股风险】基于提供的数据判断股池个股风险'
            # 跳过后续的子项说明
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【主题风险】') or lines[i].strip().startswith('- 【大盘'):
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【主题风险】' in line and ('核查' in line or '联网' in line):
            line = '- 【主题风险】基于提供的主题数据分析拥挤度和趋势变化'
            # 跳过后续的子项说明
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【大盘') or lines[i].strip().startswith('* 格式'):
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【大盘系统性风险】' in line and ('核查' in line or '联网' in line):
            line = '- 【大盘系统性风险】基于提供的大盘数据分析'
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【') or lines[i].strip() == '':
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【今日涨停原因】' in line and '联网搜索' in line:
            line = '- 【今日涨停原因】基于提供的数据分析涨停股所属主题和驱动因素'
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【明日') or lines[i].strip().startswith('- 【潜在'):
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【明日潜在热点】' in line and '联网搜索' in line:
            line = '- 【明日关注方向】基于主题生命周期和趋势强度判断'
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【龙头') or lines[i].strip().startswith('- 【市场'):
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【龙头股动态】' in line and '联网搜索' in line:
            line = '- 【龙头股动态】基于提供的数据说明龙头股表现'
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith('- 【市场情绪】'):
                    break
                i += 1
            result_lines.append(line)
            continue

        if '- 【市场情绪】' in line and '联网搜索' in line:
            line = '- 【市场情绪】基于提供的涨跌停、炸板率等数据判断'
            i += 1
            while i < len(lines):
                if lines[i].strip() == '' or lines[i].startswith('格式') or '情绪指标' in lines[i]:
                    break
                i += 1
            result_lines.append(line)
            continue

        # 移除格式示例中的风险舆情行
        if line.strip().startswith('风险舆情：风险='):
            i += 1
            continue

        # 5行改4行
        if '每只精简为1小段（5行）' in line:
            line = line.replace('5行', '4行')

        result_lines.append(line)
        i += 1

    return '\n'.join(result_lines)


if __name__ == '__main__':
    # 用最新的 prompt 测试
    with open(r"d:\mystock\cache_daily\prompt_debug_20260716.txt", "r", encoding="utf-8") as f:
        prompt = f.read()

    cleaned = strip_web_search_requirements(prompt)

    print(f"原始长度: {len(prompt)} 字符")
    print(f"清理后长度: {len(cleaned)} 字符")
    print(f"减少: {len(prompt) - len(cleaned)} 字符")
    print()

    # 检查残留
    web_terms = re.findall(r'.{0,5}联网.{0,10}', cleaned)
    if web_terms:
        print(f"❌ 仍有 {len(web_terms)} 处'联网'残留:")
        for t in web_terms[:20]:
            print(f"  - {t.strip()}")
    else:
        print("✅ 无'联网'关键词残留")

    # 检查关键标题
    if '实时风险与热点扫描' in cleaned:
        print("❌ 仍有'实时风险与热点扫描'标题")
    else:
        print("✅ 已替换热点扫描标题")

    if '热点追踪' in cleaned:
        print("❌ 仍有'热点追踪'")
    else:
        print("✅ 已替换热点追踪")

    if '东山精米' in cleaned:
        print("❌ 测试数据中有东山精米（不应出现）")

    # 保存
    with open(r"d:\mystock\cache_daily\prompt_cleaned_test_0716.txt", "w", encoding="utf-8") as f:
        f.write(cleaned)
    print("\n清理结果已保存到: prompt_cleaned_test_0716.txt")
