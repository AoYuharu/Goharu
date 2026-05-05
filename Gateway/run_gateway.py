"""
Gateway 启动脚本 - 独立运行 Gateway
"""

import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Gateway.gateway_runner import GatewayRunner


async def main():
    """Gateway 主函数"""
    print("=" * 60)
    print("TableHelper Gateway")
    print("=" * 60)
    print()

    gateway = GatewayRunner()

    try:
        await gateway.start()
        print("\n[Gateway] Running... Press Ctrl+C to stop\n")
        await gateway.run_forever()
    except Exception as e:
        print(f"[Gateway] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await gateway.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Gateway] Shutdown complete")
