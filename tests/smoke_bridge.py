"""冒烟测试：确认 Playwright 能用本机 Edge 打开 ChatGPT 并读取登录状态。

用法：
    python tests/smoke_bridge.py [--headful] [--proxy http://127.0.0.1:7897]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import BridgeError, ChatGPTBridge  # noqa: E402
from _env import PROXY  # noqa: E402


async def main() -> None:
    headful = "--headful" in sys.argv
    proxy = PROXY
    if "--proxy" in sys.argv:
        proxy = sys.argv[sys.argv.index("--proxy") + 1]
    profile = Path(__file__).resolve().parent / ".smoke-profile"
    bridge = ChatGPTBridge(
        profile,
        channel="auto",
        proxy=proxy,
        headless=not headful,
        timeout=120,
        log=lambda level, msg: print(f"[{level}] {msg}"),
    )
    try:
        await bridge.start()
        info = await bridge.status()
        print("status:", info)
    except BridgeError as exc:
        print("BridgeError:", exc)
        raise SystemExit(1)
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
