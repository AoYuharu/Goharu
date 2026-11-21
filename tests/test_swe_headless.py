"""swe_headless_agent 离线验证:剧本化假 LLM 走通"接任务 → 修 bug → 吐 patch"全链路。

不依赖任何真实 LLM API。运行(在 Goharu 仓库根目录):
    python tests/test_swe_headless.py

验证点:
1. create_tools/create_agent 接线在无头模式下可用(memory=None + 自动确认);
2. 假 LLM 经 Read → Edit 修好玩具仓库的 bug;
3. model_patch.diff 被正确产出,且包含修复内容(对齐 DGM 交付契约)。
"""

import argparse
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

import swe_headless_agent as sha

BUGGY = '''def add(a, b):
    return a - b  # BUG: should be a + b
'''


class FakeResponse:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = None


class ScriptedFixLLM:
    """剧本:Read 看文件 → Edit 修复 → 总结。"""

    def __init__(self, target_file: str):
        self.step = 0
        self.target = target_file

    def query(self, messages, on_retry=None, tools=None, **kwargs):
        self.step += 1
        print(f"  [llm] 第 {self.step} 次调用:{len(messages)} 条消息")
        if self.step == 1:
            return FakeResponse(
                [{"type": "tool_use", "id": "c1", "name": "Read",
                  "input": {"path": self.target}}],
                stop_reason="tool_use")
        if self.step == 2:
            return FakeResponse(
                [{"type": "tool_use", "id": "c2", "name": "Edit",
                  "input": {"path": self.target,
                            "old_string": "return a - b",
                            "new_string": "return a + b"}}],
                stop_reason="tool_use")
        return FakeResponse(
            [{"type": "text", "text": "已修复 add():a - b → a + b。"}])


def make_toy_repo(root: Path) -> tuple[Path, str]:
    """建一个带 bug 的 git 仓库,返回 (repo_path, base_commit)。"""
    repo = root / "toyrepo"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(BUGGY, encoding="utf-8")

    def git(*cmd):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    git("init")
    git("-c", "user.name=test", "-c", "user.email=test@test", "add", "--all")
    git("-c", "user.name=test", "-c", "user.email=test@test",
        "commit", "-m", "init")
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE, check=True).stdout.decode().strip()
    return repo, commit


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo, base_commit = make_toy_repo(tmp_path)
        print(f"[test] 玩具仓库:{repo} (base={base_commit[:8]})")

        args = argparse.Namespace(
            problem_statement="add() returns a - b instead of a + b. Fix it.",
            git_dir=str(repo),
            base_commit=base_commit,
            chat_history_file=str(tmp_path / "chat.md"),
            outdir=str(tmp_path),
            test_description="Run: python -c \"from calc import add; assert add(1,2)==3\"",
            max_steps=10,
            timeout=120,
            model=None, base_url=None, api_key_env=None, provider=None,
        )

        reply = await sha.run_agent(
            args, llm=ScriptedFixLLM(str(repo / "calc.py")))
        print(f"[test] agent 回复:{reply}")

        # 验证 1:工作树被修复
        fixed = (Path(repo) / "calc.py").read_text(encoding="utf-8")
        assert "return a + b" in fixed, f"文件未被修复:\n{fixed}"
        print("[test] ✓ 工作树文件已修复")

        # 验证 2:patch 契约
        patch = (tmp_path / "model_patch.diff").read_text(encoding="utf-8")
        assert "return a + b" in patch and "return a - b" in patch, \
            f"patch 内容不对:\n{patch}"
        assert "calc.py" in patch
        print("[test] ✓ model_patch.diff 包含修复 diff")

        # 验证 3:markdown 日志(供 DGM 诊断模型复盘)
        chat = (tmp_path / "chat.md").read_text(encoding="utf-8")
        assert "Tool Call" in chat and "Read" in chat and "Edit" in chat
        assert "Final Answer" in chat
        print("[test] ✓ chat.md 记录了工具调用与最终回答")

        # 验证 4:patch 可被 git apply 干净应用(DGM 外壳的打补丁方式)
        repo2, _ = make_toy_repo(tmp_path / "second")
        result = subprocess.run(
            ["git", "-C", repo2, "apply", "--reject", "-"],
            input=patch, text=True, capture_output=True)
        assert result.returncode == 0, f"git apply 失败:{result.stderr}"
        print("[test] ✓ patch 可被 git apply 到干净仓库")

    print("\n[test] 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
