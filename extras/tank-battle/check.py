B='B';D='.';S='S';E='E';X='X'

# Level 1
l1=[
  B*26,
  B+D*24+B,
  B+S+S+D*20+S+S+B,
  B+S+S+D*9+E+D*9+S+S+B,
  B+D*24+B,
  B+B+B+D*5+B+B+D*5+B+B+D*5+B,
  B+B+B+D*7+B+B+D*7+B+B+B,
  B+D*7+E+D*7+E+D*7+B,
  B+B+B+D*5+B+B+B+B+B+B+D*5+B+B+B+B,
  B+B+B+D*5+B+B+B+B+B+B+D*5+B+B+B+B,
  B+D*24+B,
  B+B+B+D*5+B+B+D*5+B+B+D*5+B,
  B+B+B+D*7+B+B+D*7+B+B+B,
  B+D*24+B,
  B+S+S+D*20+S+S+B,
  B+S+S+D*18+X+B,
  B+D*22+X+B,
  B*26
]

# Verify
for i,r in enumerate(l1):
    ok = len(r)==26
    if not ok:
        print(f"Row {i+1}: {len(r)} chars - BAD")

# Print valid rows
valid = all(len(r)==26 for r in l1)
print(f"Level 1: {'ALL OK' if valid else 'HAS ERRORS'}")

# Print first 3
for i,r in enumerate(l1[:3]):
    print(f"{i+1}: '{r}'")
