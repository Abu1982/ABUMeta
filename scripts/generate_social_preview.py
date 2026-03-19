from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1280
HEIGHT = 640
BG_TOP = (15, 23, 34)
BG_BOTTOM = (22, 34, 53)
WHITE = (245, 247, 250)
STEEL = (142, 163, 183)
TEAL = (57, 198, 180)
AMBER = (243, 185, 76)
PANEL_BORDER = (80, 98, 118, 100)
PANEL_FILL = (255, 255, 255, 18)


def load_font(
    size: int, *, bold: bool = False, chinese: bool = False
) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                r"C:\Windows\Fonts\bahnschrift.ttf",
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                r"C:\Windows\Fonts\bahnschrift.ttf",
                r"C:\Windows\Fonts\segoeui.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
        )
    if chinese:
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
        ] + candidates
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def make_gradient() -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / max(HEIGHT - 1, 1)
        color = tuple(
            int(BG_TOP[i] * (1 - ratio) + BG_BOTTOM[i] * ratio) for i in range(3)
        ) + (255,)
        draw.line((0, y, WIDTH, y), fill=color)
    return image


def draw_glow(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    for step, alpha in ((radius * 3, 22), (radius * 2, 36), (radius, 70)):
        box = (cx - step, cy - step, cx + step, cy + step)
        draw.ellipse(box, fill=color + (alpha,))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "assets" / "social_preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)

    image = make_gradient()
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rounded_rectangle(
        (64, 64, 1216, 576),
        radius=28,
        fill=(255, 255, 255, 8),
        outline=(142, 163, 183, 38),
        width=2,
    )
    draw.rounded_rectangle(
        (98, 104, 660, 536), radius=26, fill=PANEL_FILL, outline=PANEL_BORDER, width=2
    )

    for coords, color in (
        ((740, 132, 1160, 182), TEAL),
        ((714, 210, 1148, 294), STEEL),
        ((694, 298, 1106, 414), AMBER),
        ((724, 390, 1080, 494), TEAL),
    ):
        draw.arc(coords, start=190, end=340, fill=color + (70,), width=2)

    title_font = load_font(92, bold=True)
    sub_font = load_font(27, bold=True)
    zh_font = load_font(30, bold=False, chinese=True)
    body_font = load_font(24)
    zh_body_font = load_font(23, chinese=True)
    meta_font = load_font(20)
    node_font = load_font(16, bold=True)

    draw.text((130, 108), "ABUMeta", font=title_font, fill=WHITE)
    draw.text((136, 198), "BILINGUAL AUTONOMOUS AGENT DEMO", font=sub_font, fill=TEAL)
    draw.text((136, 246), "双语自治 Agent 演示版", font=zh_font, fill=WHITE)
    draw.text(
        (136, 308),
        "Memory / Psyche / Decision / Execution / Governance",
        font=body_font,
        fill=STEEL,
    )
    draw.text(
        (136, 370),
        "面向研究、演示与二次开发的类人自治架构",
        font=zh_body_font,
        fill=(217, 225, 232, 255),
    )
    draw.text(
        (136, 414),
        "Open-Source Demo / MIT / Windows + Linux / Cross-Platform Smoke",
        font=meta_font,
        fill=STEEL,
    )

    nodes = {
        "MEMORY": ((914, 146), TEAL, 16),
        "PSYCHE": ((1034, 214), AMBER, 14),
        "DECISION": ((1078, 338), TEAL, 14),
        "EXECUTION": ((1004, 458), STEEL, 13),
        "GOVERNANCE": ((864, 480), AMBER, 15),
        "": ((756, 392), TEAL, 13),
        " ": ((792, 210), STEEL, 12),
    }
    edges = [
        ((792, 210), (914, 146)),
        ((914, 146), (1034, 214)),
        ((1034, 214), (1078, 338)),
        ((1078, 338), (1004, 458)),
        ((1004, 458), (864, 480)),
        ((864, 480), (756, 392)),
        ((756, 392), (792, 210)),
        ((864, 480), (914, 146)),
        ((756, 392), (1034, 214)),
        ((792, 210), (1078, 338)),
    ]
    for start, end in edges:
        draw.line((start, end), fill=(245, 247, 250, 60), width=2)

    glow_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    for _, (center, color, radius) in zip(nodes.keys(), nodes.values()):
        draw_glow(glow_draw, center, radius, color)
    image = Image.alpha_composite(image, glow_layer)

    draw = ImageDraw.Draw(overlay)
    for label, (center, color, radius) in nodes.items():
        cx, cy = center
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius), fill=color + (255,)
        )
        if label.strip():
            offsets = {
                "MEMORY": (-32, -52),
                "PSYCHE": (10, -20),
                "DECISION": (12, -2),
                "EXECUTION": (4, 22),
                "GOVERNANCE": (-46, 30),
            }
            ox, oy = offsets[label]
            draw.text((cx + ox, cy + oy), label, font=node_font, fill=WHITE)

    draw.line((96, 556, 1184, 556), fill=(142, 163, 183, 56), width=1)
    footer = [
        (110, "RESEARCH"),
        (262, "READABILITY"),
        (462, "DEMO"),
        (605, "CROSS-PLATFORM"),
        (892, "OPEN SOURCE"),
    ]
    for x, text in footer:
        draw.text((x, 576), text, font=meta_font, fill=STEEL)

    final = Image.alpha_composite(image, overlay)
    final.save(output)
    print(output)


if __name__ == "__main__":
    main()
