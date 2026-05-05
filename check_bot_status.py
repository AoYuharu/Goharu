"""
检查 QQ Bot 账号状态
"""

import asyncio
import httpx


async def check_bot_status():
    """检查机器人状态"""
    print("=" * 60)
    print("QQ Bot 账号状态检查")
    print("=" * 60)
    print()

    app_id = "102839705"
    client_secret = "wOrKoInIoLsQyX6gGrS4gJxbGvbHyfN5"

    # 1. 获取 access token
    print("步骤 1: 获取 Access Token")
    print("-" * 60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 获取 token
        token_url = "https://bots.qq.com/app/getAppAccessToken"
        response = await client.post(
            token_url,
            json={"appId": app_id, "clientSecret": client_secret}
        )

        print(f"状态码: {response.status_code}")
        print(f"响应: {response.text}")

        if response.status_code != 200:
            print("\n[ERROR] 无法获取 token")
            return

        data = response.json()
        access_token = data["access_token"]
        print(f"\n[SUCCESS] Token 获取成功")
        print(f"Token (前20字符): {access_token[:20]}...")
        print(f"有效期: {data.get('expires_in')}秒")

        # 2. 测试各种 API 端点
        print("\n\n步骤 2: 测试 API 端点")
        print("-" * 60)

        endpoints = [
            ("获取机器人信息", "GET", "/users/@me", {}),
            ("获取 Gateway", "GET", "/gateway", {}),
            ("获取 Gateway Bot", "GET", "/gateway/bot", {}),
        ]

        for name, method, path, payload in endpoints:
            print(f"\n测试: {name}")
            print(f"  端点: {method} {path}")

            url = f"https://api.sgroup.qq.com{path}"
            headers = {
                "Authorization": f"QQBot {access_token}",
                "Content-Type": "application/json",
            }

            try:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, json=payload, headers=headers)

                print(f"  状态码: {resp.status_code}")
                print(f"  响应: {resp.text[:200]}")

                if resp.status_code == 200:
                    print(f"  [SUCCESS]")
                    # 如果是 gateway/bot，显示详细信息
                    if path == "/gateway/bot":
                        bot_data = resp.json()
                        print(f"  Gateway URL: {bot_data.get('url')}")
                        print(f"  Shards: {bot_data.get('shards')}")
                        print(f"  Session start limit: {bot_data.get('session_start_limit')}")
                else:
                    print(f"  [FAIL]")

            except Exception as e:
                print(f"  [ERROR] {e}")

    print("\n\n" + "=" * 60)
    print("诊断结果")
    print("=" * 60)
    print("""
根据测试结果：

1. 如果所有 API 都返回 401/403，说明：
   - Token 没有相应的权限
   - 机器人可能未激活或未通过审核

2. 如果 /gateway 返回 200 但 WebSocket 鉴权失败 (op=9)，说明：
   - 机器人没有 WebSocket 连接权限
   - 需要在 QQ 开放平台申请权限

3. 如果 /gateway/bot 返回 200，说明：
   - 机器人配置正常
   - 可以查看 session_start_limit 了解连接限制

建议：
- 登录 https://q.qq.com/ 查看机器人状态
- 检查机器人是否已通过审核
- 查看是否有权限配置选项
- 联系 QQ 开放平台技术支持
    """)


if __name__ == "__main__":
    asyncio.run(check_bot_status())
