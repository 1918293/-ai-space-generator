import numpy as np
import pytest
from pathlib import Path\n\nfrom PIL import Image, ImageDraw

import src.core as core
from src.core import assert_local_edit_integrity, dominant_colours, ensure_rgb, local_transform, mask_from_editor


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



def test_remote_generate_local_never_sends_full_frame(monkeypatch):
    image = Image.new("RGB", (120, 100), "white")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle((40, 30, 70, 60), fill=255)
    seen = {}

    def fake_remote_generate(crop, prompt):
        seen["size"] = crop.size
        return Image.new("RGB", crop.size, "black")

    monkeypatch.setattr(core, "remote_generate", fake_remote_generate)
    result, bbox = core.remote_generate_local(image, mask, "test")

    assert seen["size"] == (bbox[2] - bbox[0], bbox[3] - bbox[1])
    assert seen["size"] != image.size
    assert assert_local_edit_integrity(image, result, mask) == 0


def test_assert_local_edit_integrity_rejects_outside_mask_change():
    source = Image.new("RGB", (40, 40), "white")
    mask = Image.new("L", source.size, 0)
    ImageDraw.Draw(mask).rectangle((15, 15, 25, 25), fill=255)
    result = source.copy()
    result.putpixel((2, 2), (0, 0, 0))

    with pytest.raises(RuntimeError, match="LOCAL_EDIT_OUTSIDE_MASK_DIFF"):
        assert_local_edit_integrity(source, result, mask)



def test_remote_generate_local_rejects_whole_frame_mask(monkeypatch):
    image = Image.new("RGB", (80, 80), "white")
    mask = Image.new("L", image.size, 255)

    def should_not_run(crop, prompt):
        raise AssertionError("remote generation must not run for a whole-frame local-edit route")

    monkeypatch.setattr(core, "remote_generate", should_not_run)
    with pytest.raises(RuntimeError, match="LOCAL_EDIT_WHOLE_FRAME_ROUTE_BLOCKED"):
        core.remote_generate_local(image, mask, "test")



def test_ensure_rgb_preserves_icc_metadata():
    image = Image.new("RGB", (20, 20), "white")
    image.info["icc_profile"] = b"source-profile-bytes"
    converted = ensure_rgb(image)
    assert converted.info.get("icc_profile") == b"source-profile-bytes"


def test_profiled_source_blocks_remote_route_before_generation_when_backend_colorspace_undeclared(monkeypatch):
    image = Image.new("RGB", (100, 80), "white")
    image.info["icc_profile"] = b"source-profile-bytes"
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle((30, 20, 60, 50), fill=255)
    monkeypatch.delenv("GENERATION_OUTPUT_COLORSPACE", raising=False)

    def should_not_run(crop, prompt):
        raise AssertionError("remote generation must not run before color admission")

    monkeypatch.setattr(core, "remote_generate", should_not_run)
    with pytest.raises(RuntimeError, match="REMOTE_OUTPUT_COLORSPACE_UNDECLARED"):
        core.remote_generate_local(image, mask, "test")


def test_profiled_source_accepts_candidate_with_matching_icc(monkeypatch):
    profile = b"same-profile-bytes"
    image = Image.new("RGB", (100, 80), "white")
    image.info["icc_profile"] = profile
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle((30, 20, 60, 50), fill=255)
    monkeypatch.setenv("GENERATION_OUTPUT_COLORSPACE", "embedded")

    def fake_remote_generate(crop, prompt):
        result = Image.new("RGB", crop.size, "black")
        result.info["icc_profile"] = profile
        return result

    monkeypatch.setattr(core, "remote_generate", fake_remote_generate)
    result, _, receipt = core.remote_generate_local(image, mask, "test")
    assert receipt == "CANDIDATE_ICC_MATCHES_SOURCE"
    assert result.info.get("icc_profile") == profile
    assert core.assert_source_icc_preserved(image, result) == profile


def test_generate_design_png_readback_preserves_source_icc():
    profile = b"synthetic-test-icc-profile"
    background = Image.new("RGB", (80, 80), "white")
    background.info["icc_profile"] = profile
    layer = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((20, 20, 60, 60), fill=(255, 0, 0, 255))

    result, _, status, output_path = core.generate_design(
        {"background": background, "layers": [layer], "composite": background},
        "替換／加入材質",
        "木材",
        "#ffffff",
        "oak",
        1.0,
        False,
    )

    assert result is not None
    assert output_path is not None
    assert "ICC exact/readback PASS" in status
    try:
        with Image.open(output_path) as saved:
            assert saved.info.get("icc_profile") == profile
    finally:
        Path(output_path).unlink(missing_ok=True)
