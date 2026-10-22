from __future__ import annotations

import io
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import ExifTags, Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter

from src.validation import (
    composite_mask_only,
    image_binding_sha256,
    mask_binding_sha256,
    validate_edit_protected_contract,
    validate_target_authority_contract,
    verify_mask_lock,
)


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    annotated: Image.Image


def ensure_rgb(image: Image.Image) -> Image.Image:
    """Return an RGB copy with EXIF orientation applied and ICC profile preserved."""
    source_icc = image.info.get("icc_profile")
    image = image.copy()
    try:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    rgb = image.convert("RGB")
    if source_icc:
        rgb.info["icc_profile"] = source_icc
    return rgb


def _rational_to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        if isinstance(value, tuple) and len(value) == 2 and value[1]:
            return float(value[0]) / float(value[1])
        raise


def _gps_to_decimal(values: Iterable[Any], ref: str) -> float:
    degrees, minutes, seconds = [_rational_to_float(v) for v in values]
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if ref in {"S", "W"}:
        decimal *= -1
    return decimal


def read_photo_metadata(image: Image.Image) -> dict[str, str]:
    """Extract safe, human-readable EXIF metadata from an uploaded photo."""
    result: dict[str, str] = {}
    try:
        exif = image.getexif()
    except Exception:
        return result

    if not exif:
        return result

    tag_map = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        if key in tag_map:
            result["拍攝時間"] = str(tag_map[key])
            break

    if "Make" in tag_map or "Model" in tag_map:
        camera = " ".join(str(tag_map.get(k, "")).strip() for k in ("Make", "Model")).strip()
        if camera:
            result["裝置"] = camera

    gps_info = tag_map.get("GPSInfo")
    if gps_info:
        try:
            gps = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_info.items()}
            lat = _gps_to_decimal(gps["GPSLatitude"], str(gps["GPSLatitudeRef"]))
            lon = _gps_to_decimal(gps["GPSLongitude"], str(gps["GPSLongitudeRef"]))
            result["定位"] = f"{lat:.6f}, {lon:.6f}"
        except Exception:
            pass

    return result


def dominant_colours(image: Image.Image, count: int = 5) -> list[str]:
    """Return dominant colours as hex values without external ML dependencies."""
    img = ensure_rgb(image)
    img.thumbnail((240, 240))
    array = np.asarray(img, dtype=np.uint8).reshape(-1, 3)
    if len(array) == 0:
        return []

    # OpenCV k-means gives deterministic enough colour groups when seeded.
    cv2.setRNGSeed(42)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    k = max(1, min(count, len(array)))
    _, labels, centres = cv2.kmeans(
        np.float32(array), k, None, criteria, 5, cv2.KMEANS_PP_CENTERS
    )
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(counts)[::-1]
    return ["#%02x%02x%02x" % tuple(np.clip(centres[i], 0, 255).astype(int)) for i in order]


def basic_analysis(image: Image.Image) -> str:
    """Create a transparent baseline analysis that works without an AI service."""
    rgb = ensure_rgb(image)
    metadata = read_photo_metadata(image)
    colours = dominant_colours(rgb)
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    edge_ratio = float(np.count_nonzero(edges)) / float(edges.size or 1)

    if edge_ratio > 0.16:
        structure = "線條與細節較多"
    elif edge_ratio > 0.08:
        structure = "線條與細節中等"
    else:
        structure = "大面積表面較多"

    lines = [
        f"尺寸：{rgb.width} × {rgb.height} px",
        f"影像特徵：{structure}",
        f"主要色彩：{'、'.join(colours) if colours else '無法判讀'}",
    ]
    for label, value in metadata.items():
        lines.append(f"{label}：{value}")
    if "定位" not in metadata:
        lines.append("定位：照片未包含可讀取的 GPS，或已被移除")
    lines.append("說明：目前的離線分析只包含影像與 EXIF 特徵；物件語意辨識需設定 HF_TOKEN。")
    return "\n".join(lines)


def annotate_detections(image: Image.Image, detections: list[Any]) -> Image.Image:
    rgb = ensure_rgb(image)
    canvas = rgb.copy()
    draw = ImageDraw.Draw(canvas)
    line_width = max(2, round(min(rgb.size) / 240))
    for item in detections:
        box = getattr(item, "box", None)
        if box is None and isinstance(item, dict):
            box = item.get("box")
        label = getattr(item, "label", None) or (item.get("label") if isinstance(item, dict) else "物件")
        score = getattr(item, "score", None)
        if score is None and isinstance(item, dict):
            score = item.get("score")
        try:
            xmin = int(getattr(box, "xmin", box["xmin"]))
            ymin = int(getattr(box, "ymin", box["ymin"]))
            xmax = int(getattr(box, "xmax", box["xmax"]))
            ymax = int(getattr(box, "ymax", box["ymax"]))
        except Exception:
            continue
        draw.rectangle((xmin, ymin, xmax, ymax), outline=(20, 20, 20), width=line_width)
        text = f"{label} {float(score):.0%}" if score is not None else str(label)
        text_box = draw.textbbox((xmin, ymin), text)
        pad = 4
        draw.rectangle(
            (text_box[0] - pad, text_box[1] - pad, text_box[2] + pad, text_box[3] + pad),
            fill=(255, 255, 255),
        )
        draw.text((xmin, ymin), text, fill=(0, 0, 0))
    return canvas


def analyze_image(image: Image.Image | None) -> tuple[str, Image.Image | None]:
    if image is None:
        return "請先上傳照片。", None

    rgb = ensure_rgb(image)
    baseline = basic_analysis(image)
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        return baseline, rgb

    try:
        from huggingface_hub import InferenceClient

        model = os.getenv("DETECTION_MODEL", "").strip() or None
        client = InferenceClient(provider="auto", token=token, timeout=90)
        detections = client.object_detection(rgb, model=model, threshold=0.35)
        annotated = annotate_detections(rgb, list(detections))
        if detections:
            objects = "、".join(
                f"{getattr(d, 'label', 'object')} ({float(getattr(d, 'score', 0)):.0%})"
                for d in detections[:12]
            )
            summary = baseline.replace(
                "說明：目前的離線分析只包含影像與 EXIF 特徵；物件語意辨識需設定 HF_TOKEN。",
                f"AI 物件辨識：{objects}\n說明：辨識框需要使用者確認，不能視為施工或測量依據。",
            )
            return summary, annotated
        return baseline + "\nAI 物件辨識：未找到高於門檻的物件。", annotated
    except Exception as exc:
        return baseline + f"\nAI 物件辨識暫時失敗：{type(exc).__name__}: {exc}", rgb


def mask_from_editor(editor_value: dict[str, Any] | None) -> tuple[Image.Image | None, Image.Image | None]:
    """Extract background and a single union mask from a Gradio ImageEditor value."""
    if not editor_value:
        return None, None
    background = editor_value.get("background")
    if background is None:
        return None, None
    background = ensure_rgb(background)
    mask = np.zeros((background.height, background.width), dtype=np.uint8)

    for layer in editor_value.get("layers") or []:
        if layer is None:
            continue
        layer_img = layer if isinstance(layer, Image.Image) else Image.fromarray(np.asarray(layer))
        if layer_img.size != background.size:
            raise ValueError("Editor layer and source must have identical dimensions.")
        if "A" in layer_img.getbands():
            alpha = np.asarray(layer_img.getchannel("A"), dtype=np.uint8)
            mask = np.maximum(mask, alpha)
        else:
            layer_array = np.asarray(layer_img.convert("RGB"), dtype=np.uint8)
            non_white = np.any(layer_array < 245, axis=2).astype(np.uint8) * 255
            mask = np.maximum(mask, non_white)

    # If no explicit layer is available, infer edited pixels from the composite.
    if not np.any(mask) and editor_value.get("composite") is not None:
        composite = ensure_rgb(editor_value["composite"])
        if composite.size != background.size:
            raise ValueError("Editor composite and source must have identical dimensions.")
        delta = np.max(
            np.abs(np.asarray(composite, dtype=np.int16) - np.asarray(background, dtype=np.int16)),
            axis=2,
        )
        mask = np.where(delta > 8, 255, 0).astype(np.uint8)

    mask_img = Image.fromarray(mask, mode="L")
    return background, mask_img


def _seed_from_text(text: str) -> int:
    value = 2166136261
    for char in text:
        value ^= ord(char)
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def procedural_texture(size: tuple[int, int], style: str, prompt: str) -> Image.Image:
    """Create lightweight material previews for demo mode."""
    width, height = size
    seed = _seed_from_text(f"{style}|{prompt}")
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]

    style_key = style.lower()
    if "木" in style_key:
        grain = np.sin(x / 10.0 + np.sin(y / 42.0) * 3.0) * 18
        noise = rng.normal(0, 7, (height, width))
        base = np.stack([165 + grain + noise, 122 + grain * 0.55 + noise, 78 + grain * 0.2], axis=2)
    elif "石" in style_key or "大理石" in style_key:
        vein = np.sin((x + y * 0.55) / 17.0 + np.sin(y / 24.0))
        fine = rng.normal(0, 10, (height, width))
        base = np.stack([214 + fine, 211 + fine, 205 + fine + vein * 12], axis=2)
    elif "混凝土" in style_key:
        noise = rng.normal(0, 13, (height, width))
        base = np.stack([155 + noise, 157 + noise, 158 + noise], axis=2)
    else:
        try:
            colour = ImageColor.getrgb(style)
        except Exception:
            colour = (200, 194, 181)
        noise = rng.normal(0, 3, (height, width, 1))
        base = np.array(colour, dtype=float).reshape(1, 1, 3) + noise

    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), mode="RGB")


def local_transform(
    image: Image.Image,
    mask: Image.Image,
    action: str,
    material: str,
    colour: str,
    prompt: str,
    strength: float,
) -> Image.Image:
    """Generate a reversible local preview when no remote AI token is configured."""
    rgb = ensure_rgb(image)
    mask = mask.resize(rgb.size).convert("L")
    mask_np = np.asarray(mask, dtype=np.uint8)
    if not np.any(mask_np > 8):
        raise ValueError("請先用筆刷標記需要修改的區域。")

    action_key = action.lower()
    if "移除" in action_key:
        bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
        binary = np.where(mask_np > 8, 255, 0).astype(np.uint8)
        radius = max(3, round(min(rgb.size) * 0.012))
        result = cv2.inpaint(bgr, binary, radius, cv2.INPAINT_TELEA)
        generated = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
    elif "材質" in action_key or "替換" in action_key:
        generated = procedural_texture(rgb.size, material, prompt)
    else:
        try:
            fill = Image.new("RGB", rgb.size, ImageColor.getrgb(colour))
        except Exception:
            fill = Image.new("RGB", rgb.size, (205, 199, 186))
        # Preserve luminance to make the result read as a surface material.
        luminance = rgb.convert("L").filter(ImageFilter.GaussianBlur(radius=1.2))
        generated = Image.blend(fill, Image.merge("RGB", (luminance, luminance, luminance)), 0.34)

    opacity = max(0.05, min(1.0, float(strength)))
    adjusted_mask = mask.point(lambda value: int(value * opacity))
    return Image.composite(generated, rgb, adjusted_mask)


def remote_generate(image: Image.Image, prompt: str) -> Image.Image:
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("未設定 HF_TOKEN。")
    from huggingface_hub import InferenceClient

    model = os.getenv("GENERATION_MODEL", "").strip() or None
    client = InferenceClient(provider="auto", token=token, timeout=180)
    result = client.image_to_image(
        ensure_rgb(image),
        prompt=prompt,
        negative_prompt="distorted architecture, warped walls, duplicate furniture, text, watermark",
        model=model,
        num_inference_steps=24,
        guidance_scale=6.5,
    )
    return ensure_rgb(result)


def generate_design(
    editor_value: dict[str, Any] | None,
    action: str,
    material: str,
    colour: str,
    prompt: str,
    strength: float,
    use_remote_ai: bool,
    protected_mask: Image.Image | np.ndarray | dict[str, Any] | None = None,
    target_authority_receipt: dict[str, Any] | None = None,
) -> tuple[Image.Image | None, tuple[Image.Image, Image.Image] | None, str, str | None]:
    output_path: str | None = None
    try:
        background, mask = mask_from_editor(editor_value)
        if background is None or mask is None:
            return None, None, "請先上傳照片並標記修改區域。", None
        if not np.any(np.asarray(mask) > 8):
            return None, None, "尚未偵測到筆刷標記。請在照片上塗選要修改的區域。", None

        source_icc = background.info.get("icc_profile")

        action_key = str(action).lower()
        if "移除" in action_key or "remove" in action_key:
            target_authority_qa = validate_target_authority_contract(
                target_authority_receipt,
                source_binding_sha256=image_binding_sha256(background),
                edit_mask_binding_sha256=mask_binding_sha256(mask),
            )
            if not target_authority_qa["hard_gate_pass"]:
                raise RuntimeError(
                    f"{target_authority_qa['hold_reason']}: "
                    "removal is held before image generation until a Current target "
                    "authority receipt is supplied."
                )

        normalized_protected_mask: Image.Image | np.ndarray | None = protected_mask
        if isinstance(protected_mask, dict):
            protected_background, normalized_protected_mask = mask_from_editor(protected_mask)
            if protected_background is not None and (
                protected_background.size != background.size
                or not np.array_equal(np.asarray(protected_background), np.asarray(background))
                or protected_background.info.get("icc_profile") != source_icc
            ):
                raise ValueError("Protected editor source must match the edit source pixel-for-pixel.")
            if (
                normalized_protected_mask is not None
                and not np.any(np.asarray(normalized_protected_mask) > 8)
            ):
                normalized_protected_mask = None

        if normalized_protected_mask is not None:
            protection_qa = validate_edit_protected_contract(mask, normalized_protected_mask)
            if not protection_qa["hard_gate_pass"]:
                raise RuntimeError(
                    "修改區與保護區重疊："
                    f"edit_protected_overlap_pixels={protection_qa['edit_protected_overlap_pixels']}"
                )

        if use_remote_ai:
            full_prompt = (
                "Architectural interior design edit. Preserve the original camera angle, geometry, "
                "windows, walls, and all unselected areas. "
                f"Requested action: {action}. Material: {material}. User instruction: {prompt or 'refine the selected area'}."
            )
            candidate = remote_generate(background, full_prompt).resize(background.size)
            result = Image.composite(candidate, background, mask)
            mode = "遠端 AI 生成"
        else:
            result = local_transform(background, mask, action, material, colour, prompt, strength)
            mode = "本機示意模式"

        # Final hard lock: the generator is only a candidate producer.
        # Re-composite against the canonical source so pixels outside the
        # binary edit mask are guaranteed bit-exact before the PNG is saved.
        locked = composite_mask_only(
            np.asarray(background),
            np.asarray(ensure_rgb(result)),
            mask,
        )
        result = Image.fromarray(locked.astype(np.uint8, copy=False), mode="RGB")
        if source_icc:
            result.info["icc_profile"] = source_icc
        lock_qa = verify_mask_lock(background, result, mask, normalized_protected_mask)
        if not lock_qa["hard_lock_pass"]:
            raise RuntimeError(
                "局部像素鎖定驗證失敗："
                f"edit_protected_overlap_pixels={lock_qa['edit_protected_overlap_pixels']}, "
                f"outside_changed_pixels={lock_qa['outside_changed_pixels']}, "
                f"outside_max_channel_diff={lock_qa['outside_max_channel_diff']}, "
                f"protected_changed_pixels={lock_qa['protected_changed_pixels']}, "
                f"inside_changed_pixels={lock_qa['inside_changed_pixels']}"
            )

        fd, output_path = tempfile.mkstemp(prefix="ai-space-generator-", suffix=".png")
        os.close(fd)
        save_options = {"icc_profile": source_icc} if source_icc else {}
        result.save(output_path, "PNG", **save_options)
        # Verify the actual deliverable, including export corruption and profile
        # loss. A successful in-memory check cannot authorize different bytes.
        with Image.open(output_path) as saved:
            saved.load()
            if saved.format != "PNG" or saved.size != background.size or saved.mode != result.mode:
                raise RuntimeError("PNG readback dimensions/format verification failed.")
            if saved.info.get("icc_profile") != source_icc:
                raise RuntimeError("PNG readback ICC profile verification failed.")
            saved_rgb = saved.convert("RGB")
            if not np.array_equal(np.asarray(saved_rgb), np.asarray(result)):
                raise RuntimeError("PNG readback pixel verification failed.")
            exported_qa = verify_mask_lock(background, saved_rgb, mask, normalized_protected_mask)
            if not exported_qa["hard_lock_pass"]:
                raise RuntimeError("PNG readback mask-lock verification failed.")
        status = (
            f"完成：{mode}。未選取區域維持原圖；結果屬概念示意，不作為施工、測量或結構判斷依據。"
        )
        return result, (background, result), status, output_path
    except Exception as exc:
        if output_path is not None:
            Path(output_path).unlink(missing_ok=True)
        return None, None, f"生成失敗：{type(exc).__name__}: {exc}", None
