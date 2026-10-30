"""离线测试：把一段 Markdown（含公式/表格/代码）渲染成图片。"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge import detect_browser_channels  # noqa: E402
from render import render_to_images  # noqa: E402

SAMPLE = r"""
## 一、问题

差分放大电路里，**单端输出**的电压放大倍数为什么是负的？

## 二、推导

输入电压 $v_{id}$ 增大时，T1 的集电极电流增大：

$$
i_{c1} = \frac{v_{id}}{2 r_{be}}
$$

因此输出电压

$$v_{o1} = -i_{c1} R_C = -\frac{\beta R_C}{2 r_{be}} v_{id}$$

所以

$$
A_v = \frac{v_{o1}}{v_{id}} = -\frac{\beta R_C}{2 r_{be}}
$$

| 项目 | 数值 |
| --- | --- |
| $V_B$ | 4 V |
| $I_C$ | 2.63 mA |

```python
def gain(beta, rc, rbe):
    return -beta * rc / (2 * rbe)
```

> 负号只表示反相，不代表增益变小。
"""


async def main() -> None:
    out = Path(__file__).resolve().parent / ".ask-out"
    async with async_playwright() as pw:
        browser = None
        for channel in detect_browser_channels() or [None]:
            try:
                browser = await pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise SystemExit("没有可用浏览器：装 Edge/Chrome，或执行 playwright install chromium")
        ctx = await browser.new_context(device_scale_factor=2, viewport={"width": 960, "height": 1200})
        page = await ctx.new_page()
        paths = await render_to_images(page, SAMPLE, out, max_height=7000)
        print(paths)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
