import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extract import normalize_copied_markdown  # noqa: E402

SAMPLE = r"""共射极放大电路的电压增益公式为：\\(A_v=-\beta\frac{R_c}{r\_{be}}\\)。其中，负号表示输出电压与输入电压反相。"""

DISPLAY = r"""先看代码：

```python
gm = ICQ / UT
Av = -gm * RC / 2
```

再看公式：

\\[
A_v = -\frac{\beta R_C}{2 r_{be}}
\\]

以及行内 \\(y_2\\)。"""


def main() -> None:
    out1 = normalize_copied_markdown(SAMPLE)
    print(repr(out1))
    assert "$A_v=-\\beta\\frac{R_c}{r_{be}}$" in out1, out1

    out2 = normalize_copied_markdown(DISPLAY)
    print(out2)
    assert "$$" in out2 and "A_v = -\\frac{\\beta R_C}{2 r_{be}}" in out2
    assert "$y_2$" in out2
    assert "```python" in out2 and "gm = ICQ / UT" in out2
    print("OK")


if __name__ == "__main__":
    main()
