"""
测试任务引导系统集成
"""
import sys
import codecs
if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from Tools.task_guide import TaskGuide


def test_read_detection():
    """测试重复读取检测"""
    guide = TaskGuide()

    # 模拟 Read 工具调用
    warning_triggered = False
    for i in range(5):
        warning = guide.record_tool_call("Read", {
            "path": "test.py",
            "start_line": 1,
            "end_line": 10
        })

        if i < 3:
            assert warning is None, f"第 {i+1} 次读取不应该警告"
        elif i == 3:
            # 第 4 次读取应该触发警告
            assert warning is not None, f"第 {i+1} 次读取应该警告"
            warning_triggered = True
            print(f"✓ 第 4 次读取触发警告：")
            print(f"  {warning[:100]}...")
        else:
            # 第 5 次及以后不再重复警告（已经警告过了）
            assert warning is None, f"第 {i+1} 次读取不应该重复警告"

    assert warning_triggered, "应该至少触发一次警告"
    print("✓ 重复读取检测测试通过")


def test_different_locations():
    """测试不同位置不会触发警告"""
    guide = TaskGuide()

    # 读取不同文件
    for i in range(5):
        warning = guide.record_tool_call("Read", {
            "path": f"test{i}.py",
            "start_line": 1,
            "end_line": 10
        })
        assert warning is None, f"读取不同文件不应该警告"

    # 读取同一文件的不同位置
    for i in range(5):
        warning = guide.record_tool_call("Read", {
            "path": "test.py",
            "start_line": i * 10,
            "end_line": (i + 1) * 10
        })
        assert warning is None, f"读取不同位置不应该警告"

    print("✓ 不同位置检测测试通过")


def test_non_read_tools():
    """测试非 Read 工具不触发检测"""
    guide = TaskGuide()

    for i in range(10):
        warning = guide.record_tool_call("run_cmd", {
            "command": "echo test"
        })
        assert warning is None, f"非 Read 工具不应该触发警告"

    print("✓ 非 Read 工具测试通过")


def test_statistics():
    """测试统计功能"""
    guide = TaskGuide()

    # 读取同一位置 5 次
    for i in range(5):
        guide.record_tool_call("Read", {
            "path": "test.py",
            "start_line": 1,
            "end_line": 10
        })

    stats = guide.get_statistics()
    assert stats["total_reads"] == 5, "总读取次数应该是 5"
    assert stats["unique_locations"] == 1, "唯一位置数应该是 1"
    assert len(stats["repeated_reads"]) == 1, "重复读取记录应该有 1 条"

    print("✓ 统计功能测试通过")
    print(f"  统计信息: {stats}")


if __name__ == "__main__":
    print("开始测试任务引导系统集成...\n")

    test_read_detection()
    test_different_locations()
    test_non_read_tools()
    test_statistics()

    print("\n✅ 所有测试通过！")
