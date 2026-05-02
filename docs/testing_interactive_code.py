"""
示例：如何测试包含 input() 的交互式代码

原始代码（calculator.py）包含 input()，无法直接测试。
解决方案：创建独立的测试文件，测试核心函数。
"""

# ===== 原始代码结构 =====
# calculator.py 包含：
# 1. 核心函数：add(), subtract(), multiply(), divide()
# 2. 交互式函数：calculator() - 包含 input()

# ===== 测试策略 =====
# test_calculator.py 应该：
# 1. 导入核心函数
# 2. 测试核心函数（不测试交互式部分）
# 3. 运行 pytest 或直接运行测试文件

# ===== 示例测试文件 =====
"""
# test_calculator.py
from calculator import add, subtract, multiply, divide
import pytest

def test_add():
    assert add(10, 5) == 15
    assert add(-5, 3) == -2
    assert add(0, 0) == 0

def test_subtract():
    assert subtract(10, 5) == 5
    assert subtract(5, 10) == -5

def test_multiply():
    assert multiply(10, 5) == 50
    assert multiply(-2, 3) == -6

def test_divide():
    assert divide(10, 5) == 2.0
    assert divide(9, 3) == 3.0

    # 测试除零错误
    with pytest.raises(ValueError):
        divide(10, 0)

if __name__ == '__main__':
    # 如果没有 pytest，可以直接运行
    test_add()
    test_subtract()
    test_multiply()
    test_divide()
    print("所有测试通过！")
"""

# ===== 模型应该做的 =====
# 1. 识别 calculator.py 包含 input()
# 2. 创建 test_calculator.py 测试核心函数
# 3. 运行 python test_calculator.py
# 4. 报告测试结果

# ===== 模型不应该做的 =====
# ❌ 直接运行 python calculator.py（会卡住等待输入）
# ❌ 尝试自动输入（复杂且不可靠）
