"""打开可见浏览器窗口，等待用户手动登录 ChatGPT，登录成功后自动退出。

用法：
    python tests/login_browser.py [profile_dir]
默认使用环境变量 CHATGPT_WEB_PROFILE 指向的资料目录。
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import ChatGPTBridge  # noqa: E402

from _env import PROFILE, PROXY  # noqa: E402


async def main() -> None:
    profile = Path(sys.argv[1]) if len(sys.argv) > 1 else PROFILE
    proxy = PROXY
    if "--proxy" in sys.argv:
        proxy = sys.argv[sys.argv.index("--proxy") + 1]

    bridge = ChatGPTBridge(
        profile,
        channel="auto",
        proxy=proxy,
        headless=False,
        log=lambda level, msg: print(f"[{level}] {msg}", flush=True),
    )
    print(f"profile: {profile}", flush=True)
    try:
        await bridge.start()
        await bridge.login()
        deadline = time.time() + 600
        last_report = 0.0
        while time.time() < deadline:
            try:
                info = await bridge.status()
            except Exception as exc:
                print("等待中：", exc, flush=True)
                info = {}
            if info.get("logged_in"):
                print(
                    "✅ 已登录：",
                    info.get("email", ""),
                    info.get("plan", ""),
                    flush=True,
                )
                return
            if time.time() - last_report > 20:
                print("…还没有检测到登录，请在弹出的窗口里完成登录", flush=True)
                last_report = time.time()
            await asyncio.sleep(3)
        print("❌ 超时，仍未登录", flush=True)
        raise SystemExit(2)
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
