"""真实环境测试：向 ChatGPT 网页版提一个问题，打印抽出的 Markdown 和截图路径。

用法：
    python tests/ask_once.py "问题" [--headless] [--images a.jpg b.png]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import BridgeError, ChatGPTBridge  # noqa: E402
from extract import html_to_markdown  # noqa: E402

from _env import PROFILE, PROXY  # noqa: E402

OUT = Path(__file__).resolve().parent / ".ask-out"


async def main() -> None:
    args = sys.argv[1:]
    headless = "--headless" in args
    if headless:
        args.remove("--headless")
    images = []
    if "--images" in args:
        idx = args.index("--images")
        images = args[idx + 1 :]
        args = args[:idx]
    prompt = args[0] if args else "用中文一句话解释欧姆定律。"
    OUT.mkdir(exist_ok=True)
    bridge = ChatGPTBridge(
        PROFILE,
        channel="auto",
        proxy=PROXY,
        headless=headless,
        timeout=300,
        log=lambda level, msg: print(f"[{level}] {msg}", flush=True),
    )
    try:
        await bridge.start()
        result = await bridge.ask(
            "local-test",
            prompt,
            images,
            want_image=True,
            image_out_dir=OUT,
            timeout=300,
        )
        markdown = result.get("markdown") or html_to_markdown(
            result.get("html", ""), result.get("maths")
        )
        print("=" * 50)
        print(markdown[:3000])
        print("=" * 50)
        print("images:", result.get("image_paths"))
    except BridgeError as exc:
        print("BridgeError:", exc)
        raise SystemExit(1)
    finally:
        await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
