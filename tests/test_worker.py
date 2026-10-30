"""按插件的真实方式测试：Playwright 跑在独立线程的事件循环里，主线程用 wrap_future 等结果。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import BrowserWorker, ChatGPTBridge  # noqa: E402

from _env import PROFILE, PROXY  # noqa: E402


async def call(worker: BrowserWorker, coro):
    return await asyncio.wrap_future(worker.submit(coro))


async def main() -> None:
    worker = BrowserWorker()
    bridge = ChatGPTBridge(PROFILE, channel="auto", proxy=PROXY, headless=False)
    try:
        await call(worker, bridge.start())
        print("started in worker thread")
        info = await call(worker, bridge.status())
        print("status:", {k: info.get(k) for k in ("logged_in", "plan", "headless")})
        result = await call(
            worker,
            bridge.ask("worker-test", "只回答两个字：正常", want_image=False),
        )
        print("answer:", (result.get("markdown") or "").strip()[:60])
    finally:
        try:
            await call(worker, bridge.close())
        except Exception as exc:
            print("close:", exc)
        worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
