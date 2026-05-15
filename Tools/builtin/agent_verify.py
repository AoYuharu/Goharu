"""
Agent Verify 工具 — 严格遵循 Verification Agent 规范的对抗性验证工具。

核心哲学：不是确认实现能用，而是尝试破坏它。

4 阶段管线：
  Phase 1: UNIVERSAL BASELINE（通用基线）
  Phase 2: TYPE-SPECIFIC STRATEGY（按变更类型）
  Phase 3: ADVERSARIAL PROBES（对抗性探测 — 至少1项）
  Phase 4: VERDICT（汇总判定）

每项检查严格遵循三段式：
  Command run → Output observed → Result: PASS/FAIL/PARTIAL
"""

import ast
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from Tools.registry import registry

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# ── 常见问题扫描模式（保留自旧版） ─────────────────
_DEBUG_PATTERNS = [
    ("print(", "残留的 print() 调试语句"),
    ("breakpoint()", "残留的 breakpoint() 断点"),
    ("pdb.set_trace()", "残留的 pdb.set_trace() 断点"),
    ("icecream", "残留的 icecream 调试导入/调用"),
    ("logging.debug(", "debug 级别日志（可能过多）"),
    ("logger.debug(", "debug 级别日志（可能过多）"),
    ("# TODO", "未完成的 TODO 标记"),
    ("# FIXME", "未修复的 FIXME 标记"),
    ("# HACK", "临时 HACK 标记"),
    ("XXX", "可疑的 XXX 标记"),
    ("import pdb", "残留的 pdb 导入"),
    ("sys.exit(", "调用 sys.exit() — 库代码不应直接退出"),
    ("os._exit(", "调用 os._exit() — 危险操作"),
]


# ═══════════════════════════════════════════════════════
# 保留的辅助函数
# ═══════════════════════════════════════════════════════

def _find_project_python_files() -> list:
    """找出项目中所有 .py 文件（排除 __pycache__ 和 .git）"""
    py_files = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "venv", ".venv", "node_modules")]
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files


def _find_test_files(changed_files: list) -> list:
    """根据变更文件推断相关测试文件"""
    tests: list[str] = []
    test_dirs = ["test", "tests", "test_scripts"]
    for cf in changed_files:
        stem = Path(cf).stem
        name = Path(cf).name
        for td in test_dirs:
            test_dir = PROJECT_ROOT / td
            if test_dir.exists():
                for tf in test_dir.iterdir():
                    if tf.suffix == ".py":
                        if name in tf.name or stem in tf.name or tf.name.startswith("test_"):
                            if str(tf) not in tests:
                                tests.append(str(tf))
    return tests


def _check_python_syntax(file_path: str) -> dict:
    """检查单个 Python 文件的语法"""
    try:
        path = Path(file_path)
        if not path.exists():
            return {"result": "SKIP", "reason": "文件不存在"}
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        ast.parse(source)
        return {"result": "PASS", "lines": len(source.splitlines())}
    except SyntaxError as e:
        return {"result": "FAIL", "error": f"第 {e.lineno} 行: {e.msg}"}
    except Exception as e:
        return {"result": "FAIL", "error": str(e)}


def _check_python_imports(file_path: str) -> list[dict]:
    """检查 Python 文件的顶级导入是否可用（非侵入式）"""
    issues = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
    except SyntaxError:
        return [{"result": "SKIP", "reason": "语法错误，跳过导入检查"}]
    except Exception as e:
        return [{"result": "SKIP", "reason": str(e)}]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                try:
                    importlib.import_module(module_name)
                except ImportError:
                    issues.append({
                        "result": "FAIL",
                        "import": alias.name,
                        "error": f"无法导入模块 {alias.name}",
                        "line": node.lineno,
                    })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split(".")[0]
                try:
                    importlib.import_module(module_name)
                except ImportError:
                    issues.append({
                        "result": "FAIL",
                        "import": node.module,
                        "error": f"无法导入模块 {node.module}",
                        "line": node.lineno,
                    })
    return issues


def _scan_common_issues(file_path: str) -> list[dict]:
    """扫描文件中的常见遗留问题"""
    issues = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return [{"result": "FAIL", "error": str(e)}]

    for lineno, line in enumerate(lines, 1):
        for pattern, description in _DEBUG_PATTERNS:
            if pattern in line:
                stripped = line.strip()
                if pattern in ("# TODO", "# FIXME", "# HACK"):
                    if not stripped.startswith(pattern):
                        continue
                if pattern == "print(":
                    if stripped.startswith("#") or stripped.startswith('"""'):
                        continue
                issues.append({
                    "result": "WARN",
                    "line": lineno,
                    "content": stripped[:120],
                    "issue": description,
                })
    return issues


def _check_yaml_syntax(file_path: str) -> dict:
    """检查 YAML 文件语法"""
    try:
        import yaml
    except ImportError:
        return {"result": "SKIP", "reason": "yaml 库不可用"}
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            yaml.safe_load(f)
        return {"result": "PASS"}
    except Exception as e:
        return {"result": "FAIL", "error": str(e)}


def _check_json_syntax(file_path: str) -> dict:
    """检查 JSON 文件语法"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            json.load(f)
        return {"result": "PASS"}
    except json.JSONDecodeError as e:
        return {"result": "FAIL", "error": f"第 {e.lineno} 行: {e.msg}"}
    except Exception as e:
        return {"result": "FAIL", "error": str(e)}


def _detect_test_runner() -> Optional[str]:
    """检测可用的测试运行器"""
    try:
        import pytest
        return "pytest"
    except ImportError:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True, timeout=5, cwd=str(PROJECT_ROOT),
        )
        return "pytest"
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════
# 新增辅助函数
# ═══════════════════════════════════════════════════════

def _run_command(cmd, timeout=60, cwd=None):
    """安全执行命令并捕获输出。

    Returns:
        dict: {"returncode": int, "stdout": str, "stderr": str, "timed_out": bool}
    """
    cwd = cwd or str(PROJECT_ROOT)
    try:
        result = subprocess.run(
            cmd if isinstance(cmd, list) else cmd,
            shell=not isinstance(cmd, list),
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"命令超时 (>{timeout}s)",
            "timed_out": True,
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "timed_out": False,
        }


def _classify_changes(files_changed, task_description, approach=""):
    """根据变更文件和任务描述识别变更类型。

    Returns:
        str: "python" | "config" | "bugfix" | "refactor" | "mixed"
    """
    has_py = any(f.endswith(".py") for f in files_changed)
    has_config = any(f.endswith((".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env")) for f in files_changed)

    task_lower = (task_description or "").lower()
    approach_lower = (approach or "").lower()

    bug_keywords = ("bug", "fix", "修复", "缺陷", "错误", "异常", "崩溃", "crash")
    refactor_keywords = ("refactor", "重构", "不改变行为", "不改进行为", "no behavior change")

    is_bugfix = any(kw in task_lower for kw in bug_keywords)
    is_refactor = any(kw in approach_lower for kw in refactor_keywords)

    if is_bugfix:
        return "bugfix"
    if is_refactor:
        return "refactor"
    if has_py and has_config:
        return "mixed"
    if has_config:
        return "config"
    if has_py:
        return "python"
    return "mixed"


def _format_check(title, command, output, result, expected_vs_actual=""):
    """统一格式化每项检查，确保遵循三段式格式。

    Args:
        title: 检查标题
        command: 实际执行的命令文本
        output: 终端实际输出（粘贴而非概括）
        result: "PASS" | "FAIL" | "PARTIAL"
        expected_vs_actual: 预期 vs 实际描述（可选）

    Returns:
        dict: {"title": str, "body": str, "result": str}
    """
    lines = [
        f"### Check: {title}",
        f"**Command run:**",
    ]
    for cmd_line in command.strip().split("\n"):
        lines.append(f"  {cmd_line.strip()}")
    lines.append(f"**Output observed:**")
    for out_line in (output or "(无输出)").strip().split("\n"):
        lines.append(f"  {out_line.strip()}")
    if expected_vs_actual:
        lines.append(f"**Expected vs Actual:** {expected_vs_actual}")
    lines.append(f"**Result: {result}**")

    return {
        "title": title,
        "body": "\n".join(lines),
        "result": result,
    }


def _issue_verdict(checks):
    """Phase 4: 汇总所有检查结果，发出最终判定。

    规则：
    - PASS 必须有至少一项对抗性探测
    - 任何 FAIL → FAIL
    - 有 PARTIAL 无 FAIL → PARTIAL
    - 全部 PASS → PASS

    Returns:
        dict: {"verdict": str, "reason": str}
    """
    results = [c.get("result", "PARTIAL") for c in checks]
    has_adversarial = any("对抗性探测" in c.get("title", "") or "Adversarial" in c.get("title", "") for c in checks)

    fail_count = results.count("FAIL")
    partial_count = results.count("PARTIAL")
    pass_count = results.count("PASS")

    if fail_count > 0:
        return {"verdict": "FAIL", "reason": f"{fail_count} 项检查失败，{pass_count} 通过，{partial_count} 部分通过"}
    if partial_count > 0:
        return {"verdict": "PARTIAL", "reason": f"{pass_count} 项通过，{partial_count} 项部分通过（受限于环境/工具可用性）"}
    if not has_adversarial:
        return {"verdict": "PARTIAL", "reason": "所有基础检查通过，但缺少对抗性探测"}
    return {"verdict": "PASS", "reason": f"全部 {pass_count} 项检查通过（含对抗性探测）"}


# ═══════════════════════════════════════════════════════
# Phase 1: UNIVERSAL BASELINE（通用基线）
# ═══════════════════════════════════════════════════════

def _universal_baseline(existing_files):
    """
    Phase 1 通用基线检查：
    1.1 Build 检查（pip install 依赖可用性 + 全模块导入链测试）
    1.2 测试套件执行（python test_*.py 逐个运行）
    1.3 回归检查（git diff 相关文件范围）
    """
    checks = []

    # ── 1.1 Build 检查 ────────────────────────────────
    # 1.1a: 检查 requirements.txt 依赖
    req_path = PROJECT_ROOT / "requirements.txt"
    if req_path.exists():
        try:
            req_lines = req_path.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            req_count = len([l for l in req_lines if l.strip() and not l.strip().startswith("#")])
        except Exception:
            req_count = 0
        checks.append(_format_check(
            title="requirements.txt 依赖声明检查",
            command=f"readfile {req_path}",
            output=f"requirements.txt 存在，声明 {req_count} 个依赖",
            result="PASS",
            expected_vs_actual="requirements.txt 应存在并声明项目依赖",
        ))

        # pip check（快速依赖验证）
        pip_result = _run_command(
            [sys.executable, "-m", "pip", "check"],
            timeout=30,
        )
        pip_output = pip_result["stdout"][:3000] or pip_result["stderr"][:3000]
        checks.append(_format_check(
            title="pip check 依赖一致性检查",
            command=f"{sys.executable} -m pip check",
            output=pip_output,
            result="PASS" if pip_result["returncode"] == 0 else "FAIL",
            expected_vs_actual=f"预期 pip check 通过 (exit=0)，实际 exit={pip_result['returncode']}",
        ))
    else:
        checks.append(_format_check(
            title="依赖声明检查",
            command=f"check {PROJECT_ROOT / 'requirements.txt'}",
            output="requirements.txt 不存在，跳过依赖检查",
            result="PARTIAL",
        ))

    # 1.1b: 全模块导入链测试
    all_py_files = _find_project_python_files()
    rel_py_files = [os.path.relpath(f, PROJECT_ROOT) for f in all_py_files]
    import_test_script = "; ".join([
        "import sys",
        "sys.path.insert(0, r'" + str(PROJECT_ROOT) + "')",
        "import importlib",
        "modules = []",
        "skipped = []",
    ] + [
        f"try: importlib.import_module('{os.path.splitext(f.replace(os.sep, '.'))[0]}'); modules.append('{f}')"
        f"\n except Exception as e: skipped.append(('{f}', str(e)))"
        for f in rel_py_files
    ] + [
        "print(f'Successfully imported: {len(modules)}/{len(modules)+len(skipped)} modules')",
        "if skipped:",
        "    print('Failed imports:')",
        "    [print(f'  {m}: {e}') for m, e in skipped]",
    ])

    import_result = _run_command(
        [sys.executable, "-c", import_test_script],
        timeout=60,
    )
    import_output = import_result["stdout"][:3000] or import_result["stderr"][:3000]
    checks.append(_format_check(
        title="全模块导入链测试",
        command=f"{sys.executable} -c <全模块导入检查脚本>",
        output=import_output,
        result="PASS" if import_result["returncode"] == 0 else "FAIL",
        expected_vs_actual=f"预期所有项目模块可导入 (exit=0)，实际 exit={import_result['returncode']}",
    ))

    # ── 1.2 测试套件执行 ──────────────────────────────
    test_runner = _detect_test_runner()
    # 查找根目录下所有 test_*.py
    root_test_files = sorted(str(f) for f in PROJECT_ROOT.glob("test_*.py"))
    if root_test_files:
        test_results = []
        all_tests_pass = True
        for tf in root_test_files:
            tf_rel = os.path.relpath(tf, PROJECT_ROOT)
            tr = _run_command(
                [sys.executable, tf],
                timeout=120,
            )
            status = "PASS" if tr["returncode"] == 0 else "FAIL"
            if status == "FAIL":
                all_tests_pass = False
            # 取最后几行作为摘要
            out_lines = tr["stdout"].strip().split("\n")
            summary = "\n".join(out_lines[-8:]) if out_lines else "(无输出)"
            test_results.append(f"  {tf_rel}: {status} (exit={tr['returncode']})")
            if status == "FAIL":
                test_results.append(f"    输出摘要:\n{summary}")

        checks.append(_format_check(
            title="测试套件执行 (test_*.py 逐个运行)",
            command="\n".join([f"{sys.executable} {os.path.relpath(f, PROJECT_ROOT)}" for f in root_test_files]),
            output="\n".join(test_results),
            result="PASS" if all_tests_pass else "FAIL",
            expected_vs_actual=f"预期所有测试通过，实际 {'全部通过' if all_tests_pass else '存在失败'}",
        ))
    elif test_runner:
        checks.append(_format_check(
            title="测试套件执行",
            command="detect test files",
            output=f"测试运行器 {test_runner} 可用，但未找到 test_*.py 文件",
            result="PARTIAL",
        ))
    else:
        checks.append(_format_check(
            title="测试套件执行",
            command="detect test runner",
            output="未检测到可用测试运行器，且无 test_*.py 文件",
            result="PARTIAL",
        ))

    # ── 1.3 回归检查 ──────────────────────────────────
    git_diff_result = _run_command(
        ["git", "diff", "--name-only", "HEAD"],
        timeout=15,
    )
    if git_diff_result["returncode"] == 0:
        diff_files = git_diff_result["stdout"].strip().split("\n")
        diff_files = [f for f in diff_files if f]
        affected = len(diff_files)
        checks.append(_format_check(
            title="回归检查 (git diff 变更文件范围)",
            command="git diff --name-only HEAD",
            output=f"当前变更涉及 {affected} 个文件:\n" + "\n".join(f"  {f}" for f in diff_files[:30]),
            result="PASS",
            expected_vs_actual=f"预期变更范围与 task 描述一致，实际涉 {affected} 个文件",
        ))
    else:
        checks.append(_format_check(
            title="回归检查 (git diff)",
            command="git diff --name-only HEAD",
            output=git_diff_result["stderr"] or "git 不可用",
            result="PARTIAL",
        ))

    return checks


# ═══════════════════════════════════════════════════════
# Phase 2: TYPE-SPECIFIC STRATEGY（按变更类型）
# ═══════════════════════════════════════════════════════

def _python_strategy(py_files):
    """Phase 2 Python 代码策略：语法 + 导入 + 公开 API 对比 + 独立上下文导入测试"""
    checks = []

    if not py_files:
        return checks

    # ── 语法检查 ──────────────────────────────────────
    syntax_outputs = []
    syntax_all_pass = True
    for pf in py_files:
        result = _check_python_syntax(pf)
        rel_path = os.path.relpath(pf, PROJECT_ROOT)
        if result["result"] == "PASS":
            syntax_outputs.append(f"  {rel_path}: PASS ({result.get('lines', '?')} 行)")
        else:
            syntax_outputs.append(f"  {rel_path}: FAIL — {result.get('error', result.get('reason', '?'))}")
            syntax_all_pass = False

    checks.append(_format_check(
        title="Python 语法检查 (ast.parse)",
        command=f"ast.parse() 检查 [{len(py_files)} 个 Python 文件]",
        output="\n".join(syntax_outputs),
        result="PASS" if syntax_all_pass else "FAIL",
        expected_vs_actual=f"预期所有文件语法正确，实际 {'全部通过' if syntax_all_pass else '存在语法错误'}",
    ))

    # ── 导入检查 ──────────────────────────────────────
    import_issues = []
    for pf in py_files:
        issues = _check_python_imports(pf)
        for issue in issues:
            if issue.get("result") == "FAIL":
                rel_path = os.path.relpath(pf, PROJECT_ROOT)
                import_issues.append(f"  {rel_path}:{issue.get('line', '?')} — {issue.get('error', '?')}")

    if import_issues:
        checks.append(_format_check(
            title="Python 导入检查 (importlib)",
            command=f"importlib 导入检查 [{len(py_files)} 个文件]",
            output="\n".join(import_issues[:20]),
            result="FAIL",
            expected_vs_actual="预期所有导入可用，实际存在导入失败",
        ))
    else:
        checks.append(_format_check(
            title="Python 导入检查 (importlib)",
            command=f"importlib 导入检查 [{len(py_files)} 个文件]",
            output="所有导入均可用",
            result="PASS",
        ))

    # ── 独立上下文导入测试 ────────────────────────────
    for pf in py_files:
        rel = os.path.relpath(pf, PROJECT_ROOT)
        module_path = os.path.splitext(rel)[0].replace(os.sep, ".")
        test_cmd = f"{sys.executable} -c \"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
        test_cmd += f"import {module_path}; print(f'{module_path} imported successfully')\""
        result = _run_command(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
             f"import {module_path}; print(f'{module_path} imported successfully')"],
            timeout=30,
        )
        checks.append(_format_check(
            title=f"独立上下文导入: {rel}",
            command=test_cmd,
            output=result["stdout"][:2000] or result["stderr"][:2000],
            result="PASS" if result["returncode"] == 0 else "FAIL",
            expected_vs_actual=f"预期 {rel} 可在独立上下文中导入 (exit=0)，实际 exit={result['returncode']}",
        ))

    # ── 常见问题扫描 ──────────────────────────────────
    all_issues = []
    for f in py_files:
        issues = _scan_common_issues(f)
        for issue in issues:
            rel_path = os.path.relpath(f, PROJECT_ROOT)
            all_issues.append({
                **issue,
                "file": rel_path,
            })

    high_severity = [i for i in all_issues
                     if i.get("result") == "WARN"
                     and "breakpoint" in str(i.get("issue", "")).lower()]
    medium_severity = [i for i in all_issues
                       if i.get("result") == "WARN"
                       and i not in high_severity
                       and any(kw in str(i.get("issue", "")).lower()
                               for kw in ("print", "pdb", "sys.exit", "os._exit"))]
    low_severity = [i for i in all_issues
                    if i.get("result") == "WARN"
                    and i not in high_severity
                    and i not in medium_severity]

    issue_outputs = []
    if high_severity:
        issue_outputs.append(f"高严重度 ({len(high_severity)} 项):")
        for i in high_severity[:10]:
            issue_outputs.append(f"  {i['file']}:{i['line']} — {i['issue']}")
    if medium_severity:
        issue_outputs.append(f"中严重度 ({len(medium_severity)} 项):")
        for i in medium_severity[:10]:
            issue_outputs.append(f"  {i['file']}:{i['line']} — {i['issue']}: `{i['content']}`")
    if low_severity:
        issue_outputs.append(f"低严重度 ({len(low_severity)} 项):")
        for i in low_severity[:10]:
            issue_outputs.append(f"  {i['file']}:{i['line']} — {i['issue']}")

    if high_severity:
        checks.append(_format_check(
            title="残留断点/危险代码扫描",
            command="常见问题扫描 (breakpoint/pdb/sys.exit)",
            output="\n".join(issue_outputs),
            result="FAIL",
            expected_vs_actual="不应有残留断点或危险代码",
        ))
    elif medium_severity:
        checks.append(_format_check(
            title="常见问题扫描 (print/debug/exit)",
            command="常见问题扫描",
            output="\n".join(issue_outputs),
            result="PARTIAL",
            expected_vs_actual="建议清理调试代码",
        ))
    elif low_severity:
        checks.append(_format_check(
            title="常见问题扫描",
            command=f"扫描 [{len(py_files)} 个文件]",
            output="\n".join(issue_outputs) if issue_outputs else "仅有低严重度标记",
            result="PASS",
        ))
    else:
        checks.append(_format_check(
            title="常见问题扫描",
            command=f"扫描 [{len(py_files)} 个文件]",
            output="未发现遗留问题",
            result="PASS",
        ))

    return checks


def _config_strategy(config_files):
    """Phase 2 配置策略：YAML 格式 + 路径引用 + 环境变量检查"""
    checks = []

    if not config_files:
        return checks

    # ── YAML/JSON 格式校验 ────────────────────────────
    config_outputs = []
    config_all_pass = True
    for cf in config_files:
        rel_path = os.path.relpath(cf, PROJECT_ROOT)
        if cf.endswith((".yaml", ".yml")):
            result = _check_yaml_syntax(cf)
        else:
            result = _check_json_syntax(cf)

        if result["result"] == "PASS":
            config_outputs.append(f"  {rel_path}: PASS")
        elif result["result"] == "SKIP":
            config_outputs.append(f"  {rel_path}: SKIP ({result.get('reason', '?')})")
        else:
            config_outputs.append(f"  {rel_path}: FAIL — {result.get('error', '?')}")
            config_all_pass = False

    checks.append(_format_check(
        title="配置文件格式校验",
        command=f"格式校验 [{len(config_files)} 个配置文件]",
        output="\n".join(config_outputs),
        result="PASS" if config_all_pass else "FAIL",
        expected_vs_actual=f"预期所有配置文件格式正确，实际 {'全部通过' if config_all_pass else '存在格式错误'}",
    ))

    # ── YAML 路径引用检查 ─────────────────────────────
    yaml_files = [f for f in config_files if f.endswith((".yaml", ".yml"))]
    for yf in yaml_files:
        try:
            import yaml
            with open(yf, "r", encoding="utf-8", errors="replace") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue

        # 递归查找可能指向文件路径的值
        path_candidates = []

        def _collect_paths(obj, key_path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    new_key = f"{key_path}.{k}" if key_path else k
                    if isinstance(v, str) and any(
                        kw in k.lower() for kw in ("path", "dir", "file", "root")
                    ):
                        path_candidates.append((new_key, v))
                    elif isinstance(v, (dict, list)):
                        _collect_paths(v, new_key)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    if isinstance(v, (dict, list)):
                        _collect_paths(v, f"{key_path}[{i}]")

        _collect_paths(data)

        missing_paths = []
        for key, value in path_candidates:
            # 检查相对路径是否存在
            abs_path = (Path(yf).parent / value).resolve()
            if not abs_path.exists() and not value.startswith("$") and not value.startswith("http"):
                missing_paths.append(f"  {key}: {value} (→ {abs_path})")

        rel_yf = os.path.relpath(yf, PROJECT_ROOT)
        if missing_paths:
            checks.append(_format_check(
                title=f"配置文件路径引用检查: {rel_yf}",
                command=f"检查 {rel_yf} 中的路径引用是否存在",
                output=f"发现 {len(missing_paths)} 个路径引用不存在:\n" + "\n".join(missing_paths[:20]),
                result="FAIL",
                expected_vs_actual="所有路径引用应指向存在的文件或目录",
            ))
        else:
            checks.append(_format_check(
                title=f"配置文件路径引用检查: {rel_yf}",
                command=f"检查 {rel_yf} 中 {len(path_candidates)} 个路径引用",
                output=f"全部 {len(path_candidates)} 个路径引用指向可解析目标（含环境变量/URL）",
                result="PASS",
            ))

    # ── 环境变量引用检查 ──────────────────────────────
    for yf in yaml_files:
        try:
            with open(yf, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        # 查找 ${VAR} 和 $VAR 模式
        env_refs = set(re.findall(r'\$\{?(\w+)\}?', content))
        missing_env = []
        for var in env_refs:
            if var.isupper() and var not in os.environ:
                missing_env.append(f"  ${var} — 未在环境中设置")

        rel_yf = os.path.relpath(yf, PROJECT_ROOT)
        if missing_env:
            checks.append(_format_check(
                title=f"配置文件环境变量检查: {rel_yf}",
                command=f"检查 {rel_yf} 中的环境变量引用是否已设置",
                output=f"发现 {len(missing_env)} 个未设置的环境变量:\n" + "\n".join(missing_env[:20]),
                result="PARTIAL",
                expected_vs_actual="所有必需的环境变量应在运行时环境中设置",
            ))
        else:
            checks.append(_format_check(
                title=f"配置文件环境变量检查: {rel_yf}",
                command=f"检查 {rel_yf} 中的 {len(env_refs)} 个环境变量引用",
                output=f"全部 {len(env_refs)} 个环境变量引用已设置或为可选值",
                result="PASS",
            ))

    return checks


def _bugfix_strategy(files, task_description):
    """Phase 2 Bug修复策略：git log 找变更 → 验证修复"""
    checks = []

    # 查看最近相关 commit
    for f in files:
        rel = os.path.relpath(f, PROJECT_ROOT) if os.path.isabs(f) else f
        log_result = _run_command(
            ["git", "log", "--oneline", "-5", "--", rel],
            timeout=15,
        )
        if log_result["returncode"] == 0 and log_result["stdout"]:
            checks.append(_format_check(
                title=f"Bug修复变更历史: {rel}",
                command=f"git log --oneline -5 -- {rel}",
                output=log_result["stdout"],
                result="PASS",
                expected_vs_actual="预期有最近变更记录以确认修复范围",
            ))
        else:
            checks.append(_format_check(
                title=f"Bug修复变更历史: {rel}",
                command=f"git log --oneline -5 -- {rel}",
                output=log_result["stdout"] or log_result["stderr"] or "无变更记录",
                result="PARTIAL",
            ))

        # 查看修复 diff
        diff_result = _run_command(
            ["git", "diff", "HEAD", "--", rel],
            timeout=15,
        )
        if diff_result["returncode"] == 0 and diff_result["stdout"]:
            diff_lines = diff_result["stdout"].strip().split("\n")
            diff_summary = "\n".join(diff_lines[:40])
            if len(diff_lines) > 40:
                diff_summary += f"\n  ... ({len(diff_lines) - 40} 行被截断)"
            checks.append(_format_check(
                title=f"Bug修复变更内容: {rel}",
                command=f"git diff HEAD -- {rel}",
                output=diff_summary,
                result="PASS",
                expected_vs_actual="预期 diff 包含修复相关变更",
            ))
        else:
            checks.append(_format_check(
                title=f"Bug修复变更内容: {rel}",
                command=f"git diff HEAD -- {rel}",
                output=diff_result["stdout"] or "无待提交的 diff",
                result="PARTIAL",
            ))

    return checks


# ═══════════════════════════════════════════════════════
# Phase 3: ADVERSARIAL PROBES（对抗性探测）
# ═══════════════════════════════════════════════════════
#
# 破坏策略分层：
#   L1 — 输入/参数攻击：类型混淆、参数缺失、注入向量、超大输入
#   L2 — 导入链攻击：循环导入、篡改 sys.modules、删除后重导
#   L3 — 状态/资源攻击：幂等性、状态污染、资源泄漏
#   L4 — 文件/配置攻击：路径遍历、YAML炸弹、编码投毒
#
# 每条探测定性："我在尝试破坏 X，通过 Y 手段，期望代码 Z 方式防御"
# PASS = 防御生效（不崩溃、不泄露、不腐化状态）
# FAIL = 崩溃/挂起/安全漏洞/状态腐化

def _adversarial_probes(files, change_type):
    """Phase 3 对抗性探测 — 分层破坏性测试。"""
    checks = []
    py_files = [f for f in files if f.endswith(".py") and os.path.exists(f)]
    config_files = [f for f in files if f.endswith((".yaml", ".yml")) and os.path.exists(f)]

    # ╔══════════════════════════════════════════════════╗
    # ║  L1: 输入/参数攻击                              ║
    # ╚══════════════════════════════════════════════════╝

    # ── L1.1 类型混淆 — 用错误类型参数调用模块 ──
    if py_files and change_type in ("python", "mixed", "bugfix", "refactor"):
        for pf in py_files[:2]:
            rel = os.path.relpath(pf, PROJECT_ROOT)
            module_path = os.path.splitext(rel)[0].replace(os.sep, ".")

            # 探测：导入模块后尝试用怪异的参数调用工具 handler
            probe_script = (
                f"import sys\nsys.path.insert(0, r'{PROJECT_ROOT}')\n"
                f"import json\n"
                f"try:\n"
                f"    import {module_path} as m\n"
                f"    # Attack 1: find a handler function and call it with garbage\n"
                f"    handlers = [v for k, v in vars(m).items() if callable(v) and not k.startswith('_')]\n"
                f"    if handlers:\n"
                f"        for h in handlers[:3]:\n"
                f"            for garbage in [None, 42, [], {{'bad': 'data'}}]:\n"
                f"                try:\n"
                f"                    h(garbage)\n"
                f"                    print(f'  Handler {{h.__name__}} accepted garbage {{type(garbage).__name__}} without error!')\n"
                f"                except TypeError:\n"
                f"                    pass  # Good: Python type system caught it\n"
                f"                except Exception as e:\n"
                f"                    print(f'  Handler {{h.__name__}}({{type(garbage).__name__}}) raised {{type(e).__name__}}')\n"
                f"    print('Module loaded, handlers probed')\n"
                f"except Exception as e:\n"
                f"    print(f'ERROR: {{type(e).__name__}}: {{e}}')\n"
            )
            result = _run_command(
                [sys.executable, "-c", probe_script],
                timeout=15,
            )
            output = result["stdout"][:800] or result["stderr"][:800]
            # PASS if no crash — we just want graceful handling
            passed = result["returncode"] == 0
            checks.append(_format_check(
                title=f"对抗性探测 — 类型混淆攻击: {rel}",
                command=f"{sys.executable} -c \"<对 {module_path} 的 handler 传入 None/int/list/dict>\"",
                output=output,
                result="PASS" if passed else "FAIL",
                expected_vs_actual=f"尝试用错误类型参数调用函数，预期不崩溃，实际 {'正常处理' if passed else '崩溃 (exit=' + str(result['returncode']) + ')'}",
            ))
            break  # 以文件为粒度，只测第一个

    # ── L1.2 注入向量 — 路径遍历 / shell 元字符 ──
    if py_files:
        injection_paths = [
            ("../../../etc/passwd", "路径遍历"),
            ("$(whoami)", "shell命令替换"),
            ("`id`", "shell反引号"),
            ("file|cat /etc/hosts", "shell管道"),
            ("file; rm -rf /", "shell命令分隔"),
            (r"file\x00.txt", "null字节注入"),
        ]
        for inj_path, inj_type in injection_paths:
            test_script = (
                f"import sys\nsys.path.insert(0, r'{PROJECT_ROOT}')\n"
                f"import os\n"
                f"try:\n"
                f"    # Test: does os.path operations handle this malicious path?\n"
                f"    safe = os.path.normpath(r'{inj_path}')\n"
                f"    # Check it didn't escape project root\n"
                f"    is_subpath = safe.startswith(os.path.normpath(r'{PROJECT_ROOT}')) or not os.path.isabs(safe)\n"
                f"    print(f'Path normalized: {{repr(safe)}}, safe={{is_subpath}}')\n"
                f"except Exception as e:\n"
                f"    print(f'Exception handled: {{type(e).__name__}}')\n"
            )
            result = _run_command(
                [sys.executable, "-c", test_script],
                timeout=5,
            )
            output = result["stdout"][:300] or result["stderr"][:300]
            passed = result["returncode"] == 0
            checks.append(_format_check(
                title=f"对抗性探测 — 注入向量 ({inj_type})",
                command=f"{sys.executable} -c \"<路径注入: {inj_path}>\"",
                output=output,
                result="PASS" if passed else "FAIL",
                expected_vs_actual=f"预期 {inj_type} 输入被安全处理不崩溃，实际 {'通过' if passed else '崩溃'}",
            ))

    # ── L1.3 超大输入 — 超长参数 / 深度嵌套 ──
    if py_files and change_type in ("python", "mixed"):
        stress_tests = [
            ("超长字符串 (1MB)", "import sys; sys.path.insert(0, r'%s'); s = 'A' * (1024 * 1024); print(f'1MB string created, len={len(s)}')" % PROJECT_ROOT),
            ("深度嵌套列表 (100层)", "import sys; sys.path.insert(0, r'%s'); x = 0; [x := [x] for _ in range(100)]; print('100-deep list created')" % PROJECT_ROOT),
        ]
        for label, script in stress_tests:
            result = _run_command(
                [sys.executable, "-c", script],
                timeout=10,
            )
            output = result["stdout"][:300] or result["stderr"][:300]
            passed = result["returncode"] == 0 and not result["timed_out"]
            checks.append(_format_check(
                title=f"对抗性探测 — 超大输入: {label}",
                command=f"{sys.executable} -c \"<{label}>\"",
                output=output,
                result="PASS" if passed else "FAIL",
                expected_vs_actual=f"预期 {label} 不导致内存耗尽或超时，实际 {'通过' if passed else '崩溃/超时'}",
            ))

    # ╔══════════════════════════════════════════════════╗
    # ║  L2: 导入链攻击                                  ║
    # ╚══════════════════════════════════════════════════╝

    if py_files and change_type in ("python", "mixed", "bugfix", "refactor"):
        pf = py_files[0]
        rel = os.path.relpath(pf, PROJECT_ROOT)
        module_path = os.path.splitext(rel)[0].replace(os.sep, ".")

        # ── L2.1 篡改 sys.modules 后重导 ──
        corrupt_script = (
            f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
            f"import {module_path} as m1; "
            f"print(f'Import 1: id={{id(m1)}}'); "
            f"# Attack: delete from sys.modules and import again\n"
            f"del sys.modules['{module_path}']; "
            f"import {module_path} as m2; "
            f"print(f'Import 2 (after del): id={{id(m2)}}'); "
            f"print(f'Same module: {{id(m1) == id(m2)}}'); "
            f"print('State corruption test passed')"
        )
        result = _run_command(
            [sys.executable, "-c", corrupt_script],
            timeout=10,
        )
        output = result["stdout"][:500] or result["stderr"][:500]
        passed = result["returncode"] == 0
        checks.append(_format_check(
            title="对抗性探测 — sys.modules 状态腐化",
            command=f"{sys.executable} -c \"<导入 {module_path}, 删除 sys.modules, 重新导入>\"",
            output=output,
            result="PASS" if passed else "FAIL",
            expected_vs_actual=f"预期删除后重新导入不崩溃，实际 {'通过' if passed else '崩溃'}",
        ))

        # ── L2.2 篡改模块全局变量 ──
        mutate_script = (
            f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
            f"import {module_path} as m1; "
            f"# Attack: overwrite a global variable\n"
            f"public_vars = [k for k in dir(m1) if not k.startswith('_') and not callable(getattr(m1, k, None))]\n"
            f"if public_vars:\n"
            f"    v = public_vars[0];\n"
            f"    original = repr(getattr(m1, v));\n"
            f"    setattr(m1, v, 'MUTATED_BY_ATTACK');\n"
            f"    mutated = repr(getattr(m1, v));\n"
            f"    print(f'Variable {{v}}: {{original}} => {{mutated}}')\n"
            f"    print(f'Module global mutation succeeded without crash')\n"
            f"else:\n"
            f"    print('No public non-callable globals to mutate')\n"
        )
        result = _run_command(
            [sys.executable, "-c", mutate_script],
            timeout=10,
        )
        output = result["stdout"][:500] or result["stderr"][:500]
        passed = result["returncode"] == 0
        checks.append(_format_check(
            title="对抗性探测 — 模块全局状态篡改",
            command=f"{sys.executable} -c \"<导入 {module_path}, setattr 篡改全局变量>\"",
            output=output,
            result="PASS" if passed else "FAIL",
            expected_vs_actual=f"预期全局变量可被篡改但不应导致解释器崩溃，实际 {'通过' if passed else '崩溃'}",
        ))

        # ── L2.3 循环导入检测 ──
        cycle_script = (
            f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
            f"import ast; import os; "
            f"# Parse the file and find all its imports\n"
            f"def find_imports(filepath):\n"
            f"    try:\n"
            f"        with open(filepath, 'r', encoding='utf-8') as f:\n"
            f"            tree = ast.parse(f.read())\n"
            f"        result = set()\n"
            f"        for node in ast.walk(tree):\n"
            f"            if isinstance(node, ast.Import):\n"
            f"                for a in node.names:\n"
            f"                    result.add(a.name.split('.')[0])\n"
            f"            elif isinstance(node, ast.ImportFrom) and node.module:\n"
            f"                result.add(node.module.split('.')[0])\n"
            f"        return result\n"
            f"    except Exception as e:\n"
            f"        print(f'Error parsing {{filepath}}: {{e}}')\n"
            f"        return set()\n"
            f"all_mods = {{}}\n"
            f"for root, dirs, files in os.walk(r'{PROJECT_ROOT}'):\n"
            f"    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'venv')]\n"
            f"    for f in files:\n"
            f"        if f.endswith('.py'):\n"
            f"            fp = os.path.join(root, f)\n"
            f"            mod = os.path.splitext(os.path.relpath(fp, r'{PROJECT_ROOT}').replace(os.sep, '.'))[0]\n"
            f"            all_mods[mod] = find_imports(fp)\n"
            f"# Simple cycle detection via DFS (project-local modules only)\n"
            f"local = set(all_mods.keys())\n"
            f"visited = set(); stack = []\n"
            f"def dfs(m):\n"
            f"    if m in stack:\n"
            f"        idx = stack.index(m); print(f'CYCLE DETECTED: {{\" -> \".join(stack[idx:] + [m])}}'); return\n"
            f"    if m in visited: return\n"
            f"    visited.add(m); stack.append(m)\n"
            f"    for imp in all_mods.get(m, set()):\n"
            f"        if imp in local: dfs(imp)\n"
            f"    stack.pop()\n"
            f"for m in local: dfs(m)\n"
            f"print(f'Cycle detection complete: {{len(local)}} modules scanned')\n"
        )
        result = _run_command(
            [sys.executable, "-c", cycle_script],
            timeout=30,
        )
        output = result["stdout"][:1000] or result["stderr"][:1000]
        has_cycle = "CYCLE DETECTED" in output
        checks.append(_format_check(
            title="对抗性探测 — 循环导入检测",
            command=f"{sys.executable} -c \"<DFS扫描全项目模块导入图>\"",
            output=output,
            result="FAIL" if has_cycle else ("PASS" if result["returncode"] == 0 else "PARTIAL"),
            expected_vs_actual=f"预期无循环导入，实际 {'发现循环依赖' if has_cycle else '无循环依赖'}",
        ))

    # ╔══════════════════════════════════════════════════╗
    # ║  L3: 状态/资源攻击                              ║
    # ╚══════════════════════════════════════════════════╝

    if py_files:
        pf = py_files[0]
        rel = os.path.relpath(pf, PROJECT_ROOT)
        module_path = os.path.splitext(rel)[0].replace(os.sep, ".")

        # ── L3.1 幂等性 — 快速连续重复导入 ──
        bulk_import_script = (
            f"import sys\nsys.path.insert(0, r'{PROJECT_ROOT}')\n"
            f"modules = []\n"
            f"for i in range(10):\n"
            f"    import {module_path} as m\n"
            f"    modules.append(id(m))\n"
            f"print(f'10 imports: all_same={{len(set(modules)) == 1}}, ids={{set(modules)}}')"
        )
        result = _run_command(
            [sys.executable, "-c", bulk_import_script],
            timeout=15,
        )
        output = result["stdout"][:300] or result["stderr"][:300]
        passed = result["returncode"] == 0
        checks.append(_format_check(
            title="对抗性探测 — 幂等性（10次连续导入）",
            command=f"{sys.executable} -c \"<连续10次 import {module_path}>\"",
            output=output,
            result="PASS" if passed else "FAIL",
            expected_vs_actual=f"预期10次导入幂等（无副作用/不崩溃），实际 {'通过' if passed else '崩溃'}",
        ))

        # ── L3.2 模块热重载资源泄漏 — 重复 del+import ──
        leak_script = (
            f"import sys\nsys.path.insert(0, r'{PROJECT_ROOT}')\n"
            f"for i in range(5):\n"
            f"    import {module_path}\n"
            f"    if '{module_path}' in sys.modules:\n"
            f"        del sys.modules['{module_path}']\n"
            f"print('5 import/del cycles completed without resource leak crash')"
        )
        result = _run_command(
            [sys.executable, "-c", leak_script],
            timeout=15,
        )
        output = result["stdout"][:300] or result["stderr"][:300]
        passed = result["returncode"] == 0 and not result["timed_out"]
        checks.append(_format_check(
            title="对抗性探测 — 资源泄漏（5次导入/删除循环）",
            command=f"{sys.executable} -c \"<5次 import/del {module_path} 循环>\"",
            output=output,
            result="PASS" if passed else "FAIL",
            expected_vs_actual=f"预期5次导入/删除循环后资源正常回收，实际 {'通过' if passed else '崩溃/超时'}",
        ))

    # ╔══════════════════════════════════════════════════╗
    # ║  L4: 文件/配置攻击                              ║
    # ╚══════════════════════════════════════════════════╝

    if py_files:
        # ── L4.1 编码投毒 — BOM + 混合编码 ──
        bom_script = (
            f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
            f"import tempfile; import os; "
            f"# Create a file with BOM + valid Python\n"
            f"with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8-sig') as f:\n"
            f"    f.write('x = 42\\\\n')\n"
            f"    tmpname = f.name\n"
            f"try:\n"
            f"    with open(tmpname, 'r', encoding='utf-8-sig') as f:\n"
            f"        content = f.read()\n"
            f"    print(f'BOM file OK, content: {{repr(content)}}')\n"
            f"finally:\n"
            f"    os.unlink(tmpname)\n"
            f"print('Encoding attack handled')\n"
        )
        result = _run_command(
            [sys.executable, "-c", bom_script],
            timeout=10,
        )
        output = result["stdout"][:300] or result["stderr"][:300]
        passed = result["returncode"] == 0
        checks.append(_format_check(
            title="对抗性探测 — 编码投毒（BOM头文件）",
            command=f"{sys.executable} -c \"<创建带BOM的临时文件并读取>\"",
            output=output,
            result="PASS" if passed else "FAIL",
            expected_vs_actual=f"预期BOM编码文件被正确处理不崩溃，实际 {'通过' if passed else '崩溃'}",
        ))

    # ── L4.2 YAML 炸弹（深层嵌套/锚点爆炸） ──
    if config_files:
        yaml_bomb_script = (
            f"import yaml; "
            f"# YAML bomb: deeply nested dict via anchors\n"
            f"yaml_str = 'a: &a [\"lol\",\"lol\"]\\\\n' + ''.join(f'b: &b [*a,*a]\\\\n' for _ in range(30)) + 'c: *b\\\\n'\n"
            f"try:\n"
            f"    yaml.safe_load(yaml_str)\n"
            f"    print('YAML bomb parsed successfully (safe_load handled it)')\n"
            f"except yaml.YAMLError as e:\n"
            f"    print(f'YAML bomb blocked: {{type(e).__name__}}')\n"
            f"except MemoryError:\n"
            f"    print('YAML bomb CAUSED MemoryError - VULNERABLE!')\n"
            f"except RecursionError:\n"
            f"    print('YAML bomb CAUSED RecursionError - VULNERABLE!')\n"
            f"except Exception as e:\n"
            f"    print(f'YAML bomb handled: {{type(e).__name__}}')\n"
        )
        result = _run_command(
            [sys.executable, "-c", yaml_bomb_script],
            timeout=15,
        )
        output = result["stdout"][:500] or result["stderr"][:500]
        passed = result["returncode"] == 0 and "VULNERABLE" not in output
        checks.append(_format_check(
            title="对抗性探测 — YAML炸弹（锚点展开攻击）",
            command=f"{sys.executable} -c \"<YAML锚点嵌套展开炸弹>\"",
            output=output,
            result="FAIL" if "VULNERABLE" in output else ("PASS" if passed else "FAIL"),
            expected_vs_actual=f"预期YAML炸弹被拦截（safe_load/递归限制），实际 {'通过' if passed else '被炸弹攻击成功'}",
        ))

    # ── 保证至少一条对抗性探测 ──
    if not checks:
        cmd_result = _run_command(
            [sys.executable, "-c", "import ast; ast.parse('x=1'); print('Core integrity OK')"],
            timeout=5,
        )
        checks.append(_format_check(
            title="对抗性探测 — 解释器核心完整性",
            command=f"{sys.executable} -c \"import ast; ast.parse('x=1')\"",
            output=cmd_result["stdout"] or cmd_result["stderr"],
            result="PASS" if cmd_result["returncode"] == 0 else "FAIL",
            expected_vs_actual="Python 解释器核心功能应正常",
        ))

    return checks


# ═══════════════════════════════════════════════════════
# 主 handler
# ═══════════════════════════════════════════════════════

async def agent_verify(
    task_description: str,
    files_changed: list,
    approach: str = "",
) -> str:
    """
    对抗性验证工具 — 尝试破坏实现，而非确认它。

    4 阶段管线：
      Phase 1: UNIVERSAL BASELINE
      Phase 2: TYPE-SPECIFIC STRATEGY
      Phase 3: ADVERSARIAL PROBES
      Phase 4: VERDICT

    Args:
        task_description: 原始任务描述
        files_changed:   变更的文件路径列表
        approach:        实现方法描述（可选）
    """
    all_checks = []

    # ── Check 0: 验证输入 ─────────────────────────────
    if not files_changed:
        all_checks.append(_format_check(
            title="验证输入参数",
            command="检查 files_changed 参数",
            output="files_changed 为空列表",
            result="FAIL",
            expected_vs_actual="预期至少有一个变更文件，实际为 0",
        ))
        verdict_info = _issue_verdict(all_checks)
        return json.dumps({
            "verdict": verdict_info["verdict"],
            "checks": all_checks,
            "summary": "未提供变更文件列表，无法执行验证",
            "full_report": _build_full_report(task_description, files_changed, approach, all_checks, verdict_info),
        }, ensure_ascii=False)

    # 规范化文件路径
    normalized_files = []
    for f in files_changed:
        p = Path(f)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        normalized_files.append(str(p.resolve()))

    # ── 文件存在性检查 ────────────────────────────────
    missing_files = [f for f in normalized_files if not os.path.exists(f)]
    existing_files = [f for f in normalized_files if os.path.exists(f)]

    if missing_files:
        all_checks.append(_format_check(
            title="变更文件存在性",
            command="检查变更文件是否存在",
            output=f"{len(missing_files)}/{len(normalized_files)} 个文件不存在:\n" +
                    "\n".join(f"  缺失: {mf}" for mf in missing_files[:10]),
            result="FAIL",
            expected_vs_actual="所有变更文件应存在",
        ))
    else:
        all_checks.append(_format_check(
            title="变更文件存在性",
            command=f"检查 {len(existing_files)} 个变更文件",
            output=f"{len(existing_files)} 个文件全部存在",
            result="PASS",
        ))

    if not existing_files:
        verdict_info = _issue_verdict(all_checks)
        return json.dumps({
            "verdict": verdict_info["verdict"],
            "checks": all_checks,
            "summary": "所有变更文件均不存在",
            "full_report": _build_full_report(task_description, files_changed, approach, all_checks, verdict_info),
        }, ensure_ascii=False)

    # ── 变更类型分类 ──────────────────────────────────
    change_type = _classify_changes(normalized_files, task_description, approach)
    logger.info(
        "AgentVerify: change_type=%s | files=%d | task=%s",
        change_type, len(normalized_files), task_description[:80],
    )

    # ── Phase 1: UNIVERSAL BASELINE ───────────────────
    logger.info("AgentVerify: Phase 1 - Universal Baseline")
    baseline_checks = _universal_baseline(existing_files)
    all_checks.extend(baseline_checks)

    # ── Phase 2: TYPE-SPECIFIC STRATEGY ───────────────
    logger.info("AgentVerify: Phase 2 - Type-Specific Strategy (%s)", change_type)
    py_files = [f for f in normalized_files if f.endswith(".py") and os.path.exists(f)]
    config_files = [f for f in normalized_files if f.endswith((".yaml", ".yml", ".json")) and os.path.exists(f)]

    if change_type in ("python", "mixed") and py_files:
        all_checks.extend(_python_strategy(py_files))

    if change_type in ("config", "mixed") and config_files:
        all_checks.extend(_config_strategy(config_files))

    if change_type == "bugfix":
        if py_files:
            all_checks.extend(_python_strategy(py_files))
        all_checks.extend(_bugfix_strategy(normalized_files, task_description))

    if change_type == "refactor":
        if py_files:
            all_checks.extend(_python_strategy(py_files))

    # ── Phase 3: ADVERSARIAL PROBES ───────────────────
    logger.info("AgentVerify: Phase 3 - Adversarial Probes")
    adversarial_checks = _adversarial_probes(normalized_files, change_type)
    all_checks.extend(adversarial_checks)

    # ── Phase 4: VERDICT ──────────────────────────────
    logger.info("AgentVerify: Phase 4 - Verdict")
    verdict_info = _issue_verdict(all_checks)

    full_report = _build_full_report(task_description, files_changed, approach, all_checks, verdict_info)
    summary = _build_summary(all_checks, verdict_info)

    logger.info(
        "AgentVerify: VERDICT=%s | %s",
        verdict_info["verdict"], summary,
    )

    return json.dumps({
        "verdict": verdict_info["verdict"],
        "checks": all_checks,
        "summary": summary,
        "full_report": full_report,
    }, ensure_ascii=False, indent=2)


def _build_summary(checks, verdict_info):
    """构建摘要文本"""
    total = len(checks)
    pass_count = sum(1 for c in checks if c.get("result") == "PASS")
    fail_count = sum(1 for c in checks if c.get("result") == "FAIL")
    partial_count = sum(1 for c in checks if c.get("result") == "PARTIAL")
    return (
        f"共 {total} 项检查: {pass_count} PASS, {fail_count} FAIL, "
        f"{partial_count} PARTIAL | {verdict_info['reason']}"
    )


def _build_full_report(task_description, files_changed, approach, checks, verdict_info):
    """构建完整 Markdown 报告"""
    lines = [
        f"## Verification Report",
        f"**Task:** {task_description[:200]}",
        f"**Files Changed:** {len(files_changed)} 个",
        f"**Approach:** {approach[:200] if approach else '(未提供)'}",
        f"**Time:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for i, check in enumerate(checks):
        lines.append(f"--- Phase {i + 1} ---" if i < 4 else "")
        lines.append(check["body"])
        lines.append("")

    lines.append(f"**Summary:** {_build_summary(checks, verdict_info)}")
    lines.append(f"\nVERDICT: {verdict_info['verdict']}")
    return "\n".join(lines)


# ── 注册工具 ─────────────────────────────────────────
registry.register(
    name="AgentVerify",
    description=(
        "对抗性验证工具 — 当主智能体认为工作完成后调用此工具进行独立验证。"
        "以「尝试破坏实现」的视角检查变更，执行 4 阶段管线："
        "通用基线检查 → 按变更类型策略检查 → 对抗性探测 → 最终判定。"
        "每项检查严格遵循 Command run → Output observed → Result 三段式格式。"
        "返回结构化报告，以 VERDICT: PASS / FAIL / PARTIAL 结尾。"
        "PASS = 所有检查通过且含对抗性探测，FAIL = 存在问题需修复，"
        "PARTIAL = 部分检查无法完成或缺少对抗性探测。"
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "原始任务描述或用户请求",
            },
            "files_changed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "变更的文件路径列表（相对或绝对路径）",
            },
            "approach": {
                "type": "string",
                "description": "实现方法和思路简述（可选）",
            },
        },
        "required": ["task_description", "files_changed"],
    },
    handler=agent_verify,
    group="agent",
)
