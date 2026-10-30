"""把 ChatGPT 网页里的答案转成 Markdown。

网页里的公式是 KaTeX 渲染出来的，直接取 innerText 会丢掉公式（MathML 对
innerText 不可见），所以在浏览器端先用占位符把每个公式替换成 token 并带上
TeX 源码，这里再把 token 还原成 $...$ / $$...$$。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

# 浏览器端使用的占位符，见 bridge.EXTRACT_JS
_MATH_TOKEN = re.compile("\x02MATH(\\d+)\x02")
_DATA_IMAGE = re.compile(r"!\[[^\]]*\]\(data:[^)]*\)")
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\((?:blob:)?[^)]*\)")
_MANY_BLANK = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_COPIED_DISPLAY = re.compile(r"\\\\\[(.+?)\\\\\]", re.S)
_COPIED_INLINE = re.compile(r"\\\\\((.+?)\\\\\)", re.S)


def _unescape_tex(tex: str) -> str:
    """还原剪贴板里被 Markdown 转义的 LaTeX 字符。"""
    return (
        tex.replace("\\_", "_")
        .replace("\\*", "*")
        .replace("\\#", "#")
        .strip()
    )


def normalize_copied_markdown(text: str) -> str:
    """ChatGPT「复制」出来的是 Markdown，公式写成 \\\\(...\\\\) / \\\\[...\\\\]。

    这里把公式换成本项目统一的 $...$ / $$...$$，其余 Markdown（代码块、表格）保持原样。
    """
    text = (text or "").replace("\r\n", "\n")

    def display(match: re.Match) -> str:
        return "\n\n$$\n" + _unescape_tex(match.group(1)) + "\n$$\n\n"

    def inline(match: re.Match) -> str:
        return "$" + _unescape_tex(match.group(1)) + "$"

    text = _COPIED_DISPLAY.sub(display, text)
    text = _COPIED_INLINE.sub(inline, text)
    text = _MANY_BLANK.sub("\n\n", text)
    return text.strip()


def _code_language(el) -> str:
    """从 <pre><code class="language-python"> 里取语言名。"""
    code = el.find("code")
    if code is None:
        return ""
    for cls in code.get("class") or []:
        if cls.startswith("language-"):
            return cls[len("language-") :]
    return ""


def _restore_math(markdown: str, maths: list[dict]) -> str:
    def repl(match: re.Match) -> str:
        try:
            item = maths[int(match.group(1))]
        except (IndexError, ValueError):
            return ""
        tex = (item.get("tex") or "").strip()
        if not tex:
            return ""
        if item.get("display"):
            return f"\n\n$$\n{tex}\n$$\n\n"
        return f"${tex}$"

    return _MATH_TOKEN.sub(repl, markdown)


def clean_markdown(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n")
    text = _DATA_IMAGE.sub("[图片]", text)
    # ChatGPT 生成的图片是 blob:/https: 链接，转成文字提示即可
    text = re.sub(r"!\[[^\]]*\]\(blob:[^)]*\)", "[图片]", text)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MANY_BLANK.sub("\n\n", text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def html_to_markdown(html: str, maths: list[dict] | None = None) -> str:
    """Convert the assistant message HTML into Markdown + LaTeX."""
    maths = maths or []
    soup = BeautifulSoup(html or "", "html.parser")
    # 按钮、图标里的文字（比如「复制」）不属于答案
    for tag in soup.find_all(["button", "svg", "style", "script", "noscript"]):
        tag.decompose()
    markdown = _markdownify(
        str(soup),
        heading_style="ATX",
        bullets="-",
        code_language_callback=_code_language,
    )
    markdown = _restore_math(markdown, maths)
    return clean_markdown(markdown)


def split_message(text: str, limit: int = 1500) -> list[str]:
    """按段落把长文本切成不超过 limit 的若干段。"""
    limit = max(200, int(limit))
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n"):
        candidate = f"{current}\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        # 单行就超长时按字符硬切
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]
