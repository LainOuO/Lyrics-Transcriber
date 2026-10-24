"""一次性生成应用图标 lyrics_extractor/assets/app.ico（开发用，不参与打包）。

改图标时跑一次即可：python packaging/make_icon.py
依赖 Pillow，仅此脚本使用；程序本身与打包流程都不依赖它。
"""
from PIL import Image, ImageDraw

S = 1024
TOP = (88, 104, 226, 255)
BOTTOM = (60, 74, 188, 255)

grad = Image.new("RGBA", (S, S))
for y in range(S):
    t = y / (S - 1)
    grad.paste(tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(4)),
               (0, y, S, y + 1))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(grad, (0, 0), mask)


def bezier(p0, p1, p2, n):
    out = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        out.append((x, y))
    return out


note = Image.new("RGBA", (S, S), (0, 0, 0, 0))
head = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(head).ellipse([280, 590, 540, 794], fill=(255, 255, 255, 255))
note.alpha_composite(head.rotate(-18, center=(410, 692), resample=Image.BICUBIC))

nd = ImageDraw.Draw(note)
nd.rectangle([506, 262, 560, 700], fill=(255, 255, 255, 255))
flag = bezier((560, 262), (716, 306), (734, 508), 14) + \
       bezier((734, 508), (700, 392), (560, 386), 14)
nd.polygon(flag, fill=(255, 255, 255, 255))
icon.alpha_composite(note)

icon.save("C:/Users/good/AppData/Local/Temp/shot/icon_1024.png")
icon.save("E:/Vibecoding/歌单/lyrics_extractor/assets/app.ico",
          sizes=[(s, s) for s in (256, 128, 64, 48, 32, 24, 16)])
for s in (16, 24, 32, 48):
    icon.resize((s, s), Image.LANCZOS).resize((s * 8, s * 8), Image.NEAREST).save(
        f"C:/Users/good/AppData/Local/Temp/shot/icon_{s}.png")
print("ok")
