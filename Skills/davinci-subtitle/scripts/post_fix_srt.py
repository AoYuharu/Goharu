"""对已生成的 SRT 做繁转简（zhconv），仅改文本行。"""
import re
import sys
from pathlib import Path

from zhconv import convert

TIME_RE = re.compile(r"^\d+:\d+:\d+,\d+ --> \d+:\d+:\d+,\d+$")


def main(src: str, dst: str):
    lines = Path(src).read_text(encoding="utf-8").splitlines()
    out = []
    changed = 0
    for ln in lines:
        if not ln.strip() or ln.strip().isdigit() or TIME_RE.match(ln.strip()):
            out.append(ln)
            continue
        conv = convert(ln, "zh-cn")
        if conv != ln:
            changed += 1
        out.append(conv)
    Path(dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"converted lines={changed} -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
