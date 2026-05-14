import json
from datetime import datetime

def calculate_stats(numbers):
    """计算统计信息"""
    if not numbers:
        return {}
    
    sorted_nums = sorted(numbers)
    n = len(numbers)
    
    return {
        "count": n,
        "sum": sum(numbers),
        "mean": sum(numbers) / n,
        "median": sorted_nums[n // 2] if n % 2 else (sorted_nums[n//2 - 1] + sorted_nums[n//2]) / 2,
        "min": min(numbers),
        "max": max(numbers)
    }

def main():
    # 测试数据
    data = [23, 45, 12, 67, 34, 89, 56, 78, 90, 11]
    
    print("=" * 40)
    print("数据统计工具")
    print("=" * 40)
    print(f"\n原始数据: {data}")
    
    stats = calculate_stats(data)
    
    print("\n统计结果:")
    print("-" * 20)
    for key, value in stats.items():
        print(f"  {key:8s}: {value:.2f}")
    
    # 返回 JSON 格式结果
    result = {
        "timestamp": datetime.now().isoformat(),
        "data": data,
        "stats": stats,
        "success": True
    }
    
    print("\n" + "=" * 40)
    print("JSON 输出:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    return result

if __name__ == "__main__":
    result = main()
    exit(0)
