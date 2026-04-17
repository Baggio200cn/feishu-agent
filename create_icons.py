"""
生成 assets/ 目录下的占位图标（用于 GUI 启动器）
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


def _make_icon(size, bg_color, text, text_color="#FFFFFF"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 圆形背景
    draw.ellipse([2, 2, size - 2, size - 2], fill=bg_color)
    # 文字居中
    font_size = int(size * 0.45)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), text, fill=text_color, font=font)
    return img


def create_all():
    # icon.png — 窗口图标（蓝色火箭）
    img = _make_icon(64, "#1B6FF5", "🚀")
    img.save(os.path.join(ASSETS, "icon.png"))
    # icon.ico — Windows 任务栏图标
    ico = _make_icon(32, "#1B6FF5", "F")
    ico.save(os.path.join(ASSETS, "icon.ico"), format="ICO", sizes=[(32, 32)])

    # logo.png — 启动器顶部大图标
    img = _make_icon(96, "#1B6FF5", "飞")
    img.save(os.path.join(ASSETS, "logo.png"))

    # agent.png — 聊天窗口助理角色图标
    img = _make_icon(72, "#28A745", "A")
    img.save(os.path.join(ASSETS, "agent.png"))

    print("✅ 图标已生成到 assets/ 目录:")
    for f in ["icon.png", "icon.ico", "logo.png", "agent.png"]:
        p = os.path.join(ASSETS, f)
        if os.path.exists(p):
            print(f"   {f}  ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    create_all()
