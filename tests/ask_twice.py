"""测试追问：同一个会话里连续问两个问题，第二个依赖第一个的上下文。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import ChatGPTBridge  # noqa: E402

from _env import PROFILE, PROXY  # noqa: E402

OUT = Path(__file__).resolve().parent / ".ask-out"


async def main() -> None:
    bridge = ChatGPTBridge(
        PROFILE, channel="auto", proxy=PROXY, headless=False
    )
    try:
        await bridge.start()
        first = await bridge.ask(
            "follow-test",
            "分压偏置电路的基极电压 VB 公式是什么？只给公式，不要解释。",
            want_image=False,
        )
        print("Q1 ->", first.get("markdown", "")[:200])
        second = await bridge.ask(
            "follow-test",
            "把上面公式里的 R1 换成 20kΩ、R2 换成 10kΩ、VCC 换成 12V，算出 VB。",
            want_image=False,
        )
        print("Q2 ->", second.get("markdown", "")[:300])
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
