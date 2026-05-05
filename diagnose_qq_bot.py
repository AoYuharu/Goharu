"""
QQ Bot 诊断脚本
帮助诊断连接问题
"""

import asyncio
import httpx


async def diagnose():
    """诊断 QQ Bot 配置"""
    print("=" * 60)
    print("QQ Bot 诊断工具")
    print("=" * 60)
    print()

    app_id = "102839705"
    bot_token = "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"

    # 测试不同的 API 端点
    endpoints = [
        ("正式环境 Gateway", "https://api.sgroup.qq.com/gateway"),
        ("沙箱环境 Gateway", "https://sandbox.api.sgroup.qq.com/gateway"),
    ]

    # 测试不同的 Authorization 格式
    auth_formats = [
        ("Bot {app_id}.{token}", f"Bot {app_id}.{bot_token}"),
        ("QQBot {token}", f"QQBot {bot_token}"),
        ("Bearer {token}", f"Bearer {bot_token}"),
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for endpoint_name, endpoint_url in endpoints:
            print(f"\n{'=' * 60}")
            print(f"测试: {endpoint_name}")
            print(f"URL: {endpoint_url}")
            print(f"{'=' * 60}")

            for auth_name, auth_value in auth_formats:
                print(f"\n  尝试认证格式: {auth_name}")
                headers = {"Authorization": auth_value}

                try:
                    response = await client.get(endpoint_url, headers=headers)
                    print(f"  状态码: {response.status_code}")
                    print(f"  响应: {response.text[:200]}")

                    if response.status_code == 200:
                        print(f"  [SUCCESS] Authentication successful!")
                        data = response.json()
                        print(f"  Gateway URL: {data.get('url')}")
                    elif response.status_code == 401:
                        print(f"  [FAIL] Authentication failed (401)")
                    elif response.status_code == 404:
                        print(f"  [FAIL] Endpoint not found (404)")
                    else:
                        print(f"  [WARN] Other error")

                except Exception as e:
                    print(f"  [ERROR] Request failed: {e}")

    print("\n" + "=" * 60)
    print("诊断建议:")
    print("=" * 60)
    print("""
1. 如果所有请求都返回 401，说明凭证不正确或没有权限
2. 如果返回 404，说明 API 端点不存在
3. 如果返回 200，说明认证成功，可以使用该配置

请检查：
- 您的机器人是否是 QQ 频道机器人（bot.q.qq.com）
- 您的机器人是否已经通过审核
- 您的 app_id 和 bot_token 是否正确
- 您的机器人是否有 WebSocket 连接权限

注意：
- QQ 频道机器人和 QQ 群机器人是不同的系统
- 某些机器人类型可能不支持 WebSocket 连接
- 可能需要在开放平台申请特定的权限
    """)


if __name__ == "__main__":
    asyncio.run(diagnose())
