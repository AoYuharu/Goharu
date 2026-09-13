B='B'
D='.'
S='S'
E='E'
X='X'

# 生成5个关卡，每关18行，每行26列
all_levels = []

for lvl in range(5):
    level = []
    for row in range(18):
        if row == 0 or row == 17:
            # 边框
            s = B * 26
        elif row == 8 or row == 9:
            # 空行
            s = B + D * 24 + B
        elif row == 3 or row == 14:
            # 空行
            s = B + D * 24 + B
        else:
            # 中间区域
            s = B + D * 24 + B
        level.append(s)
    all_levels.append(level)

# 输出
for i, lvl in enumerate(all_levels):
    for j, row in enumerate(lvl):
        ok = len(row) == 26
        if not ok:
            print(f"L{i+1}R{j+1}: {len(row)} BAD")

all_ok = all(len(r) == 26 for lvl in all_levels for r in lvl)
print(f"All: {all_ok}, Total rows: {len(all_levels) * 18}")

# 输出JS格式
print("const LEVELS=[")
for i, lvl in enumerate(all_levels):
    row_str = "['" + "','".join(lvl) + "']"
    comma = "," if i < 4 else ""
    print(row_str + comma)
print("];")
