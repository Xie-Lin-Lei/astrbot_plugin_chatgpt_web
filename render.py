"""把答案 Markdown 渲染成干净的长图。

ChatGPT 网页本身是虚拟滚动 + 悬浮输入框，直接截它的消息元素会拍到空白和输入框；
这里把抽出来的 Markdown 重新渲染成一个独立 HTML 卡片，用本地 KaTeX 排版公式，
再用同一个浏览器截图。输出不依赖 ChatGPT 的界面样式，长短都可控。
"""

from __future__ import annotations

import html
import re
import time
import uuid
from pathlib import Path

from markdown_it import MarkdownIt

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
KATEX_DIR = ASSETS_DIR / "katex"
RENDER_DIR = ASSETS_DIR / "render"

SCALE = 2
CARD_WIDTH = 900
SLICE_HEIGHT = 5000  # 像素（已含 SCALE），超过就切图
SLICE_OVERLAP = 120

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)
_TOKEN_RE = re.compile("\x02M(\\d+)\x02")

_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #ffffff; }
body {
  color: #1f2328;
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
#card { width: 900px; padding: 44px 46px 50px; background: #ffffff; }
#card > :first-child { margin-top: 0; }
#card > :last-child { margin-bottom: 0; }
p, li { font-size: 17px; line-height: 1.78; }
p { margin: 0 0 13px; }
h1 { font-size: 26px; margin: 22px 0 12px; }
h2 { font-size: 23px; margin: 22px 0 12px; }
h3 { font-size: 20px; margin: 20px 0 10px; }
h4, h5, h6 { font-size: 18px; margin: 18px 0 8px; }
strong { font-weight: 700; }
ul, ol { margin: 6px 0 14px; padding-left: 1.6em; }
li { margin: 3px 0; }
li > p { margin: 0; }
table { border-collapse: collapse; margin: 12px 0 16px; font-size: 15.5px; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
pre {
  background: #f6f8fa; border-radius: 8px; padding: 12px 14px; margin: 12px 0 16px;
  overflow-x: auto; font-size: 14px; line-height: 1.55;
}
code { font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 0.92em; }
:not(pre) > code { background: #eff1f3; padding: 2px 5px; border-radius: 4px; }
blockquote { border-left: 4px solid #d0d7de; margin: 12px 0; padding: 2px 14px; color: #57606a; }
hr { border: 0; border-top: 1px solid #d8dee4; margin: 20px 0; }
img { max-width: 100%; height: auto; border-radius: 6px; margin: 8px 0; }
a { color: #0969da; text-decoration: none; }
.math-display { display: block; text-align: center; margin: 16px 0; }
.img-note { color: #57606a; background: #f0f2f4; border-radius: 4px; padding: 1px 6px; font-size: 15px; }
.katex-display { margin: 0; }
.katex { font-size: 1.05em; }
"""

_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="../katex/katex.min.css">
<style>{css}</style>
</head>
<body>
<div id="card">{body}</div>
<script src="../katex/katex.min.js"></script>
<script>
(function () {{
  var nodes = document.querySelectorAll('.math, .math-display');
  for (var i = 0; i < nodes.length; i++) {{
    var el = nodes[i];
    var tex = el.getAttribute('data-tex') || '';
    var display = el.getAttribute('data-display') === '1';
    if (window.katex) {{
      try {{
        katex.render(tex, el, {{displayMode: display, throwOnError: false, strict: false}});
        continue;
      }} catch (e) {{}}
    }}
    el.textContent = (display ? '$$' : '$') + tex + (display ? '$$' : '$');
  }}
  var done = function () {{ window.__ready = true; }};
  var images = [].slice.call(document.images);
  var pending = images.map(function (img) {{
    if (img.complete) return Promise.resolve();
    return new Promise(function (resolve) {{
      img.addEventListener('load', resolve, {{once: true}});
      img.addEventListener('error', function () {{
        var note = document.createElement('span');
        note.className = 'img-note';
        note.textContent = '[图片加载失败]';
        if (img.parentNode) img.parentNode.replaceChild(note, img);
        resolve();
      }}, {{once: true}});
    }});
  }});
  Promise.all(pending).then(function () {{
    if (document.fonts && document.fonts.ready) {{ document.fonts.ready.then(done); }} else {{ done(); }}
  }});
}})();
</script>
</body>
</html>
"""


def _split_math(markdown: str) -> tuple[str, list[tuple[str, bool]]]:
    maths: list[tuple[str, bool]] = []

    def repl(match: re.Match) -> str:
        display = match.group(1) is not None
        tex = (match.group(1) if display else match.group(2) or "").strip()
        if not tex:
            return match.group(0)
        maths.append((tex, display))
        return f"\x02M{len(maths) - 1}\x02"

    return _MATH_RE.sub(repl, markdown), maths


def _inline_math(html_text: str, maths: list[tuple[str, bool]]) -> str:
    def repl(match: re.Match) -> str:
        try:
            tex, display = maths[int(match.group(1))]
        except (IndexError, ValueError):
            return ""
        css = "math-display" if display else "math"
        return (
            f'<span class="{css}" data-tex="{html.escape(tex, quote=True)}"'
            f' data-display="{1 if display else 0}"></span>'
        )

    return _TOKEN_RE.sub(repl, html_text)


def build_html(markdown: str) -> str:
    text, maths = _split_math(markdown or "")
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable("table")
    md.enable("strikethrough")
    body = _inline_math(md.render(text), maths)
    return _TEMPLATE.format(css=_CSS, body=body)


def _blank_row(gray, target: int, width: int, limit: int = 500) -> int:
    """从 target 往上找一条整行接近纯白的横线，避免把文字从中间切断。"""
    lower = max(1, target - limit)
    for y in range(min(target, gray.height - 1), lower, -1):
        row_ok = True
        for x in range(0, width, 6):
            if gray.getpixel((x, y)) < 248:
                row_ok = False
                break
        if row_ok:
            return y
    return target


def _split_png(path: Path) -> list[str]:
    from PIL import Image

    img = Image.open(path)
    if img.height <= SLICE_HEIGHT + SLICE_OVERLAP:
        return [str(path)]
    gray = img.convert("L")
    out: list[str] = []
    top = 0
    index = 1
    while top < img.height:
        target = min(img.height, top + SLICE_HEIGHT)
        bottom = target if target >= img.height else _blank_row(gray, target, img.width)
        piece = img.crop((0, top, img.width, bottom))
        target_path = path.with_name(f"{path.stem}-{index}.png")
        piece.save(target_path)
        out.append(str(target_path))
        index += 1
        if bottom >= img.height:
            break
        top = bottom + 2
    try:
        path.unlink()
    except OSError:
        pass
    return out


async def render_to_images(
    page,
    markdown: str,
    out_dir: Path,
    *,
    max_height: int = 7000,
) -> list[str]:
    """在给定 Playwright 页面上渲染，返回图片路径列表（可能为空）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    source = RENDER_DIR / f"card-{uuid.uuid4().hex}.html"
    source.write_text(build_html(markdown), encoding="utf-8")
    png = out_dir / f"answer-{int(time.time() * 1000)}.png"
    try:
        try:
            await page.set_viewport_size({"width": CARD_WIDTH + 60, "height": 1400})
        except Exception:
            pass
        await page.goto(source.as_uri(), wait_until="load")
        await page.wait_for_function(
            "() => window.__ready === true || document.readyState === 'complete'",
            timeout=30_000,
        )
        await page.wait_for_timeout(200)
        card = page.locator("#card")
        box = await card.bounding_box()
        if not box or box.get("height", 0) < 10:
            return []
        if box["height"] > max_height:
            # 太久的内容整体缩小一点，保证还能一张长图截下来
            zoom = max(0.5, (max_height - 200) / box["height"])
            await page.evaluate("(k) => { document.documentElement.style.zoom = k; }", zoom)
            await page.wait_for_timeout(250)
            box = await card.bounding_box()
        if not box or box.get("height", 0) < 10:
            return []
        if box["height"] > 8000:
            return []
        await card.screenshot(path=str(png), animations="disabled", type="png")
        return _split_png(png)
    except Exception:
        try:
            png.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        try:
            source.unlink(missing_ok=True)
        except OSError:
            pass
