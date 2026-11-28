"""GitHub 连通性探测与自动代理解析测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Architect.connectivity import (  # noqa: E402
    AutoProxyResolver, detect_github_proxy, probe_remote,
)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def make_runner(working):
    calls = []

    def runner(cmd, timeout):
        calls.append((cmd, timeout))
        if "config" in cmd and "--get" in cmd:
            return 0, "https://github.com/AoYuharu/Goharu.git"
        if "ls-remote" in cmd:
            joined = " ".join(cmd)
            proxy = ""
            for part in cmd:
                if ".proxy=" in part:
                    proxy = part.split(".proxy=", 1)[1]
            key = proxy or "direct"
            if key in working:
                return 0, "abc\trefs/heads/master"
            return 128, f"cannot access {key}"
        return 0, ""

    return runner, calls


def main():
    print("=" * 60)
    print("GitHub 连通性测试")
    print("=" * 60)

    print("\n[1] 自动选择可用代理")
    runner, calls = make_runner({"http://127.0.0.1:7893"})
    result = detect_github_proxy(
        "/repo", candidates=["http://127.0.0.1:7890", "http://127.0.0.1:7893"],
        env={}, runner=runner)
    check("找到可用代理", result["ok"] and result["proxy"] == "http://127.0.0.1:7893",
          result)
    check("失败候选被记录", any("7890" in e for e in result["errors"]), result)
    check("探测使用超时", all(c[1] > 0 for c in calls))

    print("\n[2] 直连兜底")
    runner, _ = make_runner({"direct"})
    result = detect_github_proxy(
        "/repo", candidates=["http://127.0.0.1:7893"], env={}, runner=runner)
    check("代理失败后直连成功", result["ok"] and result["proxy"] == "", result)

    print("\n[3] 全部失败")
    runner, _ = make_runner(set())
    result = detect_github_proxy(
        "/repo", candidates=["http://127.0.0.1:7893"], env={}, runner=runner)
    check("返回失败与错误清单", not result["ok"] and result["errors"], result)

    print("\n[4] AutoProxyResolver 缓存与回写")
    selected = []
    runner, calls = make_runner({"http://p2"})
    resolver = AutoProxyResolver(
        "/repo", "origin", "master",
        configured_provider=lambda: ("", ["github.com"]),
        candidate_provider=lambda: ["http://p1", "http://p2"],
        on_selected=selected.append,
        runner=runner,
    )
    url, rules = resolver()
    check("首次解析选中 p2", url == "http://p2" and rules == ["github.com"],
          (url, rules))
    check("成功代理回写回调", selected == ["http://p2"], selected)
    n = len(calls)
    resolver()
    check("解析结果缓存", len(calls) == n)
    resolver.reset()
    resolver()
    check("reset 后重新探测", len(calls) > n)

    print("\n[5] probe_remote 错误")
    runner, _ = make_runner(set())
    check("探测失败返回细节",
          not probe_remote("/repo", "origin", "master", runner=runner)["ok"])

    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
