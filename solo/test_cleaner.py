"""用真实 prompt 测试清理效果"""
import sys
sys.path.insert(0, r"d:\mystock\solo")
from ai_prompt_cleaner import strip_web_search_requirements

with open(r"d:\mystock\cache_daily\prompt_debug_20260715.txt", "r", encoding="utf-8") as f:
    prompt = f.read()

cleaned = strip_web_search_requirements(prompt)

print(f"原始长度: {len(prompt)} 字符")
print(f"清理后长度: {len(cleaned)} 字符")
print(f"减少: {len(prompt) - len(cleaned)} 字符")
print()

# 检查是否还有残留的"联网"关键词
import re
web_terms = re.findall(r'联网[^，。\n]{0,10}', cleaned)
if web_terms:
    print(f"❌ 仍有 {len(web_terms)} 处'联网'残留:")
    for t in web_terms[:20]:
        print(f"  - {t}")
else:
    print("✅ 无'联网'关键词残留")

# 检查是否还有"编造"风险词
fabricate_terms = re.findall(r'(联网搜索|必须调用联网|逐个核查|联网核查)', cleaned)
if fabricate_terms:
    print(f"❌ 仍有编造风险词: {fabricate_terms[:10]}")
else:
    print("✅ 无高风险编造词汇")

# 保存对比
with open(r"d:\mystock\cache_daily\prompt_cleaned_test.txt", "w", encoding="utf-8") as f:
    f.write(cleaned)
print("\n清理结果已保存到: prompt_cleaned_test.txt")
