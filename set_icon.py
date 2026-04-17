"""
将自定义图片设置为飞书 Agent 的图标
用法:
  python set_icon.py path/to/your_image.jpg
  python set_icon.py path/to/your_image.jpg --shortcut  # 同时更新桌面快捷方式

会生成 assets/icon.ico、icon.png、logo.png（用同一张图），并可选重建桌面快捷方式。
"""
import argparse
import os
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    print("请先安装 Pillow: pip install Pillow")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE_DIR, "assets")


def _center_crop_square(img: "Image.Image") -> "Image.Image":
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    return img.crop((left, top, left + s, top + s))


def generate_icons(src_path: str):
    if not os.path.exists(src_path):
        print(f"❌ 源图片不存在: {src_path}")
        sys.exit(1)

    os.makedirs(ASSETS, exist_ok=True)
    img = Image.open(src_path).convert("RGBA")
    sq = _center_crop_square(img)

    # icon.ico — Windows 多分辨率
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_path = os.path.join(ASSETS, "icon.ico")
    sq.save(ico_path, format="ICO", sizes=ico_sizes)

    # icon.png / logo.png / agent.png / user.png — PNG 版
    for name, size in [("icon.png", 64), ("logo.png", 96), ("agent.png", 72), ("user.png", 72)]:
        out = os.path.join(ASSETS, name)
        sq.resize((size, size), Image.LANCZOS).save(out, format="PNG")

    print("✅ 图标已更新到 assets/:")
    for f in ["icon.ico", "icon.png", "logo.png", "agent.png", "user.png"]:
        p = os.path.join(ASSETS, f)
        if os.path.exists(p):
            print(f"   {f}  ({os.path.getsize(p):,} bytes)")


def create_shortcut():
    """在桌面创建/覆盖快捷方式，使用 assets/icon.ico"""
    if sys.platform != "win32":
        print("ℹ 非 Windows 系统，跳过快捷方式创建")
        return

    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = "pythonw.exe"
    target = os.path.join(BASE_DIR, "launcher.pyw")
    icon = os.path.join(ASSETS, "icon.ico")

    ps = f"""
$WshShell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$link = $WshShell.CreateShortcut((Join-Path $desktop '飞书Agent.lnk'))
$link.TargetPath = '{pyw}'
$link.Arguments = '"{target}"'
$link.WorkingDirectory = '{BASE_DIR}'
$link.IconLocation = '{icon},0'
$link.Description = '飞书智能 Agent 启动器'
$link.Save()
Write-Host '桌面快捷方式已更新'
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode == 0:
            print("✅ 桌面快捷方式已重新创建（使用新图标）")
        else:
            print(f"⚠ 创建快捷方式失败: {result.stderr}")
    except Exception as e:
        print(f"⚠ 创建快捷方式异常: {e}")


def main():
    ap = argparse.ArgumentParser(description="设置飞书 Agent 自定义图标")
    ap.add_argument("source", help="源图片路径（PNG/JPG）")
    ap.add_argument("--shortcut", action="store_true",
                    help="同时在桌面创建/覆盖快捷方式")
    args = ap.parse_args()

    generate_icons(args.source)
    if args.shortcut:
        create_shortcut()
    else:
        print("\n💡 提示：加 --shortcut 参数可同时更新桌面快捷方式")


if __name__ == "__main__":
    main()
