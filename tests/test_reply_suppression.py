"""群聊屏蔽词判定与 harunet_reply 出口护栏。

运行：python tests/test_reply_suppression.py
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Core.reply_suppression import is_group_suppress_text, normalize_answer  # noqa: E402
from Tools.builtin.harunet_tools import harunet_reply  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    print("=" * 60)
    print("群聊屏蔽词测试")
    print("=" * 60)

    print("\n[1] 判定")
    check("纯文本", is_group_suppress_text("不回复"))
    check("Markdown 加粗", is_group_suppress_text("**不回复**"))
    check("句号", is_group_suppress_text("不回复。"))
    check("同义短语", is_group_suppress_text("无需回复"))
    check("带正文不拦截", not is_group_suppress_text("不回复，因为这件事与我无关。"))
    check("空文本不拦截", not is_group_suppress_text(""))
    check("normalize 去空白", normalize_answer(" 不 回 复 ") == "不回复")

    print("\n[2] harunet_reply 护栏")
    raw = asyncio.run(harunet_reply(text="不回复"))
    data = json.loads(raw)
    check("屏蔽词不发送", data.get("suppressed") is True and not data.get("ok"), raw)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
