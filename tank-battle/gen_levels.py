# Generate 26-column level data
B, D, S, E, X = 'B', '.', 'S', 'E', 'X'

l1 = [
    B*26,
    B + D*24 + B,
    B + S+S + D*20 + S+S + B,
    B + S+S + D*9 + E + D*9 + S+S + B,
    B + D*24 + B,
    B + B+B+B + D*5 + B+B + D*5 + B+B + D*7 + B,
    B + B+B+B + D*7 + B+B + D*7 + B+B+B,
    B + D*7 + E + D*7 + E + D*7 + B,
    B + B+B+B + D*5 + B+B+B+B+B+B + D*5 + B+B+B+B,
    B + B+B+B + D*5 + B+B+B+B+B+B + D*5 + B+B+B+B,
    B + D*24 + B,
    B + B+B+B + D*5 + B+B + D*5 + B+B + D*7 + B,
    B + B+B+B + D*7 + B+B + D*7 + B+B+B,
    B + D*24 + B,
    B + S+S + D*20 + S+S + B,
    B + S+S + D*18 + X + B,
    B + D*22 + X + B,
    B*26
]

# Level 2
l2 = [
    B*26,
    B + S + D*7 + S + D*8 + S + B,
    B + S + B+B + D*3 + S + B+B+B+B + D*3 + S + B+B + B,
    B + S + B+B + D*6 + S + D*6 + B+B + B,
    B + S + D*7 + E + D*3 + S + D*7 + E + B,
    B + D*24 + B,
    B + B+B+B + D*2 + S+S + D*6 + S+S + D*4 + B+B+B + B,
    B + B+B+B + D*2 + S+S + D*2 + E + D*2 + S+S + D*4 + B + B,
    B + B+B+B + D*7 + B+B + D*7 + B+B+B,
    B + D*24 + B,
    B + D*3 + B+B + D*5 + B+B + D*5 + B+B + D*3 + B,
    B + D*3 + B+B + D*5 + B+B + D*5 + B+B + D*3 + B,
    B + D*24 + B,
    B + S + D*7 + S + D*8 + S + B,
    B + S + B+B + D*3 + S + B+B+B+B + D*3 + S + B+B + B,
    B + S + B+B + D*12 + B + B,
    B + D*7 + E + D*10 + E + X+X + B,
    B*26
]

# Level 3
l3 = [
    B*26,
    B + D*2 + S+S + D*4 + S+S + D*4 + S+S + D*4 + S+S + B,
    B + D*2 + S+S + D*4 + S+S + D*4 + S+S + D*4 + S+S + B,
    B + D*24 + B,
    B + B+B+B + D*4 + B+B + D*5 + B+B + D*4 + B+B+B + B,
    B + B+B+B + D*4 + B+B + D*5 + B+B + D*4 + B+B+B + B,
    B + D*24 + B,
    B + D*4 + S+S + D*6 + S+S + D*6 + S+S + B,
    B + D*4 + S+S + D*2 + E + D*2 + S+S + D*2 + E + D*2 + S+S + B,
    B + D*4 + S+S + D*6 + S+S + D*6 + S+S + B,
    B + D*24 + B,
    B + B+B+B + D*4 + B+B + D*5 + B+B + D*4 + B+B+B + B,
    B + B+B+B + D*4 + B+B + D*5 + B+B + D*4 + B+B+B + B,
    B + D*24 + B,
    B + D*2 + S+S + D*4 + S+S + D*4 + S+S + D*4 + S+S + B,
    B + D*2 + S+S + D*4 + S+S + D*4 + S+S + D*4 + S+S + B,
    B + D*23 + X+X + B,
    B*26
]

# Level 4
l4 = [
    B*26,
    B + D*10 + S+S + D*10 + B,
    B + B+B+B + D*4 + S+S + D*4 + B+B + D*4 + S+S + D*3 + B,
    B + B+B+B + D*8 + B+B + D*8 + B+B+B,
    B + D*10 + E + D*10 + E + B,
    B + D*24 + B,
    B + B+B+B + D*2 + B+B + D*4 + B+B+B+B + D*4 + B+B + D*2 + B+B+B + B,
    B + B+B+B + D*2 + B+B + D*4 + B+B+B+B + D*4 + B+B + D*2 + B+B+B + B,
    B + D*24 + B,
    B + B+B+B + D*2 + B+B + D*4 + B+B+B+B + D*4 + B+B + D*2 + B+B+B + B,
    B + B+B+B + D*2 + B+B + D*4 + B+B+B+B + D*4 + B+B + D*2 + B+B+B + B,
    B + D*24 + B,
    B + D*10 + E + D*10 + E + B,
    B + B+B+B + D*8 + B+B + D*8 + B+B+B,
    B + B+B+B + D*4 + S+S + D*4 + B+B + D*4 + S+S + D*3 + B,
    B + D*10 + S+S + D*10 + B,
    B + D*10 + S+S + D*9 + E + X + B,
    B*26
]

# Level 5
l5 = [
    B*26,
    B + S+S + D+S+S + D+S+S + D+S+S + D+S+S + S+S + B,
    B + S+S + D+S+S + D+S+S + D+S+S + D+S+S + S+S + B,
    B + D*24 + B,
    B + D*4 + B+B + D*5 + E + D*5 + B+B + D*4 + B,
    B + D*4 + B+B + D*5 + E + D*5 + B+B + D*4 + B,
    B + D*24 + B,
    B + D*2 + S+S + D*7 + S+S + D*7 + S+S + D*2 + B,
    B + D*2 + S+S + D*3 + E + D*3 + S+S + D*3 + E + D*3 + S+S + D*2 + B,
    B + D*2 + S+S + D*7 + S+S + D*7 + S+S + D*2 + B,
    B + D*24 + B,
    B + D*4 + B+B + D*5 + E + D*5 + B+B + D*4 + B,
    B + D*4 + B+B + D*5 + E + D*5 + B+B + D*4 + B,
    B + D*24 + B,
    B + S+S + D+S+S + D+S+S + D+S+S + D+S+S + S+S + B,
    B + S+S + D+S+S + D+S+S + D+S+S + D+S+S + S+S + B,
    B + D*23 + X+X + B,
    B*26
]

# Output as JS array
def to_js(rows):
    return "['" + "','".join(rows) + "']"

print("const LEVELS=[")
print(to_js(l1) + ",")
print(to_js(l2) + ",")
print(to_js(l3) + ",")
print(to_js(l4) + ",")
print(to_js(l5))
print("];")

# Verify
all_levels = [l1, l2, l3, l4, l5]
for i, lvl in enumerate(all_levels):
    bad = [(j+1, len(r)) for j, r in enumerate(lvl) if len(r) != 26]
    if bad:
        print(f"Level {i+1} BAD: {bad}")
print("Done")
