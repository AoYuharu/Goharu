"""按 clips.json 批量提取片段音频（16k mono wav），并核对时长。

用法：
    python extract_audio.py --clips clips.json --audio audio_dir [--fps 59.94]
"""
import argparse
import json
import wave
from pathlib import Path

import imageio_ffmpeg
import subprocess

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True)
    ap.add_argument("--audio", required=True)
    args = ap.parse_args()

    meta = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    fps = meta["fps"]
    out_dir = Path(args.audio)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = fail = skip = 0
    bad = []
    for i, clip in enumerate(meta["clips"], 1):
        out = out_dir / f"clip{i:02d}.wav"
        expect = clip["dur"] / fps
        if out.exists() and out.stat().st_size > 1000:
            skip += 1
        else:
            cmd = [FFMPEG, "-y", "-v", "error",
                   "-ss", f"{clip['src_start'] / fps:.4f}", "-i", clip["path"],
                   "-t", f"{expect:.4f}", "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", str(out)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                ok += 1
            else:
                fail += 1
                print("FAIL", i, clip["name"], r.stderr[:200])
                continue
        with wave.open(str(out)) as w:
            got = w.getnframes() / w.getframerate()
        if abs(got - expect) > 0.15:
            bad.append((i, round(expect, 2), round(got, 2), clip["name"]))
    print(f"extracted={ok} skipped={skip} failed={fail} -> {out_dir}")
    if bad:
        print(f"时长不符 {len(bad)} 段:")
        for b in bad:
            print("  ", b)


if __name__ == "__main__":
    main()
