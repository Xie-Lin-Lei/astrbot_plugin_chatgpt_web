"""测试脚本共用的环境配置。

这些脚本是开发/自测用的，不对普通用户提供成品体验：

    set CHATGPT_WEB_PROFILE=D:\\path\\to\\browser_profile
    set CHATGPT_WEB_PROXY=http://127.0.0.1:7897
    set ASTRBOT_HOME=D:\\path\\to\\AstrBot

`CHATGPT_WEB_PROFILE` 指向一个已经登录过 ChatGPT 的 Playwright 资料目录。
"""

import os
from pathlib import Path

PROFILE = Path(
    os.environ.get(
        "CHATGPT_WEB_PROFILE",
        str(Path.home() / ".chatgpt-web-profile"),
    )
).expanduser()

PROXY = os.environ.get("CHATGPT_WEB_PROXY", "")

ASTRBOT_HOME = os.environ.get("ASTRBOT_HOME", "")
