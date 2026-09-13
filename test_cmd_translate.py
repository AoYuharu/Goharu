"""Command translation tests (Windows cmd → POSIX). Run: python test_cmd_translate.py"""
from Tools import cmd_translate as ct

FAILED = 0

def check(name, cmd, expect):
    global FAILED
    translated, original = ct.translate_command(cmd)
    ok = translated == expect and (original is not None)
    if not ok:
        FAILED += 1
    print(f"{'PASS' if ok else 'FAIL'} {name}: {cmd!r} -> {translated!r}")

def check_passthrough(name, cmd):
    global FAILED
    translated, original = ct.translate_command(cmd)
    ok = translated == cmd and original is None
    if not ok:
        FAILED += 1
    print(f"{'PASS' if ok else 'FAIL'} {name}: {cmd!r} stayed {translated!r}")

# ── Windows cmd → POSIX ──────────────────────────────────────
check("dir", "dir", "ls")
check("dir /s /b", "dir /s /b src", "ls -R -1 src")
check("cls", "cls", "clear")
check("where", "where python", "which python")
check("tasklist", "tasklist", "ps aux")
check("taskkill", "taskkill /PID 42 /F", "kill -9 42")
check("taskkill soft", "taskkill /PID 42", "kill 42")
check("copy", "copy a b", "cp a b")
check("move", "move a b", "mv a b")
check("ren", "ren a b", "mv a b")
check("del", "del tmp.txt", "rm tmp.txt")
check("del force", "del /f tmp.txt", "rm -f tmp.txt")
check("set kv", "set A=1", "export A=1")
check("bare set", "set", "env")
check("ver", "ver", "uname -a")
check("env var", "dir %HOME%", "ls $HOME")
check("nul redirect", "dir > nul", "ls > /dev/null")
check("compound", "dir && cls", "ls && clear")
check("compound pipe", "dir | wc -l", "ls | wc -l")
check_passthrough("native ls", "ls -la")
check_passthrough("python cmd", "python script.py --flag")
check_passthrough("grep", "grep foo file")

# del /s 危险区不转译，放行给安全层
translated, original = ct.translate_command("del /s /q build")
print(f"{'PASS' if original is None else 'FAIL'} del /s not translated")
if original is not None:
    FAILED += 1

print()
print("FAILED:", FAILED)
raise SystemExit(1 if FAILED else 0)
