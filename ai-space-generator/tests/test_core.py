import numpy as np
from PIL import Image, ImageDraw

from src.core import (
    composite_preserving_unmasked,
    dominant_colours,
    local_transform,
    mask_from_editor,
)


def test_dominant_colours_returns_hex():
    image = Image.new("RGB", (80, 80), "#caa472")
    colours = dominant_colours(image, count=3)
    assert colours
    assert all(value.startswith("#") and len(value) == 7 for value in colours)


def test_mask_from_editor_extracts_alpha():
    background = Image.new("RGB", (100, 100), "white")
    layer = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle((20, 20, 60, 60), fill=(255, 0, 0, 255))
    bg, mask = mask_from_editor({"background": background, "layers": [layer], "composite": background})
    assert bg is not None and mask is not None
    assert np.asarray(mask)[30, 30] > 0
    assert np.asarray(mask)[5, 5] == 0


def test_local_material_transform_preserves_unmasked_area():
    image = Image.new("RGB", (80, 80), "white")
    mask = Image.new("L", (80, 80), 0)
    ImageDraw.Draw(mask).rectangle((20, 20, 60, 60), fill=255)
    result = local_transform(image, mask, "替換／加入材質", "木材", "#ffffff", "oak", 1.0)
    array = np.asarray(result)
    assert tuple(array[5, 5]) == (255, 255, 255)
    assert tuple(array[40, 40]) != (255, 255, 255)



def test_composite_preserving_unmasked_pixels_are_exact():
    original = Image.new("RGB", (64, 64), (10, 20, 30))
    candidate = Image.new("RGB", (64, 64), (240, 220, 200))
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).rectangle((16, 16, 47, 47), fill=255)

    result = composite_preserving_unmasked(candidate, original, mask)
    original_array = np.asarray(original)
    result_array = np.asarray(result)
    mask_array = np.asarray(mask)

    assert np.array_equal(result_array[mask_array == 0], original_array[mask_array == 0])
    assert tuple(result_array[32, 32]) == (240, 220, 200)


def test_background_local_mode_requires_masked_remote_backend():
    image = Image.new("RGB", (64, 64), "white")
    mask = Image.new("L", (64, 64), 255)

    try:
        local_transform(image, mask, "更換背景（遮罩 inpainting）", "木材", "#ffffff", "beach", 1.0)
    except ValueError as exc:
        assert "遠端 AI" in str(exc)
    else:
        raise AssertionError("background replacement should not fall back to local colour fill")
