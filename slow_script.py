import time

def fib(n):
    if n <= 1:
        return n
    return fib(n-1) + fib(n-2)

print("开始计算斐波那契数列...")
start = time.time()

result = 0
for i in range(30):
    result = fib(i)
    print(f"fib({i}) = {result}")

elapsed = time.time() - start
print(f"\n计算完成，耗时: {elapsed:.2f} 秒")
print(f"最终结果: {result}")
