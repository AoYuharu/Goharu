"""Bootstrap 契约测试：探测层 + 编排（identity 落盘 / 幂等 / Arch 补漏派发）。

直接运行：python tests/test_bootstrap.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # noqa: E402

from Architect.bootstrap import bootstrap_if_needed  # noqa: E402
from Architect.probe import run_probe  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def test_probe_real():
    """真实探测本机：关键字段必须探到（hostname/os/python/cpu）。"""
    print("== 真实探测（本机）==")
    identity, gaps = run_probe()
    check("12 类齐全", len(identity) == 12)
    check("hostname 探到", bool(identity["device"]["hostname"]))
    check("os.name 探到", bool(identity["os"]["name"]))
    check("cpu 核数探到", bool(identity["cpu"]["cores_physical"]))
    check("内存总量探到", bool(identity["memory"]["total_bytes"]))
    check("python 版本探到", bool(identity["runtime"]["python"]))
    check("gaps 是 list", isinstance(gaps, list))
    check("internet 探到（布尔）",
          isinstance(identity["network"]["internet"], bool))


class FakeHub:
    def __init__(self):
        self.wakes = []

    def request_wake(self, req, background=True):
        self.wakes.append(req)
        return {"status": "dispatched"}


def _fake_probe(identity, gaps):
    return lambda: (identity, gaps)


def test_bootstrap_flow():
    print("== 编排流程 ==")
    with tempfile.TemporaryDirectory() as tmp:
        id_path = str(Path(tmp) / "identity.yaml")
        cfg = {"architect_hub": {"bootstrap": {"identity_path": id_path}}}
        hub = FakeHub()

        identity = {"device": {"hostname": "test-host"},
                    "os": {"name": "TestOS"}}
        gaps = ["gpu.driver", "network.gateway"]

        r = bootstrap_if_needed(hub=hub, config=cfg, force=True,
                                run_probe_fn=_fake_probe(identity, gaps))
        check("返回结果含步骤", "steps" in r)
        check("identity.yaml 落盘", Path(id_path).exists())
        data = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("identity 含 hostname", data["device"]["hostname"] == "test-host")
        check("gaps_pending 记录", data["gaps_pending"] == gaps)
        check("harunet 未接线标记", data["harunet"]["pending_retry"] is True)

        check("Arch 补漏派发", len(hub.wakes) == 1
              and hub.wakes[0].reason == "bootstrap"
              and "gpu.driver" in hub.wakes[0].context["gaps"])

        # 幂等：identity 存在 → 跳过
        r2 = bootstrap_if_needed(hub=hub, config=cfg)
        check("幂等：已存在跳过", r2 is None and len(hub.wakes) == 1)

        # 无 gaps → 不派发 Arch
        r3 = bootstrap_if_needed(hub=hub, config=cfg, force=True,
                                 run_probe_fn=_fake_probe(identity, []))
        check("无 gaps 不派 Arch", r3["steps"]["arch_gapfill"] == "no_gaps"
              and len(hub.wakes) == 1)


def test_harunet_register():
    print("== HaruNet 注册缝 ==")
    with tempfile.TemporaryDirectory() as tmp:
        id_path = str(Path(tmp) / "identity.yaml")
        cfg = {"architect_hub": {"bootstrap": {"identity_path": id_path}}}
        identity = {"device": {"hostname": "h"}}

        # 注册成功
        r = bootstrap_if_needed(
            hub=FakeHub(), config=cfg, force=True,
            register_fn=lambda ident: {"agent_id": "agt_test123"},
            run_probe_fn=_fake_probe(identity, []))
        data = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("注册成功写入 agent_id",
              data["harunet"]["agent_id"] == "agt_test123")

        # 注册失败降级
        def boom(_):
            raise ConnectionError("harunet unreachable")
        r2 = bootstrap_if_needed(
            hub=FakeHub(), config=cfg, force=True,
            register_fn=boom,
            run_probe_fn=_fake_probe(identity, []))
        check("注册失败不阻塞 bootstrap", r2 is not None)
        data2 = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("失败标记待重试", data2["harunet"]["pending_retry"] is True)


def main():
    test_probe_real()
    test_bootstrap_flow()
    test_harunet_register()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
