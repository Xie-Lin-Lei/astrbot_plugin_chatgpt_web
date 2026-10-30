"""验证「图片等待合并」逻辑，不需要 QQ/ChatGPT。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 导入 AstrBot 会在当前目录生成 data/，换到临时目录里跑，别污染仓库
os.chdir(tempfile.mkdtemp(prefix="astrbot-test-"))

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _env import ASTRBOT_HOME  # noqa: E402

if ASTRBOT_HOME:
    sys.path.insert(0, ASTRBOT_HOME)
try:
    from astrbot_plugin_chatgpt_web.main import ChatGPTWebPlugin  # noqa: E402
except ModuleNotFoundError:
    raise SystemExit(
        "需要先把 ASTRBOT_HOME 指向 AstrBot 源码目录（例如 D:/path/to/AstrBot）再运行"
    )


class FakeEvent:
    def __init__(self, text="", images=None, umo="snowluma:FriendMessage:1"):
        self.unified_msg_origin = umo
        self._text = text
        self._images = list(images or [])

    def get_message_str(self):
        return self._text

    def get_messages(self):
        return self._images

    def get_sender_id(self):
        return "user"

    def get_self_id(self):
        return "bot"

    def is_private_chat(self):
        return True

    def is_at_or_wake_command(self):
        return True

    async def send(self, _chain):
        pass


async def main() -> None:
    plugin = ChatGPTWebPlugin(None, {"passive": True, "allowed_sessions": [], "image_wait_seconds": 2})
    calls = []

    async def fake_answer(event, prompt, *, new_chat=False, images=None):
        calls.append((prompt, list(images or [])))

    async def fake_collect(event):
        return list(event.get_messages() or [])

    plugin._answer = fake_answer
    plugin._collect_images = fake_collect

    # 1) 只发图 -> 等 2 秒后单独提问
    await plugin.on_message(FakeEvent(images=["a.jpg"]))
    assert calls == [], calls
    await asyncio.sleep(2.4)
    assert calls == [("", ["a.jpg"])], calls
    print("1 纯图片延迟发送 OK")

    # 2) 先图后字 -> 合并
    calls.clear()
    await plugin.on_message(FakeEvent(images=["b.jpg"]))
    await asyncio.sleep(0.2)
    await plugin.on_message(FakeEvent(text="讲解这道题"))
    await asyncio.sleep(0.2)
    assert calls == [("讲解这道题", ["b.jpg"])], calls
    await asyncio.sleep(2.2)
    assert len(calls) == 1, calls
    print("2 先图后字合并 OK")

    # 3) 图 + 字同一条 -> 立即一次提问
    calls.clear()
    await plugin.on_message(FakeEvent(text="看这张图", images=["c.jpg"]))
    assert calls == [("看这张图", ["c.jpg"])], calls
    print("3 图文同条立即发送 OK")

    # 4) 连续两张图 + 文字 -> 全部合并
    calls.clear()
    await plugin.on_message(FakeEvent(images=["d1.jpg"]))
    await plugin.on_message(FakeEvent(images=["d2.jpg"]))
    await plugin.on_message(FakeEvent(text="一起看"))
    await asyncio.sleep(0.2)
    assert calls == [("一起看", ["d1.jpg", "d2.jpg"])], calls
    print("4 多图+文字合并 OK")

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
