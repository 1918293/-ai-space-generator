import numpy as np
import pytest
from PIL import Image, ImageDraw

from src.core import dominant_colours, generate_design, local_transform, mask_from_editor
from src.validation import image_binding_sha256, mask_binding_sha256


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

def test_generate_design_restores_low_alpha_pixels_outside_binary_edit_mask(tmp_path):
    background = Image.new("RGB", (32, 32), "white")
    layer_array = np.zeros((32, 32, 4), dtype=np.uint8)
    layer_array[10, 10] = (255, 0, 0, 4)
    layer_array[11, 11] = (255, 0, 0, 255)
    layer = Image.fromarray(layer_array, mode="RGBA")

    result, _, status, output_path = generate_design(
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
    assert "完成" in status
    assert result.getpixel((10, 10)) == (255, 255, 255)
    assert result.getpixel((11, 11)) != (255, 255, 255)

    # Delivery-layer readback: PNG encoding must not introduce any
    # out-of-mask pixel drift after the in-memory hard lock passes.
    allowed = layer_array[..., 3] > 8
    source_array = np.asarray(background)
    with Image.open(output_path) as saved:
        saved_array = np.asarray(saved.convert("RGB"))
    changed = np.any(saved_array != source_array, axis=2)
    assert int(np.count_nonzero(changed & ~allowed)) == 0
    assert int(np.count_nonzero(changed & allowed)) > 0


def test_generate_design_preserves_source_icc_profile_and_dimensions():
    background = Image.new("RGB", (40, 30), "white")
    source_icc = b"hao-test-icc-profile"
    background.info["icc_profile"] = source_icc

    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((12, 8, 24, 20), fill=(255, 0, 0, 255))

    result, _, status, output_path = generate_design(
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
    assert "完成" in status
    assert result.size == background.size
    assert result.info.get("icc_profile") == source_icc

    with Image.open(output_path) as saved:
        assert saved.size == background.size
        assert saved.info.get("icc_profile") == source_icc


def test_generate_design_holds_remove_without_target_authority_before_generation(monkeypatch):
    background = Image.new("RGB", (32, 32), "white")
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    called = []

    def must_not_generate(*args, **kwargs):
        called.append(True)
        raise AssertionError("generator must not run without target authority")

    monkeypatch.setattr("src.core.local_transform", must_not_generate)
    monkeypatch.setattr("src.core.remote_generate", must_not_generate)

    result, comparison, status, output_path = generate_design(
        {"background": background, "layers": [layer], "composite": background},
        "移除物件",
        "",
        "#ffffff",
        "remove selected object",
        1.0,
        False,
    )

    assert called == []
    assert result is None
    assert comparison is None
    assert output_path is None
    assert "TARGET_AUTHORITY_MISSING" in status


def test_generate_design_holds_forged_target_authority_binding_before_generation(monkeypatch):
    background = Image.new("RGB", (32, 32), "white")
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    _, admitted_mask = mask_from_editor({"background": background, "layers": [layer]})
    receipt = {
        "authority_id": "CURRENT-385-CASE",
        "authority_status": "PASS",
        "canonical_source_id": "SOURCE",
        "target_id": "TARGET-01",
        "target_state": "UNRESOLVED",
        "mask_binding_id": "MASK-01",
        "canonical_source_binding_sha256": "0" * 64,
        "edit_mask_binding_sha256": mask_binding_sha256(admitted_mask),
    }
    called = []

    def must_not_generate(*args, **kwargs):
        called.append(True)
        raise AssertionError("generator must not run on binding mismatch")

    monkeypatch.setattr("src.core.local_transform", must_not_generate)
    result, _, status, path = generate_design(
        {"background": background, "layers": [layer]},
        "移除物件", "", "#ffffff", "remove", 1.0, False,
        target_authority_receipt=receipt,
    )
    assert called == []
    assert result is None and path is None
    assert "TARGET_AUTHORITY_BINDING_MISMATCH" in status


def test_generate_design_accepts_matching_target_authority_binding():
    background = Image.new("RGB", (32, 32), "white")
    ImageDraw.Draw(background).rectangle((10, 10, 20, 20), fill=(255, 0, 0))
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    _, admitted_mask = mask_from_editor({"background": background, "layers": [layer]})
    receipt = {
        "authority_id": "CURRENT-385-CASE",
        "authority_status": "PASS",
        "canonical_source_id": "SOURCE",
        "target_id": "TARGET-01",
        "target_state": "UNRESOLVED",
        "mask_binding_id": "MASK-01",
        "canonical_source_binding_sha256": image_binding_sha256(background),
        "edit_mask_binding_sha256": mask_binding_sha256(admitted_mask),
    }
    result, _, status, path = generate_design(
        {"background": background, "layers": [layer]},
        "移除物件", "", "#ffffff", "remove", 1.0, False,
        target_authority_receipt=receipt,
    )
    assert result is not None and path is not None
    assert "完成" in status
    from pathlib import Path
    Path(path).unlink(missing_ok=True)


def test_generate_design_rejects_protected_editor_overlap_before_generation(monkeypatch):
    background = Image.new("RGB", (32, 32), "white")
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))

    protected_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(protected_layer).point((15, 15), fill=(0, 255, 0, 255))
    protected_editor = {
        "background": background,
        "layers": [protected_layer],
        "composite": background,
    }

    def must_not_generate(*args, **kwargs):
        raise AssertionError("generator must not run when edit/protected masks overlap")

    monkeypatch.setattr("src.core.local_transform", must_not_generate)

    result, comparison, status, output_path = generate_design(
        {"background": background, "layers": [layer], "composite": background},
        "替換／加入材質",
        "木材",
        "#ffffff",
        "oak",
        1.0,
        False,
        protected_editor,
    )

    assert result is None
    assert comparison is None
    assert output_path is None
    assert "生成失敗" in status
    assert "edit_protected_overlap_pixels=1" in status


def test_generate_design_accepts_blank_protected_editor():
    background = Image.new("RGB", (32, 32), "white")
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    blank_protected_editor = {
        "background": background,
        "layers": [],
        "composite": background,
    }

    result, _, status, output_path = generate_design(
        {"background": background, "layers": [layer], "composite": background},
        "替換／加入材質",
        "木材",
        "#ffffff",
        "oak",
        1.0,
        False,
        blank_protected_editor,
    )

    assert result is not None
    assert output_path is not None
    assert "完成" in status


def test_app_builds_with_protected_editor_surface():
    import app

    assert app.demo is not None
    assert app.editor is not None
    assert app.protected_editor is not None


def test_generate_design_rejects_protected_mask_from_different_source(monkeypatch):
    background = Image.new("RGB", (32, 32), "white")
    foreign_background = background.copy()
    foreign_background.putpixel((0, 0), (254, 255, 255))
    edit_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(edit_layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    protected_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(protected_layer).point((2, 2), fill=(0, 255, 0, 255))
    called = []

    def must_not_generate(*args, **kwargs):
        called.append(True)
        raise AssertionError("foreign-source protection must be rejected before generation")

    monkeypatch.setattr("src.core.local_transform", must_not_generate)
    result, _, status, path = generate_design(
        {"background": background, "layers": [edit_layer]},
        "替換／加入材質", "木材", "#ffffff", "oak", 1.0, False,
        {"background": foreign_background, "layers": [protected_layer]},
    )
    assert called == []
    assert result is None and path is None
    assert "source" in status.lower()


@pytest.mark.parametrize("component", ["layers", "composite"])
def test_editor_rejects_misaligned_mask_data(component):
    background = Image.new("RGB", (32, 32), "white")
    value = {"background": background, "layers": []}
    if component == "layers":
        value["layers"] = [Image.new("RGBA", (16, 32), (255, 0, 0, 255))]
    else:
        value["composite"] = Image.new("RGB", (16, 32), "red")
    with pytest.raises(ValueError, match="dimensions"):
        mask_from_editor(value)


def test_generate_design_withholds_corrupted_saved_png(monkeypatch, tmp_path):
    background = Image.new("RGB", (32, 32), "white")
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((10, 10, 20, 20), fill=(255, 0, 0, 255))
    original_save = Image.Image.save

    def corrupt_saved_pixel(image, filename, *args, **kwargs):
        corrupted = image.copy()
        corrupted.putpixel((0, 0), (0, 0, 0))
        return original_save(corrupted, filename, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", corrupt_saved_pixel)
    result, _, status, path = generate_design(
        {"background": background, "layers": [layer]},
        "替換／加入材質", "木材", "#ffffff", "oak", 1.0, False,
    )
    assert result is None and path is None
    assert "readback" in status.lower()


def test_generate_design_roundtrips_a_valid_srgb_profile():
    from pathlib import Path
    from PIL import ImageCms

    background = Image.new("RGB", (47, 29), "white")
    icc_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    background.info["icc_profile"] = icc_profile
    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((5, 5, 9, 11), fill=(255, 0, 0, 255))
    result, _, status, path = generate_design(
        {"background": background, "layers": [layer]},
        "替換／加入材質", "木材", "#ffffff", "oak", 1.0, False,
    )
    assert result is not None and path is not None
    try:
        with Image.open(path) as saved:
            assert saved.size == background.size
            assert saved.info["icc_profile"] == icc_profile
            assert np.array_equal(np.asarray(saved), np.asarray(result))
    finally:
        Path(path).unlink(missing_ok=True)
