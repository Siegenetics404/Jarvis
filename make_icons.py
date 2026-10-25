"""Turns your icon picture into the four sizes Chrome needs, with transparent corners.
Run it from C:\\dev\\jarvis:   python make_icons.py
Needs:  extension\\icons\\source.png   (and optionally  source_small.png  for the 16 and 32 px sizes)"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ICON_DIR = Path(__file__).resolve().parent / "extension" / "icons"
SOURCE = ICON_DIR / "source.png"
SOURCE_SMALL = ICON_DIR / "source_small.png"
SIZES = [16, 32, 48, 128]

SHAPE = "circle"        # "circle", "rounded" (a rounded square) or "square" (keep the picture's own corners)
ZOOM = 1.0              # 1.0 = the whole picture. 1.2 = crop in 20%, so the orb looks bigger
ZOOM_SMALL = 1.2        # the same for the 16 and 32 px icons, where a bigger orb reads better


def square_crop(img, zoom):
    """The middle square of the picture, cropped in by `zoom`."""
    side = min(img.size)
    side = side / zoom
    left = (img.width - side) / 2
    top = (img.height - side) / 2
    return img.crop((round(left), round(top), round(left + side), round(top + side)))


def make_mask(size, shape):
    """A smooth-edged mask, drawn 4 times bigger and shrunk so the edge isn't jagged."""
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    if shape == "circle":
        draw.ellipse((0, 0, big - 1, big - 1), fill=255)
    elif shape == "rounded":
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.22), fill=255)
    else:
        draw.rectangle((0, 0, big, big), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def make_icon(size):
    small = size <= 32
    path = SOURCE_SMALL if (small and SOURCE_SMALL.exists()) else SOURCE
    img = Image.open(path).convert("RGBA")
    img = square_crop(img, ZOOM_SMALL if small else ZOOM)
    img = img.resize((size, size), Image.LANCZOS)
    if small:                                            # tiny sizes lose sharpness when shrunk
        img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=70, threshold=2))
    img.putalpha(make_mask(size, SHAPE))
    return img


def preview(icons):
    """One picture showing the icons at their real size on a dark and on a light toolbar."""
    gap = 24
    width = sum(i.width for i in icons) + gap * (len(icons) + 1)
    height = 128 + gap * 2
    sheet = Image.new("RGBA", (width, height * 2))
    for row, color in enumerate(("#202124", "#f1f3f4")):
        ImageDraw.Draw(sheet).rectangle([0, row * height, width, (row + 1) * height], fill=color)
        x = gap
        for icon in icons:
            sheet.alpha_composite(icon, (x, row * height + (height - icon.height) // 2))
            x += icon.width + gap
    sheet.save(ICON_DIR / "preview.png")


def main():
    if not SOURCE.exists():
        print(f"I can't find {SOURCE}")
        print("Save your picture there as source.png, then run this again.")
        return
    icons = []
    for size in SIZES:
        icon = make_icon(size)
        icon.save(ICON_DIR / f"icon{size}.png")
        icons.append(icon)
        print(f"made icon{size}.png")
    preview(icons)
    print("Open extension\\icons\\preview.png to check how they look on a dark and a light toolbar.")


if __name__ == "__main__":
    main()