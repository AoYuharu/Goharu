"""
测试提示词一致性：确保没有鼓励使用 echo >> 修改文件的内容
"""
import sys
import codecs
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())


def test_prompt_consistency():
    """检查提示词中是否存在冲突的指令"""
    print("检查提示词一致性...")

    issues = []

    # 检查 actor base.md
    actor_prompt = Path("prompts/actor/base.md").read_text(encoding="utf-8")

    # 不应该鼓励使用 echo >> 追加内容
    if "追加内容: echo" in actor_prompt or "Append: echo" in actor_prompt:
        issues.append("prompts/actor/base.md 中仍然包含鼓励使用 echo 追加内容的示例")

    # 应该明确禁止使用 echo >> 修改文件
    if "禁止使用 `echo >` 或 `echo >>`" not in actor_prompt:
        issues.append("prompts/actor/base.md 中没有明确禁止使用 echo 修改文件")

    # 应该强调必须使用 Read + Edit
    if "必须使用 Read + Edit 组合" not in actor_prompt and "必须使用 `Read` + `Edit` 组合" not in actor_prompt:
        issues.append("prompts/actor/base.md 中没有强调必须使用 Read + Edit 组合")

    print(f"[OK] 检查 prompts/actor/base.md")

    # 检查 core_tools.py
    core_tools = Path("Tools/builtin/core_tools.py").read_text(encoding="utf-8")

    # 不应该在示例中包含 echo >> 追加
    if "Append: echo more >>" in core_tools:
        issues.append("Tools/builtin/core_tools.py 中仍然包含 'Append: echo >>' 示例")

    # 不应该鼓励使用 echo >> 创建多行文件
    if "echo line1 > file.txt && echo line2 >> file.txt" in core_tools:
        issues.append("Tools/builtin/core_tools.py 中仍然鼓励使用 echo >> 创建多行文件")

    # 应该明确说明不要用 echo 修改已存在的文件
    if "DO NOT use echo > or echo >> to modify existing files" not in core_tools:
        issues.append("Tools/builtin/core_tools.py 中没有明确禁止使用 echo 修改已存在的文件")

    print(f"[OK] 检查 Tools/builtin/core_tools.py")

    # 报告结果
    if issues:
        print("\n[FAIL] 发现以下问题：")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("\n[OK] 提示词一致性检查通过")
        print("  - 已明确禁止使用 echo >> 修改文件")
        print("  - 已强调必须使用 Read + Edit 组合")
        print("  - 工具描述与提示词保持一致")
        return True


if __name__ == "__main__":
    try:
        success = test_prompt_consistency()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
