#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, subprocess, sys, tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_inside(root: Path, rel: str) -> Path:
    p=(root/rel).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f'Path escapes bundle root: {rel}')
    if not p.is_file():
        raise FileNotFoundError(rel)
    return p


def assert_hash(path: Path, expected: str, label: str):
    actual=sha256(path)
    if actual != expected:
        raise ValueError(f'{label} sha256 mismatch: expected {expected}, got {actual}')
    return actual


def import_verify(path: Path):
    spec=importlib.util.spec_from_file_location('hao_case_verify_final', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot import pixel verifier')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_payload_manifest(root: Path, manifest_path: Path) -> dict:
    payload = load_json(manifest_path)
    records = payload.get('files')
    if not isinstance(records, list):
        raise ValueError('payload manifest must contain a files list')
    checked = 0
    checked_sha256 = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('payload manifest records must be mappings')
        rel = record.get('path')
        expected = record.get('sha256')
        if not isinstance(rel, str) or not rel.strip() or not isinstance(expected, str) or not expected.strip():
            raise ValueError('payload manifest record missing path/sha256')
        path = resolve_inside(root, rel)
        actual = assert_hash(path, expected, f'payload:{rel}')
        normalized_rel = path.relative_to(root).as_posix()
        if normalized_rel in checked_sha256:
            raise ValueError(f'duplicate payload path after normalization: {normalized_rel}')
        checked_sha256[normalized_rel] = actual
        checked += 1
    expected_count = payload.get('payload_file_count')
    if expected_count is not None and expected_count != checked:
        raise ValueError(f'payload count mismatch: expected {expected_count}, checked {checked}')
    return {
        'path': str(manifest_path),
        'checked_files': checked,
        'checked_sha256': checked_sha256,
        'pass': True,
    }


def resolve_bound_executable(root: Path, spec, label: str, payload_fixity: dict | None) -> Path:
    """Resolve executable bundle code only when its bytes are fixity-bound."""
    explicit_sha = None
    if isinstance(spec, str):
        rel = spec
    elif isinstance(spec, dict):
        rel = spec.get('path')
        explicit_sha = spec.get('sha256')
    else:
        raise ValueError(f'{label} must be a path string or mapping')

    if not isinstance(rel, str) or not rel.strip():
        raise ValueError(f'{label} missing path')
    path = resolve_inside(root, rel)

    if explicit_sha is not None:
        if not isinstance(explicit_sha, str) or not explicit_sha.strip():
            raise ValueError(f'{label} sha256 must be a nonempty string')
        assert_hash(path, explicit_sha, label)
        return path

    if not payload_fixity or not payload_fixity.get('pass'):
        raise ValueError(
            f'{label} executable bytes are not fixity-bound: '
            'provide an explicit sha256 or a verified payload_manifest entry'
        )
    checked = payload_fixity.get('checked_sha256')
    if not isinstance(checked, dict):
        raise ValueError(f'{label} payload fixity index missing')
    normalized_rel = path.relative_to(root).as_posix()
    if normalized_rel not in checked:
        raise ValueError(
            f'{label} executable bytes are not listed in the verified payload_manifest: {normalized_rel}'
        )
    if sha256(path) != checked[normalized_rel]:
        raise ValueError(f'{label} changed after payload manifest verification')
    return path


def find_sha_match(root: Path, expected_sha: str) -> list[Path]:
    matches=[]
    for p in root.rglob('*'):
        if p.is_file():
            try:
                if sha256(p)==expected_sha:
                    matches.append(p.resolve())
            except OSError:
                pass
    return matches


def main() -> int:
    ap=argparse.ArgumentParser(description='Manifest-driven verifier/replay for a masked local-edit case.')
    ap.add_argument('manifest', type=Path)
    ap.add_argument('--replay', action='store_true', help='Also run packaged deterministic replay in a fresh output directory.')
    ap.add_argument('--output', type=Path, help='Write machine-readable result JSON.')
    args=ap.parse_args()

    result={'schema':'hao.masked-local-edit-case-result.v1','pass':False}
    try:
        m=load_json(args.manifest.resolve())
        manifest_dir=args.manifest.resolve().parent
        configured_root=Path(m.get('bundle_root', '.'))
        root=(configured_root if configured_root.is_absolute() else manifest_dir/configured_root).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f'bundle_root missing: {root}')

        payload_fixity = None
        if m.get('payload_manifest'):
            payload_manifest = resolve_inside(root, m['payload_manifest'])
            payload_fixity = validate_payload_manifest(root, payload_manifest)

        source=resolve_inside(root,m['source']['path'])
        mask=resolve_inside(root,m['editable_mask']['path'])
        protected=resolve_inside(root,m['protected_mask']['path'])
        final=resolve_inside(root,m['final']['path'])
        verifier=resolve_bound_executable(root,m['pixel_verifier'],'pixel_verifier',payload_fixity)
        regions={k:resolve_inside(root,v) for k,v in m.get('regions',{}).items()}
        hashes={
          'source':assert_hash(source,m['source']['sha256'],'source'),
          'editable_mask':assert_hash(mask,m['editable_mask']['sha256'],'editable_mask'),
          'protected_mask':assert_hash(protected,m['protected_mask']['sha256'],'protected_mask'),
          'final':assert_hash(final,m['final']['sha256'],'final'),
        }

        mod=import_verify(verifier)
        qa=mod.verify(source,mask,final,regions,m['source']['sha256'])
        expected=m['expected']
        pm=qa['pixel_metrics']
        technical_ok=(qa['technical_preservation_pass'] == expected['technical_preservation_pass']
                      and pm['outside_changed_pixels'] == expected['outside_changed_pixels']
                      and pm['outside_max_channel_difference'] == expected['outside_max_channel_difference'])

        from PIL import Image
        import numpy as np
        src=np.asarray(Image.open(source).convert('RGB'))
        fin=np.asarray(Image.open(final).convert('RGB'))
        prot=np.asarray(Image.open(protected).convert('L'))>0
        protected_changed=int(np.count_nonzero(np.any(src != fin, axis=2) & prot))
        technical_ok = technical_ok and protected_changed == expected['protected_changed_pixels']

        result.update({
          'case_id':m['case_id'],
          'bundle_root':str(root),
          'inputs':{'source_path':str(source),'editable_mask_path':str(mask),'protected_mask_path':str(protected),'final_path':str(final)},
          'hashes':hashes,
          'payload_fixity':payload_fixity,
          'pixel_verification':{
            'technical_preservation_pass':qa['technical_preservation_pass'],
            'outside_changed_pixels':pm['outside_changed_pixels'],
            'outside_max_channel_difference':pm['outside_max_channel_difference'],
            'protected_changed_pixels':protected_changed,
            'pass':technical_ok
          }
        })

        replay_ok=True
        if args.replay:
            replay_script=resolve_bound_executable(root,m['replay']['script'],'replay.script',payload_fixity)
            output_dir=Path(tempfile.mkdtemp(prefix='hao-case-replay-', dir=str(args.manifest.resolve().parent))).resolve()
            output_dir.rmdir()
            cp=subprocess.run([sys.executable,str(replay_script),'--bundle-root',str(root),'--output-dir',str(output_dir)],
                              text=True,capture_output=True)
            if cp.returncode != 0:
                raise RuntimeError(f'replay failed ({cp.returncode}): {cp.stderr[-2000:]}')
            receipt_path=resolve_inside(output_dir,m['replay']['receipt_name'])
            receipt=load_json(receipt_path)
            matches=find_sha_match(output_dir,m['replay']['expected_final_sha256'])
            if len(matches) != 1:
                raise RuntimeError(f'expected exactly one replay output matching final sha; found {len(matches)}: {[str(p) for p in matches]}')
            actual_output=matches[0]
            replay_ok=(receipt['generation_calls']==expected['generation_calls']
                       and receipt['network_calls']==expected['network_calls']
                       and receipt['encoded_png_matches_original_v3']==expected['encoded_png_matches_original_v3']
                       and receipt['decoded_pixels_match_original_v3']==expected['decoded_pixels_match_original_v3']
                       and receipt['final_sha256']==m['replay']['expected_final_sha256']
                       and sha256(actual_output)==m['replay']['expected_final_sha256'])
            result['replay']={
              'command':[sys.executable,str(replay_script),'--bundle-root',str(root),'--output-dir',str(output_dir)],
              'output_dir':str(output_dir),
              'actual_output_path':str(actual_output),
              'actual_output_sha256':sha256(actual_output),
              'receipt_path':str(receipt_path),
              'generation_calls':receipt['generation_calls'],
              'network_calls':receipt['network_calls'],
              'encoded_png_matches_original_v3':receipt['encoded_png_matches_original_v3'],
              'decoded_pixels_match_original_v3':receipt['decoded_pixels_match_original_v3'],
              'pass':replay_ok
            }
        result['pass']=bool(technical_ok and replay_ok)
    except Exception as e:
        result['error_type']=type(e).__name__
        result['error']=str(e)
        result['pass']=False

    encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(encoded,encoding='utf-8')
    print(encoded,end='')
    return 0 if result['pass'] else 1

if __name__=='__main__':
    raise SystemExit(main())
