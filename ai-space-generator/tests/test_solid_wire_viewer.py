"""Optional 3D viewer tests; skip when 3D extras are not installed."""
import pytest
pytest.importorskip('plotly')
trimesh = pytest.importorskip('trimesh')

from src.solid_wire_viewer import make_viewer


def test_solid_wire_perspective_html_is_self_contained(tmp_path):
    model = tmp_path / 'box.glb'
    trimesh.Scene(trimesh.creation.box()).export(model)
    output = tmp_path / 'preview.html'
    stats = make_viewer(model, output)
    html = output.read_text(encoding='utf-8')
    assert stats['scene_meshes'] == 1
    assert stats['total_mesh_faces'] == 12
    assert stats['topology_wire_traces'] == 1
    assert 'Wireframe Overlay' in html
    assert 'perspective' in html
    assert 'Solid only' in html
    assert len(html) > 1000000
