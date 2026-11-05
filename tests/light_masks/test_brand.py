"""Keep bundled brand images valid and reproducible without a running HA."""

from pathlib import Path

import pytest
from PIL import Image

from scripts import generate_brand

BRAND = Path(__file__).resolve().parents[2] / "custom_components" / "light_masks" / "brand"


@pytest.mark.parametrize("prefix", ["", "dark_"])
@pytest.mark.parametrize(("suffix", "size"), [("", 256), ("@2x", 512)])
def test_icon_format(prefix, suffix, size):
    with Image.open(BRAND / f"{prefix}icon{suffix}.png") as image:
        assert image.format == "PNG"
        assert image.size == (size, size)
        assert image.mode == "RGBA"
        alpha = image.getchannel("A")
        assert alpha.getextrema() == (0, 255)
        bounds = alpha.getbbox()
        assert bounds is not None
        left, top, right, bottom = bounds
        assert 0 < left < right < size
        assert 0 < top < bottom < size


def test_brand_generator_is_reproducible(tmp_path, monkeypatch):
    destination = tmp_path / "brand"
    monkeypatch.setattr(generate_brand, "ROOT", tmp_path)
    monkeypatch.setattr(generate_brand, "DESTINATION", destination)

    generate_brand.main()

    assert {path.name for path in destination.iterdir()} == {
        "icon.svg",
        "icon.png",
        "icon@2x.png",
        "dark_icon.png",
        "dark_icon@2x.png",
    }
    for generated in destination.iterdir():
        committed = BRAND / generated.name
        if generated.suffix == ".png":
            with Image.open(generated) as actual, Image.open(committed) as expected:
                assert actual.mode == expected.mode
                assert actual.size == expected.size
                assert actual.tobytes() == expected.tobytes()
        else:
            assert generated.read_bytes() == committed.read_bytes()
    preview = (tmp_path / "dist" / "brand-preview.html").read_text(encoding="utf-8")
    assert 'brand/icon@2x.png"' in preview
    assert 'brand/dark_icon@2x.png"' in preview
