import cv2
import numpy as np

from src.material_realism import inside_contact_occlusion, perspective_prefilter


def test_perspective_prefilter_reduces_far_high_frequency_more_than_near():
    h, w = 220, 320
    yy, xx = np.indices((h, w))
    checker = (((xx // 2 + yy // 2) % 2) * 90 + 100).astype(np.uint8)
    rgb = np.repeat(checker[..., None], 3, axis=2)
    edit = np.zeros((h, w), dtype=bool)
    edit[70:, 35:285] = True

    out = perspective_prefilter(rgb, edit)
    gray = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY).astype(np.float32)
    hf = np.abs(cv2.Laplacian(gray, cv2.CV_32F))

    far = edit & (np.indices(edit.shape)[0] < 110)
    near = edit & (np.indices(edit.shape)[0] > 180)
    assert float(np.mean(hf[far])) < float(np.mean(hf[near]))


def test_contact_occlusion_is_inside_only_and_subtle():
    edit = np.zeros((100, 140), dtype=bool)
    edit[30:95, 20:125] = True
    mult = inside_contact_occlusion(edit, strength=0.012, width_px=1.55)

    assert np.all(mult[~edit] == 1.0)
    assert float(mult[edit].min()) >= 0.988
    assert float(mult[edit].max()) <= 1.0
