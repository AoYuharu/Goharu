"""用 transformers 的 Whisper 转录「环境音与声音」轨片段，输出对齐时间线的 SRT。

不依赖 ctranslate2 / faster-whisper，纯 torch。
用法：python transcribe_hf.py --model openai/whisper-large-v3 --limit 3
"""
import argparse
import json
import os
import wave
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(r"E:\Davinci\_asr")
AUDIO_DIR = ROOT / "audio"

# Whisper 在静音/噪音段常见的幻觉输出，命中即丢弃
HALLUCINATIONS = (
    "明镜", "点点栏目", "请不吝点赞", "订阅", "转发", "打赏",
    "字幕由", "amara.org", "感谢观看", "谢谢观看", "下期再见",
)

try:
    from zhconv import convert as _zh_convert
except Exception:  # 没装 zhconv 时退化为不做转换
    _zh_convert = None


def to_simplified(text: str) -> str:
    return _zh_convert(text, "zh-cn") if _zh_convert else text


def is_hallucination(text: str) -> bool:
    t = text.replace(" ", "")
    if len(t) < 2:
        return True
    return any(h in t for h in HALLUCINATIONS)


def load_wav(path: Path):
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def fmt(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/whisper-large-v3")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "srt" / "changsha_env_whisper.srt"))
    ap.add_argument("--start", type=int, default=1, help="从第几个片段开始（1-based）")
    ap.add_argument("--clips", default=str(ROOT / "clips.json"))
    ap.add_argument("--audio", default=str(AUDIO_DIR))
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

    clips_path = Path(args.clips)
    audio_dir = Path(args.audio)
    meta = json.loads(clips_path.read_text(encoding="utf-8"))
    fps = meta["fps"]
    tl_start = meta["tl_start"]
    clips = meta["clips"]

    print(f"加载模型 {args.model} …")
    proc = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        args.model, torch_dtype=torch.float16, low_cpu_mem_usage=True
    ).to("cuda")
    model.eval()
    print("模型就绪")

    idx_from = args.start
    idx_to = len(clips) if not args.limit else min(len(clips), args.start + args.limit - 1)

    segments = []
    for i in range(idx_from, idx_to + 1):
        clip = clips[i - 1]
        wav = audio_dir / f"clip{i:02d}.wav"
        if not wav.exists():
            print(f"[{i}] 缺音频 {wav.name}")
            continue
        audio, sr = load_wav(wav)
        inputs = proc(audio, sampling_rate=sr, return_tensors="pt").input_features
        inputs = inputs.to("cuda", dtype=torch.float16)
        with torch.no_grad():
            ids = model.generate(inputs, language="zh", task="transcribe",
                                 return_timestamps=True)
        decoded = proc.decode(ids[0], output_offsets=True, skip_special_tokens=True)
        # transformers >=4.4x 返回 offsets（旧版返回 chunks）
        chunks = decoded.get("offsets") or decoded.get("chunks") or []
        clip_dur = clip["dur"] / fps
        offset = (clip["start"] - tl_start) / fps
        n = 0
        for ch in chunks:
            text = to_simplified(ch.get("text", "").strip())
            ts = ch.get("timestamp") or (None, None)
            if not text or ts[0] is None or is_hallucination(text):
                continue
            end = ts[1] if ts[1] is not None else ts[0] + 1.0
            end = min(end, clip_dur)
            if end - ts[0] < 0.2:
                continue
            segments.append((offset + ts[0], offset + end, text))
            n += 1
        print(f"[{i}/{idx_to}] {clip['name']} {clip_dur:.1f}s -> {n} 条")

    segments.sort(key=lambda x: x[0])
    lines = []
    for i, (s, e, t) in enumerate(segments, 1):
        if e - s < 0.2:
            e = s + 0.6
        lines += [str(i), f"{fmt(s)} --> {fmt(e)}", t, ""]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n写入 {out}，共 {len(segments)} 条")


if __name__ == "__main__":
    main()
