"""
生成 assets/ 目录下的 GUI 图标
需要 Pillow: pip install Pillow
"""
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("请先安装 Pillow: pip install Pillow")
    sys.exit(1)

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(ASSETS, exist_ok=True)


def _font(size: int):
    for name in ("arial.ttf", "Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _circle_icon(size: int, bg: str, text: str, fg: str = "#FFFFFF") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, size // 16)
    d.ellipse([pad, pad, size - pad, size - pad], fill=bg)
    font = _font(int(size * 0.44))
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    d.text((x, y), text, fill=fg, font=font)
    return img


def create_all():
    specs = [
        # (filename, size, bg_color, label, save_as_ico)
        ("icon.png",  64,  "#1B6FF5", "F",  False),
        ("icon.ico",  32,  "#1B6FF5", "F",  True),
        ("logo.png",  96,  "#1B6FF5", "飞", False),
        ("agent.png", 72,  "#28A745", "AI", False),
        ("user.png",  72,  "#1B6FF5", "U",  False),
    ]
    for fname, size, bg, label, as_ico in specs:
        img = _circle_icon(size, bg, label)
        path = os.path.join(ASSETS, fname)
        if as_ico:
            img.save(path, format="ICO", sizes=[(32, 32)])
        else:
            img.save(path, format="PNG")
        print(f"  ✅ {fname}  ({os.path.getsize(path):,} bytes)")

    print(f"\n图标已生成到 {ASSETS}")


if __name__ == "__main__":
    create_all()
