"""Generate the original Light Masks vector mark and antialiased HA icons."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "custom_components" / "light_masks" / "brand"
INK = "#18243B"
SCALE = 4

type Point = tuple[float, float]


class Mark:
    """Keep vector and raster geometry identical, including transparent cutouts."""

    def __init__(self) -> None:
        self.image = Image.new("RGBA", (256 * SCALE, 256 * SCALE))
        self.draw = ImageDraw.Draw(self.image)
        self.svg: list[str] = []

    def polygon(self, points: list[Point], color: str) -> None:
        self.draw.polygon([(round(x * SCALE), round(y * SCALE)) for x, y in points], fill=color)
        coordinates = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self.svg.append(f'<polygon points="{coordinates}" fill="{color}"/>')

    def path(self, start: Point, segments: list[tuple[Point, Point, Point]], color: str) -> None:
        points = [start]
        commands = [f"M {start[0]} {start[1]}"]
        origin = start
        for a, b, end in segments:
            commands.append(f"C {a[0]} {a[1]} {b[0]} {b[1]} {end[0]} {end[1]}")
            for step in range(1, 33):
                t = step / 32
                points.append(
                    (
                        (1 - t) ** 3 * origin[0]
                        + 3 * (1 - t) ** 2 * t * a[0]
                        + 3 * (1 - t) * t**2 * b[0]
                        + t**3 * end[0],
                        (1 - t) ** 3 * origin[1]
                        + 3 * (1 - t) ** 2 * t * a[1]
                        + 3 * (1 - t) * t**2 * b[1]
                        + t**3 * end[1],
                    )
                )
            origin = end
        self.draw.polygon([(round(x * SCALE), round(y * SCALE)) for x, y in points], fill=color)
        self.svg.append(f'<path d="{" ".join(commands)} Z" fill="{color}"/>')

    def ellipse(self, box: tuple[int, int, int, int], color: str) -> None:
        x1, y1, x2, y2 = box
        self.draw.ellipse(tuple(value * SCALE for value in box), fill=color)
        self.svg.append(
            f'<ellipse cx="{(x1 + x2) / 2}" cy="{(y1 + y2) / 2}" '
            f'rx="{(x2 - x1) / 2}" ry="{(y2 - y1) / 2}" fill="{color}"/>'
        )

    def layer(self, y: int, color: str, edge: str, highlight: str) -> None:
        self.path(
            (30, y - 5),
            [
                ((24, y - 2), (24, y + 9), (32, y + 13)),
                ((62, y + 27), (104, y + 48), (123, y + 56)),
                ((126, y + 58), (130, y + 58), (133, y + 56)),
                ((152, y + 48), (194, y + 27), (224, y + 13)),
                ((232, y + 9), (232, y - 2), (226, y - 5)),
                ((198, y - 20), (152, y - 41), (133, y - 49)),
                ((130, y - 51), (126, y - 51), (123, y - 49)),
                ((104, y - 41), (58, y - 20), (30, y - 5)),
            ],
            INK,
        )
        self.polygon(
            [(32, y + 2), (128, y + 48), (224, y + 2), (224, y + 9), (128, y + 53), (32, y + 9)],
            edge,
        )
        self.path(
            (34, y - 2),
            [
                ((57, y - 14), (108, y - 38), (125, y - 45)),
                ((127, y - 46), (129, y - 46), (131, y - 45)),
                ((148, y - 38), (199, y - 14), (222, y - 2)),
                ((225, y), (225, y + 2), (222, y + 4)),
                ((199, y + 15), (148, y + 39), (131, y + 46)),
                ((129, y + 47), (127, y + 47), (125, y + 46)),
                ((108, y + 39), (57, y + 15), (34, y + 4)),
                ((31, y + 2), (31, y), (34, y - 2)),
            ],
            color,
        )
        self.polygon([(40, y), (128, y - 40), (216, y), (128, y - 34)], highlight)
        # A masquerade visor on the exposed face of each floating plane.
        self.path(
            (82, y + 1),
            [
                ((97, y - 5), (113, y - 1), (128, y + 7)),
                ((143, y - 1), (159, y - 5), (174, y + 1)),
                ((172, y + 17), (159, y + 31), (143, y + 27)),
                ((136, y + 26), (132, y + 18), (128, y + 17)),
                ((124, y + 18), (120, y + 26), (113, y + 27)),
                ((97, y + 31), (84, y + 17), (82, y + 1)),
            ],
            INK,
        )
        self.path(
            (94, y + 8),
            [
                ((102, y + 7), (111, y + 10), (118, y + 16)),
                ((108, y + 19), (99, y + 16), (94, y + 8)),
            ],
            highlight,
        )
        self.path(
            (162, y + 8),
            [
                ((154, y + 7), (145, y + 10), (138, y + 16)),
                ((148, y + 19), (157, y + 16), (162, y + 8)),
            ],
            highlight,
        )


def main() -> None:
    mark = Mark()
    # The lamp sits below the stack; the lowest mask overlaps its crown.
    mark.path(
        (92, 177),
        [
            ((91, 200), (99, 210), (108, 220)),
            ((111, 224), (111, 228), (111, 235)),
            ((111, 244), (145, 244), (145, 235)),
            ((145, 228), (145, 224), (148, 220)),
            ((157, 210), (165, 200), (164, 177)),
            ((160, 146), (96, 146), (92, 177)),
        ],
        INK,
    )
    mark.path(
        (98, 177),
        [
            ((97, 198), (106, 208), (113, 216)),
            ((116, 220), (117, 223), (117, 228)),
            ((122, 230), (134, 230), (139, 228)),
            ((139, 223), (140, 220), (143, 216)),
            ((150, 208), (159, 198), (158, 177)),
            ((154, 153), (102, 153), (98, 177)),
        ],
        "#FFD36B",
    )
    mark.ellipse((106, 166, 149, 206), "#FFF3C4")
    mark.polygon([(115, 231), (141, 231), (141, 235), (115, 235)], "#A5B8CD")
    mark.polygon([(119, 241), (137, 241), (133, 247), (123, 247)], INK)
    mark.polygon(
        [
            (120, 222),
            (120, 207),
            (111, 196),
            (115, 193),
            (128, 207),
            (141, 193),
            (145, 196),
            (136, 207),
            (136, 222),
            (131, 222),
            (131, 210),
            (125, 210),
            (125, 222),
        ],
        "#B8792A",
    )
    for points in (
        [(76, 183), (80, 188), (68, 196), (64, 191)],
        [(180, 183), (176, 188), (188, 196), (192, 191)],
        [(82, 210), (86, 214), (77, 225), (72, 221)],
        [(174, 210), (170, 214), (179, 225), (184, 221)],
    ):
        mark.polygon(points, "#FFBC48")
    mark.layer(125, "#36CFC9", "#14908F", "#A5FFF0")
    mark.layer(88, "#A58AF5", "#7053C5", "#DFD2FF")
    mark.layer(51, "#FF837C", "#CC5266", "#FFCCC0")
    DESTINATION.mkdir(exist_ok=True)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        'role="img" aria-labelledby="title desc">\n'
        '<title id="title">Light Masks</title>\n'
        '<desc id="desc">Three colorful floating layers, each bearing a mask, '
        'above a glowing light bulb.</desc>\n<g transform="translate(8 8) scale(.9375)">\n'
        + "\n".join(mark.svg)
        + "\n</g>\n</svg>\n"
    )
    (DESTINATION / "icon.svg").write_text(svg, encoding="utf-8")
    padded = Image.new("RGBA", mark.image.size)
    padded.paste(
        mark.image.resize((240 * SCALE, 240 * SCALE), Image.Resampling.LANCZOS),
        (8 * SCALE, 8 * SCALE),
    )
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        padded.resize((size, size), Image.Resampling.LANCZOS).save(DESTINATION / name)
    preview_dir = ROOT / "dist"
    preview_dir.mkdir(exist_ok=True)
    panels = []
    for theme in ("light", "dark"):
        samples = "".join(
            f'<div><img width="{size}" height="{size}" src="'
            f'../custom_components/light_masks/brand/icon@2x.png" alt="Light Masks">'
            f"<small>{size} px</small></div>"
            for size in (256, 64, 32)
        )
        panels.append(f'<section class="{theme}">{samples}</section>')
    (preview_dir / "brand-preview.html").write_text(
        """<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Light Masks - brand preview</title>
<style>
*{box-sizing:border-box}body{margin:0;padding:40px;background:#e8edf4;
font-family:system-ui,sans-serif;color:#18243b}main{max-width:850px;margin:auto}
h1{font-size:42px;letter-spacing:-2px;margin:0}p{line-height:1.6}
header{margin-bottom:32px}section{border-radius:24px;padding:32px;margin:20px 0;
display:flex;align-items:center;justify-content:space-around;gap:24px;flex-wrap:wrap}
.light{background:#fff}.dark{background:#101827;color:#d6e3f1}
img{display:block;object-fit:contain}small{display:block;text-align:center;
margin-top:18px;opacity:.6}footer{font-size:14px;line-height:1.8}
a{color:inherit}
</style><main><header><h1>Light Masks</h1>
<p>Independent personalities. One light.<br>
Coral, violet and mint masks float above a warm golden bulb.</p></header>"""
        + "".join(panels)
        + "<footer>Transparent artwork, at integration-card and compact sizes.<br>"
        '<a href="../custom_components/light_masks/brand/icon.svg">Vector SVG</a>'
        ' &middot; <a href="../custom_components/light_masks/brand/icon.png">256 px PNG</a>'
        ' &middot; <a href="../custom_components/light_masks/brand/icon@2x.png">'
        "512 px PNG</a></footer></main></html>\n",
        encoding="utf-8",
    )
    print(f"Generated SVG and 256/512 px transparent icons in {DESTINATION}")
    print(f"Preview: {preview_dir / 'brand-preview.html'}")


if __name__ == "__main__":
    main()
