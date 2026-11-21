"""无头 SWE agent 入口 —— DGM(Darwin Gödel Machine)契约适配器。

把 Goharu 的 Agent Loop 包成 DGM 外壳要求的批处理形态:
  吃: --problem_statement + --git_dir + --base_commit
  吐: <outdir>/model_patch.diff(对 git_dir 的完整 git diff,含未跟踪文件)
      + markdown 运行日志(--chat_history_file,供 DGM 诊断模型复盘用)

与 DGM 的 coding_agent.py 对齐,但内部是 Goharu 的:
  create_llm(配置注入) + create_tools(最小编码工具集) + create_agent(memory=None)。

用法(在 Goharu 仓库根目录):
  python swe_headless_agent.py \
      --problem_statement "修复 add() 的 off-by-one" \
      --git_dir /testbed --base_commit abc123 \
      --chat_history_file ./chat.md --outdir ./

模型配置默认读 config.yaml 的 model.large-language-model 节,
可用 --model/--base_url/--api_key_env 或同名环境变量覆盖。
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
# configurationLoader / PromptLoader 用相对路径(./prompts/...),必须以仓库根为 cwd
os.chdir(REPO_ROOT)

logger = logging.getLogger("swe_headless_agent")

# 最小编码工具集:run_cmd(core) + Read/Write/Edit/Grep(file) + Glob
TOOL_MODULES = [
    "Tools.builtin.core_tools",
    "Tools.builtin.file_tools",
    "Tools.builtin.glob_tool",
]

INSTRUCTION_TEMPLATE = """I have uploaded a code repository in the directory {git_dir}. Help solve the following problem.

<problem_description>
{problem_statement}
</problem_description>

<test_description>
{test_description}
</test_description>

Your task is to make changes to the files in the {git_dir} directory to address the <problem_description>. I have already taken care of the required dependencies.

**Pre-flight verification (IMPORTANT - do this BEFORE making any changes)**:
- After identifying the target file, find and run existing tests for the feature to understand the current expected behavior.
- Record which tests pass/fail BEFORE the fix and what values they expect. This establishes a baseline.
- **Distinguish between tests that encode correct behavior vs. tests that may encode buggy behavior**: If a test fails after your fix, manually verify the semantic correctness of the new output to determine if the test expectations were wrong.
- Note any test failures that may indicate the existing tests encode buggy behavior rather than correct expected values.
- This prevents incorrectly concluding a fix is correct when it actually breaks existing correct behavior, or from incorrectly 'verifying' a fix against wrong expected values.

Rules:
- Work only inside {git_dir}. Use absolute paths for all file operations.
- Do NOT run git commit / git reset / git checkout -- the evaluation harness diffs the working tree against the base commit itself.
- Prefer running the project's own tests (or write a small reproduction script) to dynamically verify your fix before finishing.
- **After applying a fix, validate results by comparing against known-correct expected values (not just checking for success/None).** Run tests that include assertions on actual output values — e.g. for a parser fix, check `parse_result.hours == -1 and parse_result.minutes == -15 and parse_result.seconds == -30`, not just `assert parse_result is not None`. Syntactically-correct but semantically-incorrect fixes must be caught at this stage.
- When you are confident the problem is solved, stop calling tools and give a brief final summary.

## Test Infrastructure Priority (IMPORTANT)

**When starting work on a new repository, prioritize existing test infrastructure over creating standalone verification scripts.**

1. **Quick Scan for Test Runners**: At the start of exploration, run a quick scan to identify available test infrastructure:
   ```
   run_cmd(['find', '.', '-name', 'runtests.py', '-o', '-name', 'pytest.ini', '-o', '-name', 'setup.cfg', '-o', '-name', 'tox.ini'])
   ```
   Also look for common test directories: `tests/`, `test_*.py`, `*_tests/`

2. **Use Existing Test Files**: When adding test cases for verification, add them to existing test files rather than creating standalone scripts. Common patterns:
   - Django projects: `tests/runtests.py`, `tests/<app>_tests/`
   - pytest projects: `pytest.ini`, `conftest.py`, `tests/`
   - Standard library projects: `test_*.py`, `Lib/test/`

3. **Run Existing Test Suites**: If the project has an established test runner, use it directly for verification instead of building custom test setups:
   - `./runtests.py` for Django-style projects
   - `pytest` for pytest-based projects
   - `python -m unittest` for stdlib unittest

4. **Only Create Standalone Scripts When Necessary**: Create standalone verification scripts only if:
   - No existing test infrastructure exists in the repository
   - The existing tests are not suitable for the specific verification needed

---

## Convergence Hints (Avoid Over-Exploration)

**Know when to stop exploring and start implementing.** Many SWE issues have clearly identified:
- Target file(s): e.g., "sessions/backends/base.py" or "fix the parser in utils.py"
- Solution approach: e.g., "use legacy encode when sha1" or "add boundary checks"
- Verification method: existing test suite or a specific test file

**Convergence signals that indicate sufficient understanding:**
1. ✅ Target file identified and read — you can see the exact code that needs changing
2. ✅ Issue clearly states what the fix should do — not ambiguous
3. ✅ Test file located and understood — you know how to verify the fix
4. ✅ Similar pattern found in codebase — you can follow existing conventions

**When these signals are present, stop exploring and implement.** Strike when ready.

**Signs you are over-exploring (stop immediately):**
- 🔴 Reading files unrelated to the target (reading 10+ files when issue names 1-2)
- 🔴 Searching for patterns you've already confirmed exist
- 🔴 Re-reading files you already understand
- 🔴 No new information gained in the last 2-3 steps

**Actionable rule**: If the issue clearly identifies the file, function, and fix direction, make one targeted read, then implement and test. Do not continue exploring."""


def load_llm_config(args) -> dict:
    """config.yaml 的 model.large-language-model 节为底,CLI/环境变量覆盖。"""
    import yaml

    with open(REPO_ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm_cfg = dict(cfg["model"]["large-language-model"])

    llm_cfg["provider"] = (args.provider or os.getenv("GOHARU_PROVIDER")
                           or llm_cfg.get("provider"))
    llm_cfg["model"] = args.model or os.getenv("GOHARU_MODEL") or llm_cfg.get("model")
    llm_cfg["base_url"] = (args.base_url or os.getenv("GOHARU_BASE_URL")
                           or llm_cfg.get("base_url"))
    llm_cfg["api_key_env"] = (args.api_key_env or os.getenv("GOHARU_API_KEY_ENV")
                              or llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    return llm_cfg


def diff_versus_commit(git_dir: str, commit: str) -> str:
    """工作树 vs commit 的完整 diff,含未跟踪新文件(对齐 DGM git_utils 语义)。"""
    def run(cmd):
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False).stdout.decode("utf-8", errors="replace")

    diff_output = run(["git", "-C", git_dir, "diff", commit])
    untracked = run(["git", "-C", git_dir, "ls-files", "--others",
                     "--exclude-standard"]).splitlines()
    # 缓存/二进制/运行时产物不进 patch:--no-index 对二进制只输出 "Binary files differ",
    # 会让 DGM 外壳 patch -p1 / git apply 阶段出问题;lifecycle.txt 等运行时日志
    # 在自我修改场景(git_dir=agent 自身仓库)会污染基因型
    _EXCLUDED = ("__pycache__", "lifecycle.txt", "swe_headless.log", "runtime_memory/")
    untracked = [f for f in untracked
                 if not any(x in f for x in _EXCLUDED) and not f.endswith(".pyc")]
    devnull = "NUL" if os.name == "nt" else "/dev/null"
    for f in untracked:
        diff_output += run(["git", "-C", git_dir, "diff", "--no-index", devnull, f])
    return diff_output


class MarkdownLogger:
    """把 agent 事件流写成 markdown,供 DGM 诊断模型复盘。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def _write(self, text: str):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def on_event(self, etype: str, payload: dict):
        if etype == "step":
            self._write(f"\n## Step {payload.get('step')}/{payload.get('max_steps')}")
        elif etype == "thinking":
            self._write(f"\n**Thinking**: {payload.get('text', '')}")
        elif etype == "text":
            self._write(f"\n**Assistant**: {payload.get('text', '')}")
        elif etype == "tool_call":
            self._write(f"\n**Tool Call** `{payload.get('tool')}`:\n```json\n"
                        f"{payload.get('arguments')}\n```")
        elif etype == "tool_result":
            self._write(f"\n**Tool Result** `{payload.get('tool')}`:\n```\n"
                        f"{payload.get('preview')}\n```")
        elif etype == "retry":
            self._write(f"\n> LLM retry {payload.get('attempt')}/{payload.get('max')} "
                        f"({payload.get('phase')})")
        elif etype == "answer":
            self._write(f"\n## Final Answer\n{payload.get('answer', '')}")
        elif etype == "error":
            self._write(f"\n## Error\n{payload.get('error', '')}")


async def run_agent(args, llm=None) -> str:
    """核心流程(测试可注入假 llm)。返回最终回复文本。"""
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    from Agent.api import create_agent
    from Agent.llm import create_llm
    from Tools.api import close_tools, create_tools

    git_dir = str(Path(args.git_dir).resolve())
    md = MarkdownLogger(args.chat_history_file)
    md._write(f"# SWE Headless Run\n\n- git_dir: {git_dir}\n"
              f"- base_commit: {args.base_commit}\n- model: {args.model or 'config.yaml'}")

    if llm is None:
        llm_cfg = load_llm_config(args)
        logger.info("LLM: provider=%s model=%s base_url=%s",
                    llm_cfg.get("provider"), llm_cfg.get("model"),
                    llm_cfg.get("base_url"))
        llm = create_llm(llm_cfg)

    tools = await create_tools(modules=TOOL_MODULES)
    agent = await create_agent(
        tools=tools,
        llm=llm,
        memory=None,  # 内存态:SWE 单任务无需持久记忆/检索/persona
        on_event=md.on_event,
        max_steps=args.max_steps,
        max_tool_retries=3,
        on_confirm=lambda cmd, desc: True,  # 无头模式自动放行灰名单;黑名单仍硬拦
    )

    instruction = INSTRUCTION_TEMPLATE.format(
        git_dir=git_dir,
        problem_statement=args.problem_statement,
        test_description=args.test_description or "Run the project's existing tests.",
    )
    md._write(f"\n## Instruction\n{instruction}")

    logger.info("agent 开始运行(max_steps=%d, timeout=%ds)",
                args.max_steps, args.timeout)
    try:
        reply = await asyncio.wait_for(agent.run_turn(instruction),
                                       timeout=args.timeout)
    except asyncio.TimeoutError:
        reply = ""
        logger.error("agent 运行超时(%ds)", args.timeout)
        md._write(f"\n## Timeout\nAgent exceeded {args.timeout}s wall clock.")
    finally:
        await close_tools(tools)

    patch = diff_versus_commit(git_dir, args.base_commit)
    out_path = Path(args.outdir) / "model_patch.diff"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(patch, encoding="utf-8")
    logger.info("model_patch.diff 已写出:%s(%d 字节)", out_path, len(patch))
    md._write(f"\n## Patch\n```diff\n{patch}\n```")
    return reply


def main():
    parser = argparse.ArgumentParser(description="Goharu headless SWE agent (DGM contract)")
    parser.add_argument("--problem_statement", required=True)
    parser.add_argument("--git_dir", required=True)
    parser.add_argument("--base_commit", required=True)
    parser.add_argument("--chat_history_file", required=True)
    parser.add_argument("--outdir", default=".")
    parser.add_argument("--test_description", default=None)
    parser.add_argument("--max_steps", type=int, default=150,
                        help="单轮最大 LLM 步数(DGM 无此概念,这里防失控)")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="墙钟超时秒数(对齐 DGM 自改进 30min)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None,
                        help="anthropic_compatible / openai_compatible / auto")
    parser.add_argument("--base_url", default=None)
    parser.add_argument("--api_key_env", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(
                      str(Path(args.outdir) / "swe_headless.log"),
                      encoding="utf-8")],
    )
    t0 = time.time()
    reply = asyncio.run(run_agent(args))
    logger.info("完成,耗时 %.1fs。最终回复:%s", time.time() - t0, reply[:500])


if __name__ == "__main__":
    main()
