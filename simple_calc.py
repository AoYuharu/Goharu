"""简易计算器"""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("除数不能为零")
    return a / b


if __name__ == '__main__':
    print("===== 计算器功能测试 =====\n")

    # 测试加法
    assert add(2, 3) == 5, "加法测试失败"
    print(f"[OK] 加法: 2 + 3 = {add(2, 3)}")

    # 测试减法
    assert subtract(10, 4) == 6, "减法测试失败"
    print(f"[OK] 减法: 10 - 4 = {subtract(10, 4)}")

    # 测试乘法
    assert multiply(6, 7) == 42, "乘法测试失败"
    print(f"[OK] 乘法: 6 * 7 = {multiply(6, 7)}")

    # 测试除法
    assert divide(20, 4) == 5, "除法测试失败"
    print(f"[OK] 除法: 20 / 4 = {divide(20, 4)}")

    # 测试除零错误
    try:
        divide(1, 0)
        print("[FAIL] 除零测试失败")
    except ValueError:
        print("[OK] 除零错误处理正常")

    print("\n===== 所有测试通过! =====")
