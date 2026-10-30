"""生成一张中文题目图片，用于测试「以图提问」。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / ".ask-out"
OUT.mkdir(exist_ok=True)
path = OUT / "question.png"

img = Image.new("RGB", (900, 420), "white")
draw = ImageDraw.Draw(img)
cn_font = Path("C:/Windows/Fonts/msyh.ttc")
if cn_font.exists():
    font = ImageFont.truetype(str(cn_font), 34)
    title = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 40)
else:
    font = ImageFont.load_default()
    title = font

draw.text((40, 30), "题目（分压偏置电路）", font=title, fill="black")
lines = [
    "已知 Vcc = 12 V，R1 = 20 kΩ，R2 = 10 kΩ，",
    "三极管 β = 100，r_be = 1 kΩ，Rc = 3 kΩ。",
    "",
    "求：基极偏置电压 VB、集电极电流 IC，",
    "并说明单端输出时电压放大倍数为什么是负的。",
]
y = 100
for line in lines:
    draw.text((40, y), line, font=font, fill="black")
    y += 58

img.save(path)
print(path)
