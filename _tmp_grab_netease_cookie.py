"""临时脚本：打开网易云音乐页面，等用户手动登录后抓取 Cookie 存到 MyJar。

用完即删。不打印 Cookie 内容。
"""
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

JAR = Path(r"D:\MyProject\Programming\python\pythonsome\pythonsome\newbroker\MUSIC\MyJar\goharu.txt")
PROFILE = Path.home() / ".goharu_netease_profile"
TIMEOUT = 600  # 10 分钟


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            no_viewport=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://music.163.com/", wait_until="domcontentloaded")
        log("浏览器已打开 https://music.163.com/ ，请在弹出的窗口里登录（扫码/手机号均可）")

        deadline = time.time() + TIMEOUT
        last_note = 0
        cookie_str = ""
        while time.time() < deadline:
            try:
                cookies = ctx.cookies(["https://music.163.com", "https://music.163.com/#/"])
            except Exception as e:
                log(f"读取 cookie 出错: {e}")
                cookies = []
            jar = {c["name"]: c["value"] for c in cookies}
            if jar.get("MUSIC_U"):
                # 抓一份域名覆盖完整的 cookie（含 .music.163.com）
                all_cookies = []
                for c in cookies:
                    all_cookies.append(f'{c["name"]}={c["value"]}')
                cookie_str = "; ".join(all_cookies)
                log("检测到 MUSIC_U，登录成功，开始校验账号…")
                break
            if time.time() - last_note > 30:
                last_note = time.time()
                log(f"等待登录中… 剩余 {int(deadline - time.time())}s")
            time.sleep(2)

        if not cookie_str:
            log("超时未检测到登录状态，未写入文件")
            ctx.close()
            return 1

        # 校验账号信息
        try:
            resp = page.request.get(
                "https://music.163.com/api/nuser/account/get",
                headers={"Cookie": cookie_str, "Referer": "https://music.163.com/"},
            )
            data = resp.json()
            profile = (data.get("profile") or {})
            account = (data.get("account") or {})
            log("账号: %s (uid=%s), vipType=%s" % (
                profile.get("nickname"), profile.get("userId"), profile.get("vipType")))
            if account.get("anonymousUser") or not profile:
                log("警告：接口返回未登录状态，不写入")
                ctx.close()
                return 2
        except Exception as e:
            log(f"账号校验接口异常（仍继续写入）: {e}")

        if JAR.exists():
            bak = JAR.with_suffix(".txt.bak")
            shutil.copy2(JAR, bak)
            log(f"旧 cookie 已备份到 {bak.name}")
        JAR.parent.mkdir(parents=True, exist_ok=True)
        JAR.write_text(cookie_str + "\n", encoding="utf-8")
        log(f"已写入 {JAR}（{len(cookie_str)} 字符）")
        log("可以关闭浏览器窗口了")
        time.sleep(3)
        ctx.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
