import numpy as np
from PIL import Image, ImageDraw

from src.material_projection import (
    PerspectiveMaterialConfig,
    build_orthographic_tile_plane,
    metric_scale_verified,
    outside_mask_invariance,
    render_perspective_material,
)


def _fixture():
    source = Image.new("RGB", (320, 240), (180, 185, 190))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 319, 90), fill=(120, 160, 205))
    draw.rectangle((0, 90, 319, 120), fill=(205, 200, 190))
    mask = Image.new("L", source.size, 0)
    ImageDraw.Draw(mask).polygon([(70, 118), (245, 118), (319, 239), (35, 239)], fill=255)
    texture = Image.new("RGB", (64, 64), (170, 172, 168))
    tex = np.asarray(texture).copy()
    tex[8:20, 8:20] = (195, 194, 188)
    tex[40:52, 34:48] = (145, 148, 146)
    texture = Image.fromarray(tex)
    return source, mask, texture


def test_masked_projection_preserves_every_outside_pixel():
    source, mask, texture = _fixture()
    config = PerspectiveMaterialConfig(
        plane_quad=((70, 118), (245, 118), (319, 239), (35, 239)),
        floor_width_mm=2980,
        floor_depth_mm=3576,
        tile_width_mm=298,
        tile_depth_mm=298,
        grout_mm=2,
        scale_basis="FIELD_ANCHORED_ESTIMATE",
    )
    result, meta = render_perspective_material(source, mask, texture, config)
    qa = outside_mask_invariance(source, result, mask)
    assert qa == {
        "dimensions_match": True,
        "outside_changed_pixels": 0,
        "outside_max_channel_diff": 0,
    }
    assert result.size == source.size
    assert meta["full_image_generation_used"] is False
    assert meta["tile_size_mm"] == [298, 298]


def test_material_projection_changes_selected_floor_pixels():
    source, mask, texture = _fixture()
    config = PerspectiveMaterialConfig(
        plane_quad=((70, 118), (245, 118), (319, 239), (35, 239)),
        floor_width_mm=2980,
        floor_depth_mm=3576,
        tile_width_mm=298,
        tile_depth_mm=298,
        scale_basis="RELATIVE",
    )
    result, _ = render_perspective_material(source, mask, texture, config)
    src = np.asarray(source)
    out = np.asarray(result)
    edit = np.asarray(mask) > 8
    assert np.count_nonzero(np.max(np.abs(src.astype(int) - out.astype(int)), axis=2)[edit]) > 0


def test_metric_verification_fails_closed_for_estimated_or_relative_plane():
    common = dict(
        plane_quad=((70, 118), (245, 118), (319, 239), (35, 239)),
        floor_width_mm=2980,
        floor_depth_mm=3576,
        tile_width_mm=298,
        tile_depth_mm=298,
    )
    assert metric_scale_verified(PerspectiveMaterialConfig(**common, scale_basis="RELATIVE")) is False
    assert metric_scale_verified(PerspectiveMaterialConfig(**common, scale_basis="FIELD_ANCHORED_ESTIMATE")) is False
    assert metric_scale_verified(PerspectiveMaterialConfig(**common, scale_basis="FIELD_MEASURED")) is True


def test_tile_count_uses_physical_module_not_visual_guess():
    source, mask, texture = _fixture()
    config = PerspectiveMaterialConfig(
        plane_quad=((70, 118), (245, 118), (319, 239), (35, 239)),
        floor_width_mm=2980,
        floor_depth_mm=3576,
        tile_width_mm=298,
        tile_depth_mm=298,
        grout_mm=2,
        scale_basis="FIELD_MEASURED",
    )
    _, meta = render_perspective_material(source, mask, texture, config)
    assert meta["cols"] == 10
    assert meta["rows"] == 12


def test_partial_plane_extent_does_not_rescale_298mm_tiles():
    _, _, texture = _fixture()
    config = PerspectiveMaterialConfig(
        plane_quad=((70, 118), (245, 118), (319, 239), (35, 239)),
        floor_width_mm=2280,
        floor_depth_mm=3000,
        tile_width_mm=298,
        tile_depth_mm=298,
        grout_mm=2,
        scale_basis="FIELD_ANCHORED_ESTIMATE",
        tile_pixels=96,
    )
    plane, meta = build_orthographic_tile_plane(texture, config)
    assert meta["cols"] == 8
    assert meta["rows"] == 11
    assert meta["plane_width_px"] == round(2280 / 298 * 96)
    assert meta["plane_height_px"] == round(3000 / 298 * 96)
    assert plane.shape[1] == meta["plane_width_px"]
    assert plane.shape[0] == meta["plane_height_px"]
    # The canvas is intentionally smaller than ceil(tile_count) * tile_pixels;
    # otherwise the homography would shrink every 298 mm tile to fit.
    assert meta["plane_width_px"] < meta["cols"] * meta["tile_pixels"]
    assert meta["plane_height_px"] < meta["rows"] * meta["tile_pixels"]
