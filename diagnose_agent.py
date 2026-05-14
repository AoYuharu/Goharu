"""诊断智能体执行问题"""
import sys
import traceback
from pathlib import Path

print("=== 智能体诊断工具 ===\n")

# 1. 检查导入
print("1. 检查模块导入...")
try:
    from Agent.Delegates.PaperAnalysisDelegate import PaperAnalysisDelegate
    print("   ✓ PaperAnalysisDelegate 导入成功")
except Exception as e:
    print(f"   ✗ 导入失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# 2. 检查配置
print("\n2. 检查配置...")
try:
    from configurationLoader import config
    max_iterations = config.get("agent_delegate.max_iterations", 8)
    print(f"   ✓ max_iterations = {max_iterations}")
except Exception as e:
    print(f"   ✗ 配置加载失败: {e}")

# 3. 检查工具注册表
print("\n3. 检查工具注册表...")
try:
    from Tools.registry import registry
    read_tool = registry.get_entry("Read")
    if read_tool:
        print(f"   ✓ Read 工具已注册")
    else:
        print(f"   ✗ Read 工具未注册")
except Exception as e:
    print(f"   ✗ 工具注册表错误: {e}")

# 4. 测试创建子智能体
print("\n4. 测试创建子智能体...")
try:
    delegate = PaperAnalysisDelegate(
        agent_type="content_analysis",
        task="测试任务",
        agent_id="test_001",
        tools_registry=registry,
        output_callback=None
    )
    print("   ✓ 子智能体创建成功")

    # 测试构建消息
    messages = delegate._build_prompt_messages()
    print(f"   ✓ 消息构建成功，共 {len(messages)} 条")

    # 检查项目路径
    import os
    for msg in messages:
        if "项目上下文" in msg.get("content", ""):
            print(f"   ✓ 项目路径已注入: {os.getcwd()}")
            break

except Exception as e:
    print(f"   ✗ 创建失败: {e}")
    traceback.print_exc()

print("\n=== 诊断完成 ===")
