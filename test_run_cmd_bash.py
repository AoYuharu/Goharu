"""Verify run_cmd now executes via Git Bash: multi-line python -c must produce real output."""
import asyncio
from Tools.builtin.core_tools import run_cmd

# 之前 cmd.exe 会 mangling 的多行 python -c（"30 个全是错的" 事件的同一类命令）
MULTILINE_PY = '''python -c "
B='B';D='.';S='S';E='E';X='X'
l1='B'*26
l2='B'+'.'*24+'B'
rows=[l1,l2]
print('rows:',len(rows))
for i,r in enumerate(rows): print(i+1,len(r))
"'''

async def main():
    # 1) 多行 python -c
    out = await run_cmd(MULTILINE_PY, "test multi-line python -c")
    print("=== multi-line python -c ===")
    print(out)

    # 2) 残留 Windows 命令转译后执行（dir -> ls）
    out2 = await run_cmd("dir", "test dir translation")
    print("=== dir -> ls ===")
    print(out2)

    # 3) 标准 POSIX 命令
    out3 = await run_cmd("echo hello-from-bash", "test echo")
    print("=== echo ===")
    print(out3)

if __name__ == "__main__":
    asyncio.run(main())
