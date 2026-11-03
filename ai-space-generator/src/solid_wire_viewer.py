"""Offline HTML viewer of a real 3D mesh in solid shade + topology wire + perspective.

Optional dependencies: pip install trimesh plotly numpy
This is a viewer, not reconstruction or metrological calibration.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import trimesh
import plotly.graph_objects as go


def make_viewer(model_file: Path, output_html: Path, cameras_file: Path | None = None):
    scene = trimesh.load(model_file, force='scene')
    fig = go.Figure()
    models, wire_ids = [], []
    colors = ['#c1c1c1', '#b5b5b5', '#d0d0d0', '#aaa9a9', '#919191']
    all_points=[]
    for node in scene.graph.nodes_geometry:
        transform, mesh_name = scene.graph[node]
        mesh = scene.geometry[mesh_name]
        v = trimesh.transform_points(mesh.vertices, transform)
        f = mesh.faces
        e = mesh.edges_unique
        all_points.append(v)
        models.append(len(fig.data))
        fig.add_trace(go.Mesh3d(x=v[:,0], y=v[:,1], z=v[:,2],
            i=f[:,0], j=f[:,1], k=f[:,2], name=mesh_name,
            color=colors[len(models)%len(colors)], flatshading=True,
            lighting=dict(ambient=0.78, diffuse=0.52, specular=0.02, roughness=1),
            showscale=False, hoverinfo='skip', showlegend=False))
        lines = np.full((len(e),3,3), np.nan, dtype=float)
        lines[:,:2,:] = v[e]
        xyz = lines.reshape(-1,3)
        wire_ids.append(len(fig.data))
        fig.add_trace(go.Scatter3d(x=xyz[:,0],y=xyz[:,1],z=xyz[:,2],
            mode='lines',line=dict(color='rgba(45,49,54,0.65)',width=1.1),
            hoverinfo='skip',showlegend=False, name=mesh_name+' wire'))
    pts=np.vstack(all_points)
    mid=(pts.max(axis=0)+pts.min(axis=0))/2
    half=np.maximum((pts.max(axis=0)-pts.min(axis=0))/2,0.01)
    def pose(position, lookat):
        pos=np.asarray(position,dtype=float)
        target=np.asarray(lookat,dtype=float)
        eye=(pos-mid)/half
        aim=(target-mid)/half
        return dict(eye=dict(x=float(eye[0]),y=float(eye[1]),z=float(eye[2])),
            center=dict(x=float(aim[0]),y=float(aim[1]),z=float(aim[2])),
            up=dict(x=0,y=0,z=1),projection=dict(type='perspective'))
    presets=[]
    if cameras_file is not None:
        config=json.loads(cameras_file.read_text(encoding='utf-8'))
        for key,v in config['cameras'].items():
            presets.append((key,pose(v['position'],v['lookat'])))
    if not presets:
        presets=[('Overview',dict(eye=dict(x=2,y=-2,z=1.3),up=dict(x=0,y=0,z=1),projection=dict(type='perspective')))]
    withwire=[True]*len(fig.data)
    nowire=[i in models for i in range(len(fig.data))]
    fig.update_layout(
        paper_bgcolor='#f5f6f7',margin=dict(l=0,r=0,b=0,t=80),
        title=dict(text='Solid Shaded Mesh + Wireframe Overlay · Perspective View',x=0.5),
        scene=dict(aspectmode='data',xaxis=dict(visible=False),
            yaxis=dict(visible=False),zaxis=dict(visible=False),
            camera=presets[0][1],bgcolor='#eff0f2'),
        updatemenus=[
            dict(type='buttons',direction='right',x=0.0,y=1.11,
                buttons=[dict(label='Solid + Wire',method='restyle',args=[{'visible':withwire}]),
                         dict(label='Solid only',method='restyle',args=[{'visible':nowire}])]),
            dict(type='buttons',direction='right',x=0.0,y=1.03,
                buttons=[dict(label=k.split('_')[0],method='relayout',args=[{'scene.camera':v}])
                         for k,v in presets])],
    )
    output_html.parent.mkdir(parents=True,exist_ok=True)
    fig.write_html(str(output_html),include_plotlyjs=True,full_html=True,auto_open=False)
    return {'scene_meshes':len(models),'total_mesh_faces':sum(len(scene.geometry[n].faces) for n in scene.geometry),
            'topology_wire_traces':len(wire_ids),'camera_presets':len(presets),'html_bytes':output_html.stat().st_size}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cameras',type=Path)
    args=ap.parse_args()
    print(json.dumps(make_viewer(args.model,args.output,args.cameras),indent=2))
if __name__=='__main__': main()
