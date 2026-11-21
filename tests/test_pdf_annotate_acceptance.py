"""最终验收：真实 LLM 通过 GatewaySession 自主使用 PDF 批注工具并给出回复。

运行时间可能较长（LLM 多步 ReAct + 记忆系统 embedding 加载）。
成功后会将带批注的演示副本输出到 Desktop/T3Time_Analysis/T3Time_annotated_demo.pdf。
"""

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

SRC = Path.home() / "Desktop" / "T3Time.pdf"
WORK_DIR = Path("runtime_memory/temp/pdf_annot_acceptance")
WORK_PDF = WORK_DIR / "T3Time.pdf"
DEMO_OUT = Path.home() / "Desktop" / "T3Time_Analysis" / "T3Time_annotated_demo.pdf"

TASK = (
    f"请用 PDF 批注工具处理文件 {WORK_PDF.resolve()} ："
    "1) 把第 1 页中的 'Adaptive Multi-Head Alignment' 高亮为绿色；"
    "2) 在第 1 页 (60, 700) 附近放一条 freetext 中文批注：'方法核心：三模态对齐与残差融合'；"
    "3) 用 pdf_list_annotations 验证批注已写入。"
    "完成后简要告诉我做了什么。"
)


async def main():
    from Gateway.gateway_entry import GatewaySession

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, WORK_PDF)

    session = GatewaySession()
    await session.initialize()
    try:
        reply = await session.process_message(TASK)
        print("\n===== 最终回复 =====")
        print(reply)

        import fitz

        doc = fitz.open(str(WORK_PDF))
        counts = {}
        for i in range(doc.page_count):
            for a in doc[i].annots() or []:
                counts[a.type[1]] = counts.get(a.type[1], 0) + 1
        doc.close()
        print("\n===== 批注统计 =====")
        print(counts)

        ok_reply = bool(reply and reply.strip())
        ok_annot = counts.get("Highlight", 0) >= 1 and counts.get("FreeText", 0) >= 1
        if ok_annot:
            shutil.copy2(WORK_PDF, DEMO_OUT)
            print("演示文件:", DEMO_OUT)
        ok = ok_reply and ok_annot
        print(
            f"\n===== 验收{'通过' if ok else '失败'}"
            f"（回复={'有' if ok_reply else '无'}, 批注={'有' if ok_annot else '无'}）====="
        )
        sys.exit(0 if ok else 1)
    finally:
        await session.shutdown()


asyncio.run(main())
