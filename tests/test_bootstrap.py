"""Bootstrap 契约测试：L1-L5 探测、四态证据、编排与安装者引导。

直接运行：python tests/test_bootstrap.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # noqa: E402

import Architect.probe as probe  # noqa: E402
import Architect.explore_flow as explore_flow  # noqa: E402
from Architect.bootstrap import bootstrap_if_needed  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_probe_real():
    print("== 真实探测（本机）==")
    identity, gaps = probe.run_probe()
    check("14 类齐全", len(identity) - 1 == 14, list(identity))
    check("五层报告齐全", list(identity["probe_report"]) == list(probe.LAYER_TITLES))
    check("hostname 探到", bool(identity["device"]["hostname"]))
    check("os.name 探到", bool(identity["os"]["name"]))
    check("cpu 核数探到", bool(identity["cpu"]["cores_physical"]))
    check("内存总量探到", bool(identity["memory"]["total_bytes"]))
    check("python 版本探到", bool(identity["runtime"]["python"]))
    check("internet 探到（布尔）", isinstance(identity["network"]["internet"], bool))
    statuses = {
        field["status"]
        for layer in identity["probe_report"].values()
        for field in layer["fields"].values()
    }
    check("四态取值合法", statuses <= {"verified", "unknown", "unavailable", "not_applicable"})
    unknown = [
        path
        for layer in identity["probe_report"].values()
        for path, field in layer["fields"].items()
        if field["status"] == "unknown"
    ]
    check("只有 unknown 进入 gaps", set(gaps) == set(unknown), gaps)
    check("每项都有证据", all(
        field["evidence"]
        for layer in identity["probe_report"].values()
        for field in layer["fields"].values()
    ))


def test_layered_probe():
    print("== 分层顺序与异常隔离 ==")
    original = probe.PROBE_TABLE
    calls = []

    def broken():
        calls.append("L1")
        raise RuntimeError("sensitive detail")

    def succeed(layer, result):
        def run():
            calls.append(layer)
            return result
        return run

    try:
        probe.PROBE_TABLE = [
            ("L1", "device", broken, ("hostname",)),
            ("L2", "runtime", succeed("L2", {"python": "3.12"}), ("python",)),
            ("L3", "communication", succeed(
                "L3", {"usb_count": 0, "serial_ports": []}),
             ("usb_count", "serial_ports")),
            ("L4", "peripherals", succeed(
                "L4", {"camera": False, "audio": False}), ("camera", "audio")),
            ("L5", "actuation", succeed(
                "L5", {"gpio_chips": [], "control_candidates": []}),
             ("gpio_chips", "control_candidates")),
        ]
        identity, gaps = probe.run_probe()
    finally:
        probe.PROBE_TABLE = original
    check("L1 异常后 L2-L5 继续", calls == ["L1", "L2", "L3", "L4", "L5"], calls)
    check("异常字段进入 gaps", gaps == ["device.hostname"], gaps)
    report = identity["probe_report"]
    check("异常证据不泄露原始异常文本",
          "sensitive detail" not in report["L1"]["fields"]["device.hostname"]["evidence"]
          and "RuntimeError" in report["L1"]["fields"]["device.hostname"]["evidence"])
    check("False/0/空列表均为 verified",
          report["L3"]["fields"]["communication.usb_count"]["status"] == "verified"
          and report["L3"]["fields"]["communication.serial_ports"]["status"] == "verified"
          and report["L4"]["fields"]["peripherals.camera"]["status"] == "verified")


def test_field_states():
    print("== 四态判定 ==")
    old = probe.IS_WINDOWS, probe.IS_MACOS, probe._which
    try:
        probe.IS_WINDOWS = probe.IS_MACOS = False
        probe._which = lambda _name: None
        check("缺失可执行文件是 unavailable",
              probe._field_state("runtime", "java", None, {}) == "unavailable")
        check("Linux 无 GPIO 候选是 not_applicable",
              probe._field_state("actuation", "control_candidates", None, {}) == "not_applicable")
        check("普通缺值是 unknown",
              probe._field_state("cpu", "instruction_sets", None, {}) == "unknown")
        probe.IS_WINDOWS = True
        check("Windows 未探物理能力不能误报不适用",
              probe._field_state("actuation", "control_candidates", None, {}) == "unknown")
    finally:
        probe.IS_WINDOWS, probe.IS_MACOS, probe._which = old


def test_os_specific_communication():
    print("== Windows/Linux/macOS 通信探测分支 ==")
    names = ("IS_WINDOWS", "IS_MACOS", "_ps", "_windows_serial_ports",
             "_which", "_run")
    old = {name: getattr(probe, name) for name in names}
    old_glob = probe.glob.glob
    try:
        probe.IS_WINDOWS, probe.IS_MACOS = True, False
        probe._ps = lambda *_args, **_kwargs: "3"
        probe._windows_serial_ports = lambda: ["COM3", "COM4"]
        result = probe.probe_communication()
        check("Windows USB/串口", result == {"usb_count": 3, "serial_ports": ["COM3", "COM4"]})

        probe.IS_WINDOWS, probe.IS_MACOS = False, True
        probe._which = lambda name: f"/usr/sbin/{name}"
        probe._run = lambda _cmd: "Product ID: 0x1\nProduct ID: 0x2"
        probe.glob.glob = lambda _pattern: ["/dev/cu.usbmodem1"]
        result = probe.probe_communication()
        check("macOS USB/串口", result == {"usb_count": 2, "serial_ports": ["/dev/cu.usbmodem1"]})

        probe.IS_MACOS = False
        probe._run = lambda _cmd: "Bus 001 Device 002: ID 1234:5678 Test"
        probe.glob.glob = lambda _pattern: []
        result = probe.probe_communication()
        check("Linux USB/串口", result == {"usb_count": 1, "serial_ports": []})
    finally:
        for name, value in old.items():
            setattr(probe, name, value)
        probe.glob.glob = old_glob


class FakeHub:
    def __init__(self):
        self.wakes = []

    def request_wake(self, req, background=True):
        self.wakes.append(req)
        return {"status": "dispatched"}


def _identity():
    return {
        "device": {"hostname": "test-host"},
        "os": {"name": "TestOS"},
        "cpu": {"instruction_sets": None},
        "runtime": {"git": None},
        "probe_report": {
            "L1": {"title": "系统与硬件基础", "fields": {
                "device.hostname": {"status": "verified", "evidence": "fake"},
                "cpu.instruction_sets": {"status": "unknown", "evidence": "fake"},
            }},
            "L2": {"title": "软件与中间件", "fields": {
                "runtime.git": {"status": "unknown", "evidence": "fake"},
            }},
        },
    }


def _fake_probe(identity, gaps):
    return lambda: (identity, gaps)


def test_bootstrap_flow():
    print("== 编排与初始探索方向 ==")
    with tempfile.TemporaryDirectory() as tmp:
        id_path = str(Path(tmp) / "identity.yaml")
        cfg = {"architect_hub": {"bootstrap": {
            "identity_path": id_path,
            "initial_exploration": "  先查摄像头\n  再查 GPIO  ",
        }}}
        hub = FakeHub()
        gaps = ["cpu.instruction_sets", "runtime.git"]
        r = bootstrap_if_needed(hub=hub, config=cfg, force=True,
                                run_probe_fn=_fake_probe(_identity(), gaps))
        check("返回结果含步骤", "steps" in r)
        data = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("identity 落盘且保留原字段", data["device"]["hostname"] == "test-host")
        check("gaps_pending 记录", data["gaps_pending"] == gaps)
        check("harunet 未接线标记", data["harunet"]["pending_retry"] is True)
        check("只派发一次 Arch", len(hub.wakes) == 1)
        wake = hub.wakes[0]
        check("使用 explore 类型", wake.reason == "bootstrap" and wake.kind == "explore")
        check("缺项按层分组", wake.context["layered_gaps"] == {
            "L1": ["cpu.instruction_sets"], "L2": ["runtime.git"]})
        check("引导压缩并进入任务", wake.context["initial_exploration"] == "先查摄像头 再查 GPIO")
        check("已验证事实进入任务",
              wake.context["verified_facts"]["L1"]["device.hostname"] == "test-host")
        check("包含只读与禁止重复约束",
              "不要重复已验证探测" in wake.context["instruction"]
              and "禁止安装软件" in wake.context["instruction"])

        r2 = bootstrap_if_needed(hub=hub, config=cfg)
        check("幂等：已存在跳过", r2 is None and len(hub.wakes) == 1)

        cfg["architect_hub"]["bootstrap"]["initial_exploration"] = ""
        r3 = bootstrap_if_needed(hub=hub, config=cfg, force=True,
                                 run_probe_fn=_fake_probe(_identity(), []))
        check("无 gaps 无引导不唤醒", r3["steps"]["arch_gapfill"] == "no_gaps"
              and len(hub.wakes) == 1)

        cfg["architect_hub"]["bootstrap"]["initial_exploration"] = "只检查摄像头"
        r4 = bootstrap_if_needed(hub=hub, config=cfg, force=True,
                                 run_probe_fn=_fake_probe(_identity(), []))
        check("无 gaps 有引导仍只唤醒一次", len(hub.wakes) == 2
              and "0 gaps, initial_exploration" in r4["steps"]["arch_gapfill"])
        check("方向任务内容正确", hub.wakes[-1].context["initial_exploration"] == "只检查摄像头")


def test_harunet_register():
    print("== HaruNet 注册缝 ==")
    with tempfile.TemporaryDirectory() as tmp:
        id_path = str(Path(tmp) / "identity.yaml")
        cfg = {"architect_hub": {"bootstrap": {"identity_path": id_path}}}
        r = bootstrap_if_needed(
            hub=FakeHub(), config=cfg, force=True,
            register_fn=lambda _ident: {"agent_id": "agt_test123"},
            run_probe_fn=_fake_probe(_identity(), []))
        data = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("注册成功写入 agent_id", r is not None
              and data["harunet"]["agent_id"] == "agt_test123")

        def boom(_):
            raise ConnectionError("harunet unreachable")

        r2 = bootstrap_if_needed(
            hub=FakeHub(), config=cfg, force=True, register_fn=boom,
            run_probe_fn=_fake_probe(_identity(), []))
        data2 = yaml.safe_load(Path(id_path).read_text(encoding="utf-8"))
        check("注册失败不阻塞 bootstrap", r2 is not None)
        check("失败标记待重试", data2["harunet"]["pending_retry"] is True)


def test_explore_flow_hard_rule():
    print("== 闭环：硬规则直通 ==")
    calls = {"arch": 0, "verify": 0}

    def arch(round_no, context, feedback):
        calls["arch"] += 1
        return {"status": "success"}

    def verify(identity_path, gaps, round_no):
        calls["verify"] += 1
        return {"status": "success", "verdict": "PASS"}

    def read(path):
        return {"gaps_pending": []}

    r = explore_flow.run_explore_with_verify(
        {"identity_path": "/tmp/identity.yaml", "explore_max_rounds": 3},
        dispatch_arch=arch, dispatch_verify=verify, read_identity=read)
    check("gaps 清空直接 PASS", r["verdict"] == "PASS")
    check("只跑一轮", calls["arch"] == 1 and calls["verify"] == 1, calls)
    check("剩余 gaps 空", r["remaining_gaps"] == [])


def test_explore_flow_best_effort():
    print("== 闭环：verify 判已尽力 PASS ==")
    def arch(round_no, context, feedback):
        return {"status": "success"}

    def verify(identity_path, gaps, round_no):
        return {"status": "success", "verdict": "PASS",
                "content": "剩余 unknown 已带凭据，机器可定位"}

    def read(path):
        return {"gaps_pending": ["cpu.instruction_sets"]}

    r = explore_flow.run_explore_with_verify(
        {"identity_path": "/tmp/identity.yaml", "explore_max_rounds": 3},
        dispatch_arch=arch, dispatch_verify=verify, read_identity=read)
    check("已尽力 PASS", r["verdict"] == "PASS")
    check("剩余 unknown 记录", r["remaining_gaps"] == ["cpu.instruction_sets"])
    check("只跑一轮", len(r["rounds"]) == 1)


def test_explore_flow_feedback():
    print("== 闭环：FAIL 回喂迭代 ==")
    state = {"gaps": ["cpu.instruction_sets", "gpu.driver"]}
    feedbacks = []

    def arch(round_no, context, feedback):
        feedbacks.append(feedback)
        if round_no == 2:
            state["gaps"] = []
        return {"status": "success"}

    def verify(identity_path, gaps, round_no):
        return {"status": "success",
                "verdict": "FAIL" if round_no == 1 else "PASS",
                "content": "缺 cpu.instruction_sets 凭据"}

    def read(path):
        return {"gaps_pending": list(state["gaps"])}

    r = explore_flow.run_explore_with_verify(
        {"identity_path": "/tmp/identity.yaml", "explore_max_rounds": 3},
        dispatch_arch=arch, dispatch_verify=verify, read_identity=read)
    check("迭代两轮", len(r["rounds"]) == 2, [x["round"] for x in r["rounds"]])
    check("第二轮回喂含 FAIL 项", "cpu.instruction_sets" in feedbacks[1], feedbacks[1])
    check("最终 PASS", r["verdict"] == "PASS")


def test_explore_flow_max_rounds():
    print("== 闭环：max_rounds 截断防无尽循环 ==")
    def arch(round_no, context, feedback):
        return {"status": "success"}

    def verify(identity_path, gaps, round_no):
        return {"status": "success", "verdict": "FAIL", "content": "仍未尽力"}

    def read(path):
        return {"gaps_pending": ["cpu.instruction_sets"]}

    r = explore_flow.run_explore_with_verify(
        {"identity_path": "/tmp/identity.yaml", "explore_max_rounds": 2},
        dispatch_arch=arch, dispatch_verify=verify, read_identity=read)
    check("跑满 2 轮", len(r["rounds"]) == 2, len(r["rounds"]))
    check("best_effort 状态", r["status"] == "best_effort")
    check("最终 FAIL", r["verdict"] == "FAIL")


def main():
    test_probe_real()
    test_layered_probe()
    test_field_states()
    test_os_specific_communication()
    test_bootstrap_flow()
    test_harunet_register()
    test_explore_flow_hard_rule()
    test_explore_flow_best_effort()
    test_explore_flow_feedback()
    test_explore_flow_max_rounds()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
