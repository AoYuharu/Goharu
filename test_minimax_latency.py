"""MiniMax-M2.7 延迟基准测试：~80K token 上下文，连续请求测 TTFT / 总耗时 / 吞吐。

独立脚本，直接走 anthropic 兼容端点（api.minimaxi.com/anthropic），不依赖项目运行时。
用底层 stream 事件手动抓首个输出 token（thinking 模型 text_stream 会为空）。
"""
import re
import time
from pathlib import Path

import anthropic


def load_api_key():
    env = Path(r"E:\Goharu\.env").read_text(encoding="utf-8")
    m = re.search(r'ANTHROPIC_API_KEY\s*=\s*"([^"]+)"', env)
    if not m:
        raise RuntimeError("ANTHROPIC_API_KEY not found in .env")
    return m.group(1)


def main():
    client = anthropic.Anthropic(
        api_key=load_api_key(),
        base_url="https://api.minimaxi.com/anthropic",
        timeout=600.0,
    )

    # 英文 filler：实测约 5.8 字符/token，目标 ~80K token => ~465K 字符
    filler_para = (
        "The quick brown fox jumps over the lazy dog while the engineering team "
        "discusses the architecture of a distributed long-term memory system. "
    )
    para_chars = len(filler_para)
    target_chars = 465_000
    repeat = target_chars // para_chars + 1
    filler = (filler_para * repeat).strip()
    print(f"[filler] chars={len(filler):,}  est_tokens~={len(filler)//5.8:,.0f}", flush=True)

    question = (
        "\n\n请阅读上面的全部内容，然后用中文一句话总结其主题，"
        "并额外用中文写一段约 300 字的简短说明。"
    )

    N = 3
    for i in range(1, N + 1):
        print(f"\n===== 请求 {i}/{N} =====", flush=True)
        t0 = time.time()
        first_delta_ts = None   # 首个 content_block_delta（含 thinking）= 真·TTFT
        first_text_ts = None    # 首个 text_delta = 内容回复起点
        out_tokens = None
        err = None
        try:
            stream = client.messages.create(
                model="MiniMax-M2.7",
                max_tokens=2048,
                temperature=0.7,
                messages=[{"role": "user", "content": filler + question}],
                stream=True,
            )
            for event in stream:
                et = event.type
                if et == "content_block_delta":
                    if first_delta_ts is None:
                        first_delta_ts = time.time()
                    if getattr(event.delta, "type", None) == "text_delta" and first_text_ts is None:
                        first_text_ts = time.time()
                elif et == "message_delta":
                    u = getattr(event, "usage", None)
                    if u is not None:
                        out_tokens = getattr(u, "output_tokens", None)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        t_end = time.time()

        if err:
            print(f"[ERROR] {err}", flush=True)
            continue

        ttft = (first_delta_ts - t0) if first_delta_ts else None
        tt_text = (first_text_ts - t0) if first_text_ts else None
        total = t_end - t0

        if ttft is not None:
            print(f"  TTFT (首个token)  = {ttft*1000:.0f} ms", flush=True)
        if tt_text is not None:
            print(f"  首文本token       = {tt_text*1000:.0f} ms", flush=True)
        print(f"  output_tokens     = {out_tokens}", flush=True)
        print(f"  总耗时             = {total*1000:.0f} ms", flush=True)
        if ttft is not None and out_tokens:
            gen = total - ttft
            if gen > 0:
                print(f"  生成吞吐           = {out_tokens/gen:.1f} tok/s", flush=True)


if __name__ == "__main__":
    main()
