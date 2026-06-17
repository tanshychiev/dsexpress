from pathlib import Path
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent
IMG_DIR = BASE_DIR / "static" / "img"

SOURCE = IMG_DIR / "favicon.png"

OUTPUTS = [
    (180, IMG_DIR / "ds-app-icon-180.png"),
    (192, IMG_DIR / "ds-app-icon-192.png"),
    (512, IMG_DIR / "ds-app-icon-512.png"),
]

GREEN = (15, 122, 69, 255)

if not SOURCE.exists():
    raise FileNotFoundError(f"Cannot find source icon: {SOURCE}")

src = Image.open(SOURCE).convert("RGBA")

# Crop transparent/empty space around logo first
bbox = src.getbbox()
if bbox:
    src = src.crop(bbox)

for size, output_path in OUTPUTS:
    canvas = Image.new("RGBA", (size, size), GREEN)

    # Make the logo bigger inside the square
    padding = int(size * 0.04)
    logo_box = size - (padding * 2)

    logo = ImageOps.contain(src, (logo_box, logo_box), Image.LANCZOS)

    x = (size - logo.width) // 2
    y = (size - logo.height) // 2

    canvas.alpha_composite(logo, (x, y))

    # iPhone prefers no transparency
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)

    print("Saved:", output_path)

print("DONE")