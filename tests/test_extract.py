"""离线自测：用模拟的 ChatGPT 消息 HTML 验证抽出的 Markdown 是否正常。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extract import html_to_markdown, split_message  # noqa: E402

# 浏览器端（bridge.EXTRACT_JS）会把公式换成占位符，这里模拟它的输出
HTML = """
<div class="markdown prose">
<p>因为两个管子的集电极电流变化方向相反，所以：</p>
<span>\x02MATH0\x02</span>
<h3>1. 先看 T1 的电流</h3>
<p>当输入电压 \x02MATH1\x02 增大时：</p>
<ul>
<li>集电极电流增大</li>
<li>集电极电压减小</li>
</ul>
<table>
<thead><tr><th>参数</th><th>数值</th></tr></thead>
<tbody><tr><td>增益</td><td>-100</td></tr></tbody>
</table>
<pre><div class="code"><code class="language-python">print("hi")
</code></div></pre>
<button data-testid="copy-turn-action-button">复制</button>
<p><img src="data:image/png;base64,AAAA" alt=""></p>
</div>
"""

MATHS = [
    {"tex": "A_v = -\\frac{\\beta R_c}{r_{be}}", "display": True},
    {"tex": "\\Delta v_{id}", "display": False},
]


def main() -> None:
    out = html_to_markdown(HTML, MATHS)
    print(out)
    print("=" * 40)
    assert "$$" in out and "A_v = -\\frac{\\beta R_c}{r_{be}}" in out, "display math lost"
    assert "$\\Delta v_{id}$" in out, "inline math lost"
    assert "### 1. 先看 T1 的电流" in out, "heading lost"
    assert "- 集电极电流增大" in out, "list lost"
    assert "| 参数 | 数值 |" in out, "table lost"
    assert "```python" in out and 'print("hi")' in out, "code block lost"
    assert "复制" not in out, "button leaked"
    assert "data:image" not in out and "[图片]" in out, "data image not cleaned"
    chunks = split_message("x" * 4000, 1500)
    assert all(len(c) <= 1500 for c in chunks) and len(chunks) == 3, "split broken"
    print("OK")


if __name__ == "__main__":
    main()
