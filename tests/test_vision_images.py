"""视觉链路测试：截图图像块的透传与清扫。"""
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    _r = getattr(_s, "reconfigure", None)
    if _r:
        _r(encoding="utf-8")

from Tools import config_proxy
from Tools.mcp import manager as mcp_manager
from Agent.ActorAgent import strip_stale_image_blocks
from Core.ToolCall import ToolCall

PASS = True


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if not cond:
        PASS = False
    print(f"  {status}  {name}" + (f" | {detail}" if detail and not cond else ""))


def _fake_result(blocks):
    return SimpleNamespace(content=blocks, isError=False)


_PNG = base64.b64encode(b"\x89PNG" * 100).decode()


def main():
    print("[1] vision=off：占位符丢弃（旧行为）")
    config_proxy.set_config({"model": {"large-language-model": {"vision": False}}})
    mcp_manager.pop_pending_images()  # 清空
    text = mcp_manager._serialize_result(_fake_result([
        SimpleNamespace(type="image", mimeType="image/png", data=_PNG),
    ]))
    check("文本含占位符", "截图已被丢弃" in text, text[:80])
    check("无暂存图像", mcp_manager.pop_pending_images() == [])

    print("[2] vision=on：图像暂存 + 文本引导")
    config_proxy.set_config({"model": {"large-language-model": {"vision": True}}})
    text = mcp_manager._serialize_result(_fake_result([
        SimpleNamespace(type="text", text="ok"),
        SimpleNamespace(type="image", mimeType="image/png", data=_PNG),
    ]))
    check("文本告知已附加", "图像已附加" in text, text[:80])
    imgs = mcp_manager.pop_pending_images()
    check("暂存 1 张", len(imgs) == 1)
    check("块结构是 Anthropic image", imgs and imgs[0]["type"] == "image"
          and imgs[0]["source"]["media_type"] == "image/png"
          and imgs[0]["source"]["data"] == _PNG)
    check("取出后清空", mcp_manager.pop_pending_images() == [])

    print("[3] create_anthropic_tool_result 透传 list 内容")
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
              {"type": "text", "text": "ok"}]
    tr = ToolCall.create_anthropic_tool_result("toolu_1", blocks)
    check("list 原样透传", tr["content"] is blocks or tr["content"] == blocks)
    tr2 = ToolCall.create_anthropic_tool_result("toolu_2", {"a": 1})
    check("dict 仍转 JSON 字符串", tr2["content"] == '{"a": 1}')

    print("[4] strip_stale_image_blocks：只留最新 user 消息的图")
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}
    msgs = [
        {"role": "user", "content": "最早的问题"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "browser_screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                       "content": [img, {"type": "text", "text": "截图ok"}]}]},
        {"role": "assistant", "content": [{"type": "text", "text": "看到了"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2",
                                       "content": [img, {"type": "text", "text": "最新截图"}]}]},
    ]
    out = strip_stale_image_blocks(msgs)
    old_tr = out[2]["content"][0]["content"]
    check("历史 tool_result 里的图被替换", old_tr[0]["type"] == "text" and "已省略" in old_tr[0]["text"])
    check("历史文本块保留", old_tr[1]["text"] == "截图ok")
    new_tr = out[4]["content"][0]["content"]
    check("最新 user 消息的图保留", new_tr[0]["type"] == "image")

    print("[5] 无图像/空列表不炸")
    check("空列表", strip_stale_image_blocks([]) == [])
    plain = [{"role": "user", "content": "纯文本"}]
    check("纯文本原样", strip_stale_image_blocks(plain) == plain)

    config_proxy.reset_config()
    print("\n" + ("全部 PASS" if PASS else "存在 FAIL"))
    sys.exit(0 if PASS else 1)


if __name__ == "__main__":
    main()
