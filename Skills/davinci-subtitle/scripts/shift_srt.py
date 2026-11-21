"""按固定秒数平移 SRT 时间码（用于把字幕追加到字幕轨指定位置）。

Resolve 的 AppendToTimeline 对字幕是"追加"语义：
    新条目起点 = 现有字幕轨最后一条的结束位置 + SRT 时间码
所以要让新字幕落在目标位置 target，需先令 SRT 时间码整体减去
    shift = 现有字幕轨最后一条的结束秒数
用法：python shift_srt.py in.srt out.srt --shift 770.5
"""
import argparse
import re
from pathlib import Path

TIME_RE = re.compile(r"^(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)$")


def fmt(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse(tc: str) -> float:
    h, m, rest = tc.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--shift", type=float, required=True)
    args = ap.parse_args()

    lines = Path(args.src).read_text(encoding="utf-8").splitlines()
    out, dropped = [], 0
    for ln in lines:
        m = TIME_RE.match(ln.strip())
        if not m:
            out.append(ln)
            continue
        s = parse(f"{m.group(1)}:{m.group(2)}:{m.group(3)},{m.group(4)}") - args.shift
        e = parse(f"{m.group(5)}:{m.group(6)}:{m.group(7)},{m.group(8)}") - args.shift
        if e <= 0:
            dropped += 1
            out.append(ln)
            continue
        out.append(f"{fmt(max(s, 0))} --> {fmt(e)}")
    Path(args.dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"shifted by {args.shift}s -> {args.dst} (dropped={dropped})")


if __name__ == "__main__":
    main()
