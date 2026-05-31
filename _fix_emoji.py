path = r'D:\mystock\etf_quant.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Remove emoji from main() - replace with ASCII
replacements = {
    '\U0001f4cb': '[持仓]',  # clipboard
    '\U0001f4d6': '[报告]',  # book
    '\U0001f4ca': '[快照]',  # chart
    '\U0001f534': '[!!止损!!]',  # red circle
    '\U0001f7e2': '[!!止盈!!]',  # green circle
    '\u26a0\ufe0f': '[警告]',  # warning
    '\u26a0': '[警告]',
    '\u2705': '[OK]',  # check
}

for old, new in replacements.items():
    content = content.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("OK - emojis replaced")
