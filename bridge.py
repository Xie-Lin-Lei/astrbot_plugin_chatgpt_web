"""ChatGPT 网页版浏览器桥。

用 Playwright 驱动本机已经装好的 Edge（channel="msedge"），并复用一份持久化
的浏览器资料目录，所以只要人工登录一次，后面都能自动带着登录态访问。

所有 Playwright 调用都跑在一个独立线程自己的事件循环里：AstrBot 的循环可能被
Quart/Hypercorn 改造过，把浏览器驱动塞进去容易踩 Windows 下子进程/事件循环的坑，
隔离开以后也方便随时重启浏览器而不影响机器人主进程。
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import os
import platform
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:  # 作为插件包导入（AstrBot），或作为顶层模块导入（测试脚本）
    from .extract import html_to_markdown, normalize_copied_markdown
    from .render import render_to_images
except ImportError:  # pragma: no cover
    from extract import html_to_markdown, normalize_copied_markdown
    from render import render_to_images

CHATGPT_HOME = "https://chatgpt.com/"

# 2026 版网页端（app shell）结构：
#   提交按钮   form[data-chatgpt-composer] button[type=submit]
#   输入框     div[contenteditable][data-composer-markdown]
#   一轮对话   [data-talvt-turn-state="in_progress"|"complete"]
#   回答正文   [data-markdown-copy="contents"]
# 旧版结构作为兜底一起兼容。
SEL_COMPOSER = (
    "[contenteditable='true'][data-composer-markdown], #prompt-textarea,"
    " [contenteditable='true']"
)
SEL_SEND = (
    "form[data-chatgpt-composer] button[type='submit'],"
    " button[aria-label='发送'], button[data-testid='send-button']"
)
SEL_STOP = "button[aria-label*='停止'], button[data-testid='stop-button']"
SEL_TURN = "[data-talvt-turn-state]"
SEL_ASSISTANT = (
    "[data-content-search-unit-key$=':assistant'],"
    " [data-message-author-role='assistant']"
)
SEL_COPY_ROOT = "[data-markdown-copy='contents']"

CHALLENGE_HINTS = ("just a moment", "请稍候", "verifying you are human", "检查浏览器")

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
_MD_IMAGE_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\((https?://[^)\s]+)\)")
_IMAGE_URL_HINT = re.compile(
    r"images\.openai\.com|\.(?:png|jpe?g|gif|webp|bmp)(?:\?|$)|purpose=inline", re.I
)

EXTRACT_JS = r"""
() => {
  const copies = [...document.querySelectorAll('[data-markdown-copy="contents"]')]
    .filter(n => n.getBoundingClientRect().width > 0);
  let src = copies.length ? copies[copies.length - 1] : null;
  if (!src) {
    const blocks = [...document.querySelectorAll('[data-content-search-unit-key$=":assistant"], [data-message-author-role="assistant"]')]
      .filter(n => n.getBoundingClientRect().width > 0);
    const last = blocks[blocks.length - 1];
    if (last) src = last.querySelector('.markdown') || last;
  }
  if (!src) return { error: '没有找到 ChatGPT 的回答' };

  const block = src.closest('[data-content-search-unit-key$=":assistant"], [data-message-author-role="assistant"]');
  const clone = src.cloneNode(true);
  clone.querySelectorAll('button, svg, style, script, noscript, h4.sr-only, [hidden]').forEach(n => n.remove());

  const maths = [];
  const token = (i) => '\u0002MATH' + i + '\u0002';
  const texOf = (n) => {
    const a = n.querySelector('annotation[encoding="application/x-tex"]');
    if (a && a.textContent.trim()) return a.textContent.trim();
    const m = n.querySelector('.katex-mathml');
    if (m) return m.textContent.replace(/\s+/g, ' ').trim();
    return '';
  };

  clone.querySelectorAll('.katex-display').forEach(n => {
    const tex = texOf(n);
    if (!tex) return;
    maths.push({ tex: tex, display: true });
    n.replaceWith(document.createTextNode(' ' + token(maths.length - 1) + ' '));
  });
  clone.querySelectorAll('.katex').forEach(n => {
    // 行间公式（.katex-display）已经被整体替换掉了，跳过它内部的 .katex
    if (n.closest('.katex-display')) return;
    const tex = texOf(n);
    if (!tex) return;
    maths.push({ tex: tex, display: false });
    n.replaceWith(document.createTextNode(token(maths.length - 1)));
  });

  return {
    html: clone.innerHTML,
    text: (src.innerText || '').trim(),
    url: location.href,
    maths: maths,
    height: block ? block.getBoundingClientRect().height : src.getBoundingClientRect().height
  };
}
"""

STATE_JS = r"""
() => {
  const turns = document.querySelectorAll('[data-talvt-turn-state]');
  const lastTurn = turns[turns.length - 1];
  const state = lastTurn ? (lastTurn.getAttribute('data-talvt-turn-state') || '') : '';
  const stop = document.querySelector('button[aria-label*="停止"], button[data-testid="stop-button"]');
  const copies = [...document.querySelectorAll('[data-markdown-copy="contents"]')]
    .filter(n => n.getBoundingClientRect().width > 0);
  const lastCopy = copies[copies.length - 1];
  return {
    streaming: state === 'in_progress' || !!stop,
    state: state,
    count: turns.length,
    len: lastCopy ? (lastCopy.innerText || '').length : 0,
    alert: (() => {
      const el = document.querySelector('[role="alert"]');
      return el ? (el.innerText || '').trim().slice(0, 300) : '';
    })()
  };
}
"""


UPLOAD_STATE_JS = r"""
() => {
  const alertEl = document.querySelector('[role="alert"]');
  const b = document.querySelector('form[data-chatgpt-composer] button[type="submit"]')
      || document.querySelector('button[aria-label="发送"]')
      || document.querySelector('button[data-testid="send-button"]');
  let ready = false;
  if (b && !b.disabled) {
    const r = b.getBoundingClientRect();
    ready = r.width > 0 && r.height > 0;
  }
  return {
    ready: ready,
    alert: alertEl ? (alertEl.innerText || '').trim().slice(0, 300) : '',
    attachments: document.querySelectorAll('button[aria-label^="移除"]').length,
    hasForm: !!document.querySelector('form[data-chatgpt-composer]'),
  };
}
"""


class BridgeError(Exception):
    """浏览器桥可预期的错误，消息可以直接发给用户。"""


class NotLoggedInError(BridgeError):
    pass


class ChallengeRequiredError(BridgeError):
    pass


class AskTimeoutError(BridgeError):
    pass


def detect_browser_channels() -> list[str]:
    """返回本机可用的 Playwright channel 名称，按优先级排序。"""
    system = platform.system()
    if system == "Windows":
        candidates = [
            (
                "msedge",
                [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                ],
            ),
            (
                "chrome",
                [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    os.path.expandvars(
                        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
                    ),
                ],
            ),
        ]
    elif system == "Darwin":
        candidates = [
            ("msedge", ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]),
            (
                "chrome",
                [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                ],
            ),
        ]
    else:
        candidates = [
            ("msedge", ["/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"]),
            (
                "chrome",
                [
                    "/usr/bin/google-chrome",
                    "/usr/bin/google-chrome-stable",
                    "/usr/bin/chromium",
                    "/usr/bin/chromium-browser",
                ],
            ),
        ]
    return [name for name, paths in candidates if any(Path(p).exists() for p in paths)]


def detect_system_proxy() -> str:
    """配置里没填代理时，依次尝试环境变量和 Windows 系统代理。"""
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key)
        if value:
            return value
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if enabled and server:
                if "=" in server:
                    parts = dict(
                        item.split("=", 1) for item in server.split(";") if "=" in item
                    )
                    server = parts.get("https") or parts.get("http") or ""
                if server and not server.startswith(("http://", "socks")):
                    server = "http://" + server
                return server
        except Exception:
            pass
    return ""


class BrowserWorker:
    """一个只跑 Playwright 的线程 + 独立事件循环。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="chatgpt-web-browser", daemon=True
        )
        self._thread.start()
        self._ready.wait(10)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, coro) -> concurrent.futures.Future:
        self.ensure_started()
        if self._loop is None:
            raise BridgeError("浏览器线程没有启动")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)


class ChatGPTBridge:
    """对上层只暴露 async 方法。"""

    def __init__(
        self,
        profile_dir: str | Path,
        *,
        channel: str = "auto",
        proxy: str = "",
        headless: bool = False,
        timeout: int = 300,
        max_sessions: int = 4,
        max_image_height: int = 8000,
        min_interval: float = 3.0,
        locale: str = "",
        timezone_id: str = "",
        log: Any = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.channel = channel
        self.proxy = proxy
        self.headless = headless
        self.timeout = timeout
        self.max_sessions = max(1, max_sessions)
        self.max_image_height = max(600, max_image_height)
        self.min_interval = max(0.0, float(min_interval or 0))
        self.locale = locale
        self.timezone_id = timezone_id
        self._proxy = ""
        self._last_done = 0.0
        self._log = log or (lambda *a, **k: None)

        self._pw = None
        self._ctx = None
        self._pages: dict[str, Any] = {}
        self._order: list[str] = []
        self._render_page = None
        self._lock: asyncio.Lock | None = None

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        await self._ensure_browser()

    async def close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.close()
            except Exception:
                pass
            self._ctx = None
            self._pages.clear()
            self._order.clear()
            self._render_page = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def _ensure_browser(self) -> None:
        if self._ctx is not None:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._ctx is not None:
                return
            await self._launch_browser()

    def _resolve_proxy(self) -> str:
        if self.proxy and self.proxy.strip():
            return self.proxy.strip()
        detected = detect_system_proxy()
        if detected:
            self._log("info", f"未配置代理，自动使用系统代理：{detected}")
        return detected

    def _channel_candidates(self) -> list[str | None]:
        wanted = (self.channel or "").strip()
        if wanted and wanted.lower() != "auto":
            return [wanted]
        detected = detect_browser_channels()
        if detected:
            self._log("info", f"检测到已安装的浏览器：{', '.join(detected)}")
        # None 表示 Playwright 自带的 Chromium
        return detected + [None]

    async def _launch_browser(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - 缺依赖时给出明确提示
            raise BridgeError(
                "缺少 playwright，请在 AstrBot 的虚拟环境里执行：pip install playwright"
            ) from exc

        self._pw = await async_playwright().start()
        self._proxy = self._resolve_proxy()
        base_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "viewport": {"width": 1280, "height": 960},
            "device_scale_factor": 2,
            "args": [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,960",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if self.locale:
            base_kwargs["locale"] = self.locale
        if self.timezone_id:
            base_kwargs["timezone_id"] = self.timezone_id
        if self._proxy:
            base_kwargs["proxy"] = {"server": self._proxy}

        last_error: Exception | None = None
        tried: list[str] = []
        for channel in self._channel_candidates():
            kwargs = dict(base_kwargs)
            if channel:
                kwargs["channel"] = channel
                tried.append(channel)
            else:
                tried.append("Playwright Chromium")
            for _ in range(2):
                try:
                    self._ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as exc:
                    last_error = exc
                    text = str(exc).lower()
                    if "already in use" in text or "has been closed" in text:
                        # 上一次的浏览器还没退干净，等它释放资料目录再试
                        await asyncio.sleep(2.0)
                        continue
                    break
            if self._ctx is not None:
                break

        if self._ctx is None:
            await self._safe_stop_playwright()
            message = str(last_error or "")
            lower = message.lower()
            if "user data directory is already in use" in lower:
                raise BridgeError(
                    "浏览器资料目录正被另一个窗口占用，请关掉那个窗口后重试"
                )
            if "executable" in lower or "channel" in lower:
                raise BridgeError(
                    "没有找到可用的浏览器（尝试过："
                    + "、".join(tried)
                    + "）。请安装 Edge 或 Chrome，或执行 "
                    "python -m playwright install chromium 后重试。"
                    f"（{message[:140]}）"
                )
            raise BridgeError(f"启动浏览器失败：{message[:200]}")
        self._ctx.set_default_timeout(30_000)
        self._ctx.set_default_navigation_timeout(90_000)
        try:
            await self._ctx.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin="https://chatgpt.com"
            )
        except Exception as exc:
            self._log("debug", f"授予剪贴板权限失败：{exc}")
        self._log("info", f"浏览器已启动 profile={self.profile_dir} headless={self.headless}")

    async def _safe_stop_playwright(self) -> None:
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def login(self, *, headful: bool = True) -> dict:
        """打开可见窗口让用户手动登录，返回当前页面地址。"""
        if headful and self.headless:
            await self.close()
            self.headless = False
        await self._ensure_browser()
        assert self._lock is not None
        async with self._lock:
            page = await self._ctx.new_page()
            await page.goto(CHATGPT_HOME, wait_until="domcontentloaded")
            try:
                await page.bring_to_front()
            except Exception:
                pass
            return {"url": page.url}

    # ---------- 页面管理 ----------

    def _touch(self, key: str) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    async def _page_for(self, key: str, *, new_chat: bool) -> Any:
        page = self._pages.get(key)
        if page is not None and page.is_closed():
            self._pages.pop(key, None)
            page = None
        if page is None:
            if len(self._pages) >= self.max_sessions and self._order:
                old_key = self._order.pop(0)
                old_page = self._pages.pop(old_key, None)
                if old_page is not None and not old_page.is_closed():
                    try:
                        await old_page.close()
                    except Exception:
                        pass
            page = await self._ctx.new_page()
            self._pages[key] = page
            await self._goto_home(page)
        elif new_chat:
            await self._goto_home(page)
        self._touch(key)
        await self._require_composer(page)
        return page

    async def _goto_home(self, page) -> None:
        await page.goto(CHATGPT_HOME, wait_until="domcontentloaded")
        await self._require_composer(page)

    async def _require_composer(self, page, timeout_ms: int = 60_000) -> None:
        try:
            await page.wait_for_selector(SEL_COMPOSER, state="visible", timeout=timeout_ms)
        except Exception:
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            lowered = title.lower()
            if any(hint in lowered for hint in CHALLENGE_HINTS):
                raise ChallengeRequiredError(
                    "ChatGPT 弹出了 Cloudflare 人机验证。发送「gpt登录」打开浏览器窗口手动过一次验证，"
                    "之后保持登录态即可。"
                )
            info = await self._session_info(page)
            if not info.get("user"):
                raise NotLoggedInError(
                    "还没有登录 ChatGPT 网页版。发送「gpt登录」打开浏览器窗口登录一次。"
                )
            raise BridgeError(f"ChatGPT 页面没有加载出输入框（title={title[:60]!r}）")

    async def _session_info(self, page) -> dict:
        try:
            return await page.evaluate(
                """async () => {
                    try {
                        const r = await fetch('/api/auth/session', {cache: 'no-store'});
                        const data = await r.json();
                        return data && typeof data === 'object' ? data : {};
                    } catch (e) {
                        return {error: String(e)};
                    }
                }"""
            )
        except Exception as exc:
            return {"error": str(exc)}

    # ---------- 提问 ----------

    async def ask(
        self,
        key: str,
        text: str,
        images: list[str] | None = None,
        *,
        new_chat: bool = False,
        want_image: bool = True,
        image_out_dir: str | Path | None = None,
        timeout: int | None = None,
    ) -> dict:
        images = [str(p) for p in (images or [])]
        timeout = int(timeout or self.timeout)
        await self._ensure_browser()
        assert self._lock is not None

        async with self._lock:
            gap = self.min_interval - (time.time() - self._last_done)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                page = await self._page_for(key, new_chat=new_chat)
                before = int(
                    await page.evaluate(
                        "() => document.querySelectorAll('[data-talvt-turn-state]').length"
                    )
                )
                await self._submit(page, text, images)
                await self._wait_started(page, before)
                await self._wait_finished(page, timeout, before)

                data = await page.evaluate(EXTRACT_JS)
                if not data or data.get("error"):
                    raise BridgeError((data or {}).get("error") or "读取回答失败")

                # 优先用消息自带的「复制」按钮拿 Markdown：公式、代码块、表格都是原样
                copied = ""
                try:
                    copied = await self._copy_last_answer(page)
                except Exception as exc:
                    self._log("debug", f"复制答案失败：{exc}")
                markdown = normalize_copied_markdown(copied)
                if not markdown.strip():
                    markdown = html_to_markdown(data.get("html", ""), data.get("maths"))
                if not markdown.strip():
                    markdown = (data.get("text") or "").strip()
                if not markdown:
                    raise BridgeError("ChatGPT 这次没有给出文字回答（可能触发了限制或报错）")

                image_paths: list[str] = []
                if want_image and image_out_dir is not None:
                    try:
                        markdown = await self._inline_images(page, markdown)
                        render_page = await self._get_render_page()
                        image_paths = await render_to_images(
                            render_page,
                            markdown,
                            Path(image_out_dir),
                            max_height=self.max_image_height,
                        )
                    except Exception as exc:
                        self._log("warning", f"答案渲染成图片失败：{exc}")
                if want_image and image_out_dir is not None and not image_paths:
                    self._log(
                        "warning",
                        "答案太长或渲染失败，没有生成图片，改为发送文字",
                    )
                return {
                    "markdown": markdown,
                    "text": data.get("text", ""),
                    "html": data.get("html", ""),
                    "url": data.get("url", ""),
                    "image_paths": image_paths,
                }
            finally:
                self._last_done = time.time()

    async def _submit(self, page, text: str, images: list[str]) -> None:
        if images:
            await self._upload_images(page, images)
            await self._wait_upload_ready(page)
        composer = page.locator(SEL_COMPOSER).first
        await composer.click()
        # 清掉可能残留的草稿
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        if text:
            for index, line in enumerate(text.split("\n")):
                if index:
                    await page.keyboard.press("Shift+Enter")
                if line:
                    await page.keyboard.insert_text(line)
        await page.wait_for_timeout(150)

        clicked = False
        try:
            send = page.locator(SEL_SEND).first
            await send.wait_for(state="visible", timeout=5_000)
            if await send.is_enabled():
                await send.click()
                clicked = True
        except Exception:
            clicked = False
        if not clicked:
            await page.keyboard.press("Enter")

    async def _upload_images(self, page, images: list[str]) -> None:
        inputs = page.locator('input[type="file"]')
        count = await inputs.count()
        if count == 0:
            raise BridgeError("页面上找不到图片上传入口（ChatGPT 界面可能改版了）")
        chosen = None
        best_score = -1
        fallback = None
        for index in range(count):
            try:
                accept = (await inputs.nth(index).get_attribute("accept")) or ""
                multiple = (await inputs.nth(index).get_attribute("multiple")) is not None
            except Exception:
                continue
            if "image" not in accept:
                continue
            fallback = fallback or inputs.nth(index)
            # 纯图片输入框最好用（image/*,video/* 那个会走另一套处理流程）
            score = 0
            if "video" not in accept:
                score += 2
            if multiple:
                score += 1
            if score > best_score:
                best_score = score
                chosen = inputs.nth(index)
        chosen = chosen or fallback or inputs.first
        await chosen.set_input_files(images)

    async def _wait_upload_ready(self, page, timeout: int = 120) -> None:
        """等附件上传完成；顺便把 ChatGPT 的报错弹窗立刻转成明确错误。"""
        started = time.time()
        while time.time() - started < timeout:
            state = await page.evaluate(UPLOAD_STATE_JS)
            alert = (state.get("alert") or "").strip()
            if alert:
                raise BridgeError(f"ChatGPT 拒绝这次请求：{alert}")
            if state.get("ready") and int(state.get("attachments", 0)) > 0:
                return
            await asyncio.sleep(0.4)
        raise BridgeError(
            "图片上传后发送按钮一直没有就绪（可能图片格式不支持，或 ChatGPT 界面又改版了）。"
        )

    async def _wait_started(self, page, before: int, timeout: int = 120) -> None:
        started = time.time()
        while time.time() - started < timeout:
            state = await self._state(page)
            if int(state.get("count", 0)) > before:
                return
            alert = (state.get("alert") or "").strip()
            if alert:
                raise BridgeError(f"ChatGPT 报错：{alert}")
            await asyncio.sleep(0.8)
        raise BridgeError(
            "消息好像没发出去（没有等到回答）。可以发「gpt状态」看看浏览器是否正常，"
            "或者发「gpt登录」手动检查页面。"
        )

    async def _wait_finished(self, page, timeout: int, before: int) -> None:
        started = time.time()
        last_len = -1
        stable = 0
        while time.time() - started < timeout:
            state = await self._state(page)
            if state.get("streaming"):
                stable = 0
                last_len = state.get("len", 0)
                await asyncio.sleep(1.0)
                continue
            length = int(state.get("len", 0))
            alert = (state.get("alert") or "").strip()
            if alert and length == 0:
                raise BridgeError(f"ChatGPT 报错：{alert}")
            if (
                length > 0
                and length == last_len
                and int(state.get("count", 0)) > before
            ):
                stable += 1
                if stable >= 3:
                    return
            else:
                stable = 0
            last_len = length
            await asyncio.sleep(1.0)
        raise AskTimeoutError(
            f"等了 {timeout} 秒还没生成完。可以在配置里调大 ask_timeout，"
            "或发「gpt状态」看浏览器是否卡住。"
        )

    async def _state(self, page) -> dict:
        try:
            return await page.evaluate(STATE_JS)
        except Exception:
            return {"streaming": False, "count": 0, "len": 0, "alert": ""}

    async def status(self, probe_key: str = "__status__") -> dict:
        await self._ensure_browser()
        assert self._lock is not None
        async with self._lock:
            page = self._pages.get(probe_key)
            if page is None or page.is_closed():
                page = await self._ctx.new_page()
                self._pages[probe_key] = page
                self._touch(probe_key)
                try:
                    await page.goto(CHATGPT_HOME, wait_until="domcontentloaded")
                except Exception as exc:
                    raise BridgeError(f"打不开 ChatGPT 首页：{str(exc)[:160]}") from exc
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            if any(hint in title.lower() for hint in CHALLENGE_HINTS):
                raise ChallengeRequiredError(
                    "ChatGPT 弹出了 Cloudflare 人机验证。发送「gpt登录」打开浏览器窗口手动过一次。"
                )
            info = await self._session_info(page)
            if not info.get("user"):
                return {
                    "logged_in": False,
                    "url": page.url,
                    "error": info.get("error", ""),
                    "conversations": max(0, len(self._pages) - 1),
                    "headless": self.headless,
                    "proxy": self._proxy or self.proxy,
                }
            account = info.get("account") or {}
            user = info.get("user") or {}
            return {
                "logged_in": True,
                "email": user.get("email", ""),
                "name": user.get("name", ""),
                "plan": account.get("planType", ""),
                "url": page.url,
                "conversations": max(0, len(self._pages) - 1),
                "headless": self.headless,
                "proxy": self._proxy or self.proxy,
            }

    async def _inline_images(self, page, markdown: str) -> str:
        """把回答里的远程图片下载成 data URI，渲染卡片时不再依赖外网/登录态。

        剪贴板里的网络配图有两种写法：`![alt](url)` 和 `[image](url)`（后者是
        网页里图片卡片的链接形式），两种都要还原成真正的图片。
        """
        text = markdown or ""
        budget = 12 * 1024 * 1024

        async def fetch(url: str) -> str:
            nonlocal budget
            if budget <= 0:
                return ""
            try:
                response = await self._ctx.request.get(url, timeout=30_000)
                if not response.ok:
                    self._log("debug", f"图片下载失败 {url[:80]}：HTTP {response.status}")
                    return ""
                ctype = (response.headers.get("content-type") or "").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    return ""
                body = await response.body()
                if not body or len(body) > 6 * 1024 * 1024 or len(body) > budget:
                    return ""
                budget -= len(body)
                return f"data:{ctype};base64,{base64.b64encode(body).decode()}"
            except Exception as exc:
                self._log("debug", f"图片下载失败 {url[:80]}：{exc}")
                return ""

        for alt, url in _MD_IMAGE_RE.findall(text)[:8]:
            uri = await fetch(url)
            if uri:
                text = text.replace(f"![{alt}]({url})", f"![]({uri})")

        for alt, url in _MD_IMAGE_LINK_RE.findall(text)[:12]:
            if not _IMAGE_URL_HINT.search(url):
                continue
            uri = await fetch(url)
            if uri:
                text = text.replace(f"[{alt}]({url})", f"![]({uri})")
        return text

    async def _read_clipboard(self, page) -> str:
        try:
            value = await page.evaluate(
                """async () => {
                    try { return await navigator.clipboard.readText(); }
                    catch (e) { return ''; }
                }"""
            )
            return value or ""
        except Exception:
            return ""

    async def _copy_last_answer(self, page) -> str:
        """点最后一条回答的「复制」按钮，读剪贴板；读完把原来的剪贴板内容写回去。"""
        try:
            await page.bring_to_front()
        except Exception:
            pass
        previous = await self._read_clipboard(page)
        clicked = await page.evaluate(
            """() => {
                const turns = [...document.querySelectorAll('[data-talvt-turn-state]')];
                const turn = turns[turns.length - 1];
                if (!turn) return false;
                const rows = turn.querySelectorAll('[class*="actionRow"]');
                const row = rows.length ? rows[rows.length - 1] : turn;
                const btn = [...row.querySelectorAll('button')].find(b =>
                    /复制|copy/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-testid') || '')));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not clicked:
            return ""
        await page.wait_for_timeout(500)
        text = await self._read_clipboard(page)
        if previous and text:
            try:
                await page.evaluate(
                    "(t) => { navigator.clipboard.writeText(t).catch(() => {}); }",
                    previous,
                )
            except Exception:
                pass
        return text or ""

    async def _get_render_page(self):
        if self._render_page is None or self._render_page.is_closed():
            self._render_page = await self._ctx.new_page()
        return self._render_page

    async def close_session(self, key: str) -> bool:
        page = self._pages.pop(key, None)
        if key in self._order:
            self._order.remove(key)
        if page is not None and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass
        return True
