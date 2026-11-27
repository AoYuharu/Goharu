"""DiskUsage 只读容量扫描契约测试。

运行：python tests/test_disk_usage.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Tools.builtin.disk_usage import _scan_tree  # noqa: E402
from Tools.registry import registry  # noqa: E402
from Tools.result_formatters import format_tool_result  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def make_tree():
    tmp = tempfile.mkdtemp(prefix="goharu_disk_usage_")
    root = Path(tmp)
    (root / "a.bin").write_bytes(b"x" * 100)
    (root / "sub").mkdir()
    (root / "sub" / "b.bin").write_bytes(b"y" * 300)
    (root / "sub" / "c.bin").write_bytes(b"z" * 200)
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "d.bin").write_bytes(b"w" * 50)
    return root


def test_basic_and_topn():
    print("\n[1] 基本聚合与 Top-N")
    root = make_tree()
    try:
        result = _scan_tree(str(root), 1, 2, threading.Event())
        check("总大小正确", result["total_bytes"] == 650, str(result))
        check("文件数正确", result["file_count"] == 4, str(result))
        check("完整扫描", result["complete"] and not result["cancelled"], str(result))
        check("Top-N 条目数", len(result["entries"]) == 2, str(result["entries"]))
        check("条目按大小降序", [e["bytes"] for e in result["entries"]] == [550, 100],
              str(result["entries"]))
        check("最大文件 Top-N", [f["bytes"] for f in result["largest_files"]] == [300, 200],
              str(result["largest_files"]))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_depth():
    print("\n[2] 深度聚合")
    root = make_tree()
    try:
        result = _scan_tree(str(root), 2, 10, threading.Event())
        names = {Path(e["path"]).name for e in result["entries"]}
        check("二级目录出现", "deep" in names, str(result["entries"]))
        check("总大小不随 depth 改变", result["total_bytes"] == 650, str(result))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_hardlink():
    print("\n[3] 硬链接去重")
    root = make_tree()
    try:
        try:
            os.link(root / "a.bin", root / "hard.bin")
        except OSError as exc:
            print(f"  SKIP  文件系统不支持硬链接: {exc}")
            return
        result = _scan_tree(str(root), 1, 10, threading.Event())
        check("硬链接只计一次", result["total_bytes"] == 650, str(result))
        check("记录去重数量", result["hardlink_deduped"] == 1, str(result))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_symlink_not_followed():
    print("\n[4] 符号链接不跟随")
    root = make_tree()
    outside = Path(tempfile.mkdtemp(prefix="goharu_disk_outside_"))
    try:
        (outside / "huge.bin").write_bytes(b"o" * 4096)
        try:
            os.symlink(outside, root / "link", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            print(f"  SKIP  平台不支持符号链接: {exc}")
            return
        result = _scan_tree(str(root), 1, 10, threading.Event())
        check("链接目标未计入", result["total_bytes"] == 650, str(result))
        check("记录跳过链接", result["skipped_links"] == 1, str(result))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_permission_error():
    print("\n[5] 权限错误显式统计")
    root = make_tree()
    (root / "locked").mkdir()
    real_scandir = os.scandir

    def fake_scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError("denied")
        return real_scandir(path)

    try:
        with mock.patch("Tools.builtin.disk_usage.os.scandir", side_effect=fake_scandir):
            result = _scan_tree(str(root), 1, 10, threading.Event())
        check("权限错误计数", result["permission_errors"] == 1, str(result))
        check("标记不完整", not result["complete"], str(result))
        check("保留错误样例", result["error_samples"], str(result))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_cancelled():
    print("\n[6] 协作式取消")
    root = make_tree()
    try:
        stop = threading.Event()
        stop.set()
        result = _scan_tree(str(root), 1, 10, stop)
        check("预置取消生效", result["cancelled"], str(result))
        check("取消后不完整", not result["complete"], str(result))
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_delegation_guard():
    print("\n[8] 主 Agent 多子树直扫被拦截")
    root = make_tree()
    (root / "sub2").mkdir()
    try:
        raw = asyncio.run(registry.dispatch("DiskUsage", {"path": str(root)}))
        data = json.loads(raw)
        check("主 Agent 直扫多子树返回 delegation_required",
              data.get("delegation_required") is True, raw[:500])
        check("返回子树清单", len(data.get("subtrees", [])) == 2, str(data))
        raw = asyncio.run(registry.dispatch(
            "DiskUsage", {"path": str(root), "_subagent": True}))
        data = json.loads(raw)
        check("Explore 调用正常放行", data.get("total_bytes") == 650, raw[:500])
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_async_dispatch_and_formatter():
    print("\n[7] registry dispatch 与 formatter")
    root = make_tree()
    try:
        raw = asyncio.run(registry.dispatch(
            "DiskUsage", {"path": str(root), "top_n": 5, "depth": 1}))
        data = json.loads(raw)
        check("异步 dispatch 返回 JSON", data["total_bytes"] == 650, raw[:300])
        text = format_tool_result("DiskUsage", raw)
        check("formatter 输出中文结构化文本",
              "逻辑大小" in text and "覆盖状态：完整" in text and "目录聚合 Top-N" in text,
              text[:500])
        missing = asyncio.run(registry.dispatch("DiskUsage", {"path": str(root / "nope")}))
        check("不存在路径返回 error", "error" in json.loads(missing), missing)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 60)
    print("DiskUsage 只读容量扫描测试")
    print("=" * 60)
    test_basic_and_topn()
    test_depth()
    test_hardlink()
    test_symlink_not_followed()
    test_permission_error()
    test_cancelled()
    test_delegation_guard()
    test_async_dispatch_and_formatter()
    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
