"""AstrBot ChatGPT 网页版问答插件

把你自己的电脑当成「中转站」：QQ 里发来的文字/图片会交给本机浏览器里已经登录的
ChatGPT 网页版去问，答案再发回 QQ。默认被动模式，白名单会话里普通消息直接转发。

指令：
    gpt <问题>      显式提问（被动模式关闭时也能用）
    gpt登录          打开浏览器窗口，手动登录 ChatGPT / 过人机验证
    gpt状态          查看登录状态、当前会话数、浏览器配置
    gpt新对话        给当前 QQ 会话开一个全新的 ChatGPT 对话
    gpt重启          关掉浏览器，下次提问时重新启动
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr

from .bridge import (
    AskTimeoutError,
    BridgeError,
    BrowserWorker,
    ChallengeRequiredError,
    ChatGPTBridge,
    NotLoggedInError,
)
from .extract import split_message

PLUGIN_NAME = "astrbot_plugin_chatgpt_web"
ANSWER_KEEP_DAYS = 7
DEFAULT_IMAGE_PROMPT = "请看我发的图片，把里面的题目讲清楚：给出思路、推导过程和结论。"


class ChatGPTWebPlugin(Star):
    """把 QQ 消息转发到本机 ChatGPT 网页版。"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        except Exception:
            data_dir = Path("data") / "plugin_data" / PLUGIN_NAME
        self._data_dir = Path(data_dir)
        self._image_dir = self._data_dir / "answers"
        self._profile_dir = self._data_dir / "browser_profile"
        self._image_dir.mkdir(parents=True, exist_ok=True)

        self._worker = BrowserWorker()
        self._bridge: ChatGPTBridge | None = None
        self._bridge_key: tuple | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending_images: dict[str, dict] = {}
        self._cleanup_images()

    # ---------- 桥接 ----------

    def _bridge_config_key(self) -> tuple:
        return (
            str(self.config.get("browser_channel") or "auto"),
            str(self.config.get("proxy") or ""),
            bool(self.config.get("headless", False)),
            max(1, int(self.config.get("max_sessions") or 4)),
            max(600, int(self.config.get("max_image_height") or 8000)),
            max(0, int(self.config.get("ask_interval") or 3)),
            str(self.config.get("browser_locale") or ""),
            str(self.config.get("browser_timezone") or ""),
        )

    async def _ensure_bridge(self) -> ChatGPTBridge:
        key = self._bridge_config_key()
        if self._bridge is not None and self._bridge_key == key:
            return self._bridge
        if self._bridge is not None:
            old = self._bridge
            self._bridge = None
            try:
                await self._call_bridge(old.close())
            except Exception as exc:
                logger.debug(f"[{PLUGIN_NAME}] 关闭旧浏览器失败: {exc}")
        (
            channel,
            proxy,
            headless,
            max_sessions,
            max_image_height,
            ask_interval,
            locale,
            timezone_id,
        ) = key
        self._bridge = ChatGPTBridge(
            self._profile_dir,
            channel=channel,
            proxy=proxy,
            headless=headless,
            timeout=max(30, int(self.config.get("ask_timeout") or 300)),
            max_sessions=max_sessions,
            max_image_height=max_image_height,
            min_interval=ask_interval,
            locale=locale,
            timezone_id=timezone_id,
            log=lambda level, msg: getattr(logger, level, logger.info)(
                f"[{PLUGIN_NAME}] {msg}"
            ),
        )
        self._bridge_key = key
        return self._bridge

    async def _call_bridge(self, coro):
        future = self._worker.submit(coro)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

    def _cleanup_images(self):
        try:
            cutoff = time.time() - ANSWER_KEEP_DAYS * 86400
            for path in self._image_dir.glob("answer-*.png"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass

    # ---------- 消息辅助 ----------

    def _ignore_prefixes(self) -> list[str]:
        raw = self.config.get("ignore_prefixes")
        if raw is None:
            raw = ["/", "#", "gpt", "问gpt", "找书", "选书", "以图找书"]
        if isinstance(raw, str):
            raw = raw.split(",")
        return [str(item).strip() for item in raw if str(item).strip()]

    def _looks_like_command(self, text: str) -> bool:
        stripped = text.lstrip()
        return any(stripped.startswith(prefix) for prefix in self._ignore_prefixes())

    def _session_allowed(self, event: AstrMessageEvent) -> bool:
        allowed = self.config.get("allowed_sessions") or []
        if isinstance(allowed, str):
            allowed = allowed.split(",")
        allowed = [str(item).strip() for item in allowed if str(item).strip()]
        if not allowed:
            return True
        umo = event.unified_msg_origin or ""
        group_id = str(event.get_group_id() or "")
        sender_id = str(event.get_sender_id() or "")
        for item in allowed:
            if item in (umo, group_id, sender_id):
                return True
            if item in umo:
                return True
        return False

    async def _collect_images(self, event: AstrMessageEvent) -> list[str]:
        limit = max(1, int(self.config.get("max_images") or 4))
        paths: list[str] = []
        for segment in event.get_messages() or []:
            if len(paths) >= limit:
                break
            if not isinstance(segment, Comp.Image):
                continue
            try:
                path = await segment.convert_to_file_path()
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 图片转换失败: {exc}")
                continue
            if path:
                paths.append(str(path))
        return paths

    async def _safe_send(self, event: AstrMessageEvent, text: str):
        try:
            await event.send(MessageChain(chain=[Comp.Plain(text)]))
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 发送消息失败: {exc}")

    async def _deliver(
        self,
        event: AstrMessageEvent,
        markdown: str,
        image_paths: list[str],
    ):
        mode = str(self.config.get("reply_mode") or "auto").strip().lower()
        use_image = False
        if mode == "image":
            use_image = bool(image_paths)
        elif mode == "auto" and image_paths:
            rich = "$" in markdown or "```" in markdown or "\n|" in markdown
            use_image = rich or len(markdown) > 400
        if use_image:
            try:
                for index, path in enumerate(image_paths):
                    await event.send(
                        MessageChain(chain=[Comp.Image.fromFileSystem(path)])
                    )
                    if index + 1 < len(image_paths):
                        await asyncio.sleep(0.4)
                return
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 发送图片失败，改发文字: {exc}")
        chunks = split_message(markdown, int(self.config.get("text_chunk") or 1500))
        for index, chunk in enumerate(chunks):
            await event.send(MessageChain(chain=[Comp.Plain(chunk)]))
            if index + 1 < len(chunks):
                await asyncio.sleep(0.4)

    def _take_pending_images(self, key: str) -> list[str]:
        item = self._pending_images.pop(key, None)
        if not item:
            return []
        task = item.get("task")
        if task is not None and not task.done():
            task.cancel()
        return list(item.get("images") or [])

    def _clear_pending_images(self) -> None:
        for key in list(self._pending_images):
            self._take_pending_images(key)

    async def _flush_pending_images(self, key: str, delay: int) -> None:
        try:
            await asyncio.sleep(max(1, delay))
        except asyncio.CancelledError:
            return
        item = self._pending_images.pop(key, None)
        if not item:
            return
        logger.info(f"[{PLUGIN_NAME}] 没有等到补充文字，按默认提示词发送图片")
        await self._answer(item["event"], "", images=item["images"])

    async def _hint_later(self, event: AstrMessageEvent, done: asyncio.Event):
        delay = max(5, int(self.config.get("hint_after") or 30))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if done.is_set():
            return
        await self._safe_send(event, "⏳ 还在等 ChatGPT 生成，快好了…")

    # ---------- 核心流程 ----------

    async def _answer(
        self,
        event: AstrMessageEvent,
        prompt: str,
        *,
        new_chat: bool = False,
        images: list[str] | None = None,
    ) -> None:
        key = event.unified_msg_origin
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            await self._safe_send(event, "前面还有一条正在等 ChatGPT，排一下队…")
        async with lock:
            if images is None:
                images = await self._collect_images(event)
            prompt = (prompt or "").strip()
            logger.info(
                f"[{PLUGIN_NAME}] 开始提问 {key}：{len(prompt)} 字，{len(images)} 张图"
            )
            if not prompt and not images:
                return
            if not prompt:
                prompt = str(self.config.get("image_prompt") or DEFAULT_IMAGE_PROMPT)

            try:
                bridge = await self._ensure_bridge()
            except BridgeError as exc:
                await self._safe_send(event, f"❌ {exc}")
                return

            mode = str(self.config.get("reply_mode") or "auto").strip().lower()
            done = asyncio.Event()
            hint_task = asyncio.create_task(self._hint_later(event, done))
            started = time.time()
            try:
                result = await self._call_bridge(
                    bridge.ask(
                        key,
                        prompt,
                        images,
                        new_chat=new_chat,
                        want_image=mode != "text",
                        image_out_dir=self._image_dir,
                        timeout=max(30, int(self.config.get("ask_timeout") or 300)),
                    )
                )
            except NotLoggedInError as exc:
                await self._safe_send(event, f"🔑 {exc}")
                return
            except ChallengeRequiredError as exc:
                await self._safe_send(event, f"🛡️ {exc}")
                return
            except AskTimeoutError as exc:
                await self._safe_send(event, f"⏰ {exc}")
                return
            except BridgeError as exc:
                await self._safe_send(event, f"❌ {exc}")
                return
            except Exception as exc:
                logger.error(f"[{PLUGIN_NAME}] 提问失败: {exc}", exc_info=True)
                await self._safe_send(event, f"❌ 调用 ChatGPT 网页版出错：{exc}")
                return
            finally:
                done.set()
                hint_task.cancel()

            markdown = (result.get("markdown") or "").strip()
            if not markdown:
                markdown = (result.get("text") or "").strip()
            if not markdown:
                await self._safe_send(event, "ChatGPT 这次没有返回文字内容。")
                return

            image_paths = result.get("image_paths") or []
            logger.info(
                f"[{PLUGIN_NAME}] 回答完成，用时 {time.time() - started:.1f}s，"
                f"{len(markdown)} 字，图片 {len(image_paths)} 张"
            )
            await self._deliver(event, markdown, image_paths)

    # ---------- 事件 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """被动模式：白名单会话里的普通消息直接转给 ChatGPT。"""
        if not bool(self.config.get("passive", True)):
            return
        if not self._session_allowed(event):
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        if not event.is_private_chat():
            # 群聊里默认要 @ 机器人（或用唤醒前缀），避免对每条群消息都回 ChatGPT
            if not bool(self.config.get("passive_groups", False)) and not event.is_at_or_wake_command:
                return
        text = (event.get_message_str() or "").strip()
        if text and self._looks_like_command(text):
            return
        images = await self._collect_images(event)
        if not text and not images:
            return
        if text and not images and len(text) > 2000:
            text = text[:2000]

        key = event.unified_msg_origin
        pending = self._take_pending_images(key)
        if pending:
            # 上一条是纯图片消息，用户紧接着补了文字：合并成一次提问
            images = pending + images

        wait = max(0, int(self.config.get("image_wait_seconds") or 0))
        if images and not text and wait > 0:
            task = asyncio.create_task(self._flush_pending_images(key, wait))
            self._pending_images[key] = {
                "event": event,
                "images": images,
                "task": task,
            }
            logger.info(
                f"[{PLUGIN_NAME}] 收到 {len(images)} 张图片，等待 {wait} 秒看是否有补充文字…"
            )
            return

        logger.info(
            f"[{PLUGIN_NAME}] 被动转发 {key}：文字 {len(text)} 字，图片 {len(images)} 张"
        )
        await self._answer(event, text, images=images)

    @filter.command("gpt帮助", alias={"gpthelp"})
    async def help_command(self, event: AstrMessageEvent):
        """gpt帮助：列出所有指令"""
        await self._safe_send(
            event,
            "\n".join(
                [
                    "🤖 ChatGPT 网页版问答",
                    "· 直接发文字或图片（私聊，或群里 @ 机器人）",
                    "· gpt <问题>：显式提问",
                    "· gpt状态：登录与浏览器状态",
                    "· gpt登录：打开浏览器窗口登录 / 过人机验证",
                    "· gpt新对话：为当前会话开一个新对话",
                    "· gpt重启：重启浏览器",
                    "· gpt帮助：显示这条说明",
                ]
            ),
        )

    @filter.command("gpt", alias={"问gpt", "askgpt"})
    async def ask_gpt(self, event: AstrMessageEvent, prompt: GreedyStr):
        """gpt <问题>：把问题交给本机的 ChatGPT 网页版"""
        if not self._session_allowed(event):
            await self._safe_send(event, "这个会话没有在插件白名单里。")
            return
        pending = self._take_pending_images(event.unified_msg_origin)
        images = None
        if pending:
            images = pending + await self._collect_images(event)
        await self._answer(event, str(prompt), images=images)

    @filter.command("gpt登录", alias={"gpt登陆", "gptlogin"})
    async def login(self, event: AstrMessageEvent):
        """gpt登录：打开浏览器窗口，手动登录 ChatGPT 或完成人机验证"""
        await self._safe_send(
            event,
            "正在打开浏览器窗口，请到电脑上登录 ChatGPT（或完成人机验证），"
            "完成后发「gpt状态」确认。",
        )
        try:
            bridge = await self._ensure_bridge()
            await self._call_bridge(bridge.login())
        except BridgeError as exc:
            await self._safe_send(event, f"❌ {exc}")
        except Exception as exc:
            logger.error(f"[{PLUGIN_NAME}] 打开登录页失败: {exc}", exc_info=True)
            await self._safe_send(event, f"❌ 打开浏览器失败：{exc}")
            return
        await self._safe_send(
            event,
            "浏览器已打开 https://chatgpt.com/ 。登录完成（能正常看到输入框）后，"
            "发「gpt状态」确认；登录态会保存在插件数据目录里，下次不用再登。",
        )

    @filter.command("gpt状态", alias={"gptstatus"})
    async def status(self, event: AstrMessageEvent):
        """gpt状态：查看 ChatGPT 网页版的登录状态"""
        try:
            bridge = await self._ensure_bridge()
            info = await self._call_bridge(bridge.status())
        except BridgeError as exc:
            await self._safe_send(event, f"❌ {exc}")
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_NAME}] 状态检查失败: {exc}", exc_info=True)
            await self._safe_send(event, f"❌ 状态检查失败：{exc}")
            return

        lines = ["🩺 ChatGPT 网页版状态", ""]
        if info.get("logged_in"):
            lines.append("登录：✅ 已登录")
            if info.get("email"):
                lines.append(f"账号：{info['email']}")
            if info.get("plan"):
                lines.append(f"套餐：{info['plan']}")
        else:
            lines.append("登录：❌ 未登录")
            if info.get("error"):
                lines.append(f"会话接口：{info['error'][:80]}")
            lines.append("发送「gpt登录」打开浏览器登录。")
        channel = self.config.get("browser_channel") or "auto"
        channel_text = "自动" if str(channel).lower() == "auto" else channel
        lines.append(
            f"浏览器：{info.get('headless') and '无界面' or '可见窗口'}，{channel_text}"
        )
        proxy = str(self.config.get("proxy") or "")
        lines.append(f"代理：{proxy or '直连'}")
        lines.append(f"进行中的对话页：{info.get('conversations', 0)}")
        lines.append(f"被动模式：{'开' if self.config.get('passive', True) else '关'}")
        await self._safe_send(event, "\n".join(lines))

    @filter.command("gpt新对话", alias={"gpt新会话", "gptnew"})
    async def new_chat(self, event: AstrMessageEvent):
        """gpt新对话：当前 QQ 会话下一次提问时开一个全新的 ChatGPT 对话"""
        key = event.unified_msg_origin
        self._take_pending_images(key)
        bridge = self._bridge
        if bridge is not None:
            try:
                await self._call_bridge(bridge.close_session(key))
            except Exception as exc:
                logger.debug(f"[{PLUGIN_NAME}] 关闭对话页失败: {exc}")
        await self._safe_send(event, "🆕 已重置，下一条问题会开始新的对话。")

    @filter.command("gpt重启", alias={"gptrestart"})
    async def restart(self, event: AstrMessageEvent):
        """gpt重启：关闭浏览器，下次提问时重新启动"""
        bridge = self._bridge
        if bridge is not None:
            try:
                await self._call_bridge(bridge.close())
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 关闭浏览器失败: {exc}")
        self._bridge = None
        self._bridge_key = None
        self._locks.clear()
        self._clear_pending_images()
        await self._safe_send(event, "♻️ 浏览器已关闭，下次提问会自动重新启动。")

    async def terminate(self):
        if self._bridge is not None:
            try:
                await asyncio.wait_for(self._call_bridge(self._bridge.close()), timeout=20)
            except Exception:
                pass
            self._bridge = None
        self._worker.shutdown()
