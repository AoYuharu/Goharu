"""清洗 SRT：过滤 Whisper 幻觉（超长重复字符/超长文本/重复短语）。"""
import re
import sys
from pathlib import Path

TIME_RE = re.compile(r"^\d+:\d+:\d+,\d+ --> \d+:\d+:\d+,\d+$")
MAX_LEN = 50
MAX_RUN = 12
MAX_REPEAT = 4


def longest_run(text: str) -> int:
    best = 1
    cur = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def max_phrase_repeat(text: str, sizes=(2, 3, 4)) -> int:
    best = 1
    for n in sizes:
        for i in range(0, len(text) - n + 1):
            pat = text[i:i + n]
            if not pat.strip():
                continue
            cnt = text.count(pat)
            best = max(best, cnt)
    return best


def is_bad(text: str) -> bool:
    t = text.strip()
    if len(t) > MAX_LEN:
        return True
    if longest_run(t) >= MAX_RUN:
        return True
    if max_phrase_repeat(t) >= MAX_REPEAT + 2:
        return True
    return False


def main(src: str, dst: str):
    blocks = re.split(r"\n\s*\n", Path(src).read_text(encoding="utf-8").strip())
    kept = []
    dropped = []
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        text = " ".join(lines[2:])
        if is_bad(text):
            dropped.append((lines[1], text[:60]))
            continue
        kept.append((lines[1], text))
    out = []
    for i, (tm, text) in enumerate(kept, 1):
        out += [str(i), tm, text, ""]
    Path(dst).write_text("\n".join(out), encoding="utf-8")
    print(f"保留 {len(kept)} 条，丢弃 {len(dropped)} 条 -> {dst}")
    for tm, t in dropped:
        print(f"  丢弃 {tm} | {t}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
