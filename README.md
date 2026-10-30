# ChatGPT 网页版问答（AstrBot 插件）

把 QQ 里的文字和图片转发到**你自己电脑上已登录的 ChatGPT 网页版**，再把回答渲染成一张干净的图片发回 QQ。

不用 OpenAI API Key，不额外消耗额度，走的就是你平时手动用网页版的那份账号。

![示例](docs/example.jpg)

## 特性

- **图文一起问**：同一条消息里的图片和文字会作为**一条**消息发给 ChatGPT；先发图、后补文字也会自动合并成一次提问
- **会追问**：同一个 QQ 会话对应同一个 ChatGPT 对话，可以直接问“那为什么是负的？”
- **答案排版**：公式（KaTeX）、表格、代码块都会保留，渲染成长图发回；太长会自动缩放并按空白行切成多张
- **配图一起发**：回答里的网络图片会下载后嵌进图里，不会留下裂图
- **群聊不刷屏**：私聊直接答；群里默认要 @ 机器人或唤醒前缀
- **一处配置**：正常情况下只需要填一个代理（甚至可以不填）

## 它是怎么工作的

```
QQ ──消息──▶ AstrBot 插件 ──▶ 本机浏览器（Playwright 驱动，复用登录态）
                                   │
                               chatgpt.com
                                   │
QQ ◀──排版后的图片/文字── 插件 ◀────┘
```

**为什么不做 API 反代？** ChatGPT 网页接口要求 Cloudflare 的 `cf_clearance` 和动态 sentinel 签名，纯 HTTP 反代/重放很快就会失效；这里直接操作真实浏览器，网页改版、验证流程都和人手动用一样。

## 安装

1. 把整个目录放进 `AstrBot/data/plugins/astrbot_plugin_chatgpt_web/`（插件市场安装也可以）。
2. 依赖会在加载时自动安装；手动安装可以执行：
   ```bash
   # 路径换成你自己的 AstrBot 虚拟环境
   python -m pip install -r requirements.txt
   ```
   浏览器**不需要额外下载**：默认自动使用系统已装的 Edge / Chrome。
3. 在 AstrBot 面板里启用插件（或重载插件）。

## 三步用起来

1. **能打开 ChatGPT**：先在电脑浏览器里确认 https://chatgpt.com 能正常访问；访问不了就在插件配置里填 `proxy`（也可以不填，插件会自动读环境变量或系统代理）。
2. **登录一次**：QQ 里给机器人发 `gpt登录`，电脑上会弹出一个**插件专用**的浏览器窗口（不是你的日常浏览器），在里面登录 ChatGPT。
3. **开始用**：私聊直接发问题或图片；群里 @ 机器人说话。

登录态保存在 `data/plugin_data/astrbot_plugin_chatgpt_web/browser_profile/`，之后不用再登。浏览器窗口会常驻，**不要手动关掉**；要重启就发 `gpt重启`。

## 指令

| 指令 | 别名 | 作用 |
| --- | --- | --- |
| `gpt <问题>` | `问gpt` / `askgpt` | 显式提问（关掉被动模式后也能用） |
| `gpt状态` | `gptstatus` | 登录状态、代理、浏览器、进行中的对话数 |
| `gpt登录` | `gptlogin` / `gpt登陆` | 打开浏览器窗口登录 / 过人机验证 |
| `gpt新对话` | `gptnew` / `gpt新会话` | 给当前 QQ 会话开一个全新的 ChatGPT 对话 |
| `gpt重启` | `gptrestart` | 关掉浏览器，下次提问时重新启动 |
| `gpt帮助` | `gpthelp` | 显示指令说明 |

## 配置

装好后在 AstrBot 插件配置页里改，只有 `proxy` 可能是必填的。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `proxy` | 空 | 访问 ChatGPT 的代理。留空会尝试 `HTTPS_PROXY` 环境变量和 Windows 系统代理，再退回直连。示例：`http://127.0.0.1:7897` |
| `browser_channel` | `auto` | `auto` 自动检测 Edge/Chrome；也可写 `msedge`、`chrome`；留空用 Playwright 自带 Chromium |
| `passive` | `true` | 白名单会话里普通消息自动转发 |
| `passive_groups` | `false` | 群里不 @ 也触发（容易刷屏，按需打开） |
| `allowed_sessions` | `[]` | 允许的会话 UMO / 群号 / QQ 号，空 = 全部 |
| `ignore_prefixes` | `/ # gpt 找书 …` | 被动模式下跳过的指令前缀，避免把别的插件指令也转发 |
| `reply_mode` | `auto` | `auto` 公式/代码/长答案发图；`text` 只发文字；`image` 尽量发图 |
| `headless` | `false` | 无界面运行。**不建议开**：Cloudflare 会直接拦无头浏览器 |
| `ask_timeout` | `300` | 单次等待上限（秒） |
| `ask_interval` | `3` | 两次请求的最小间隔（秒），降低风控概率 |
| `hint_after` | `30` | 等这么久还没结果就先发条提示 |
| `max_sessions` | `4` | 同时保留的 ChatGPT 对话页数量 |
| `max_images` | `4` | 一条消息最多转发几张图 |
| `image_wait_seconds` | `8` | 只发图时先等几秒，等你补文字后合并成一次提问；0 = 不等待 |
| `max_image_height` | `8000` | 答案卡片最大高度（CSS 像素），太长会先缩小，仍超就改发文字 |
| `image_prompt` | … | 只发图、没补文字时用的默认提示词 |
| `text_chunk` | `1500` | 文字答案每条的最大字数 |
| `browser_locale` / `browser_timezone` | 空 | 高级项，默认跟随系统 |

## 常见问题

**一直提示 Cloudflare 人机验证**
`headless` 保持关闭，发 `gpt登录`，在弹出的窗口里点一下验证；过完不要关窗口。

**打不开 chatgpt.com / 等待超时**
先在普通浏览器里确认能打开。Clash 类工具通常有单独的 ChatGPT 代理组，选带 `-GPT` 的节点；能上 Google 不代表能上 ChatGPT。

**提示找不到可用浏览器**
装 Edge 或 Chrome，或者执行 `python -m playwright install chromium` 后用 `browser_channel: ""`。

**提示资料目录被占用**
插件用的浏览器窗口还开着（或上次没退干净）。关掉那个窗口，或发 `gpt重启`。

**回答很慢 / 超时**
网页版推理模型本身慢，调大 `ask_timeout`；真的卡住就 `gpt重启`。

**出现 “Unusual activity has been detected”**
这是 ChatGPT 的临时风控，通常和短时间内大量自动提问有关。等一会儿再用；正常聊天频率不会触发，也可以把 `ask_interval` 调大。

**群里不回复**
默认要 @ 机器人（或用唤醒前缀）。想让它对所有群消息生效，把 `passive_groups` 设为 `true`。

**图片位置显示「[图片加载失败]」**
回答里的图片链接过期或下载失败，文字部分不受影响。

**想换账号**
发 `gpt重启` 后删掉 `data/plugin_data/astrbot_plugin_chatgpt_web/browser_profile/`，再发 `gpt登录` 重新登。

## 隐私与风险

- 登录 Cookie 保存在本机插件数据目录，不要同步或分享这个目录。
- 插件会短暂读取系统剪贴板（点击网页的“复制”按钮取答案），**读完会把原内容写回**。
- 你发的文字和图片会发送给 OpenAI，和手动使用网页版一样。
- 用脚本操作网页可能违反 ChatGPT 的服务条款，账号风险自负，建议先用小号试用。

## 开发与测试

不需要登录也能跑：

```bash
python tests/test_extract.py      # 网页 HTML → Markdown / LaTeX
python tests/test_normalize.py    # 剪贴板 Markdown 规范化
python tests/test_render.py       # Markdown → 卡片图片
```

需要登录态 / AstrBot 源码的：

```bash
set CHATGPT_WEB_PROFILE=D:\path\to\browser_profile
set CHATGPT_WEB_PROXY=http://127.0.0.1:7897
set ASTRBOT_HOME=D:\path\to\AstrBot

python tests/smoke_bridge.py
python tests/ask_once.py "1+1 等于几"
python tests/test_merge.py         # 图片+文字合并逻辑
```

目录：

```
main.py       AstrBot 插件入口（指令、被动模式、消息发送）
bridge.py     Playwright 浏览器桥（登录、提问、抽取答案、取图）
extract.py    网页 HTML → Markdown，剪贴板 Markdown 规范化
render.py     Markdown → 长图（本地 KaTeX）
assets/katex  本地 KaTeX 资源
```

## English quick start

1. Drop this folder into `AstrBot/data/plugins/`.
2. Make sure `chatgpt.com` is reachable (set `proxy` if needed; system proxy is auto-detected).
3. Send `gpt登录` to the bot, log in in the window that opens.
4. Send a question or an image in QQ.

Commands: `gpt <question>`, `gptstatus`, `gptlogin`, `gptnew`, `gptrestart`, `gpthelp`.
Headful mode is required (Cloudflare blocks headless).

## License

[MIT](LICENSE)
