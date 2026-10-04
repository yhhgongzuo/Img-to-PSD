"""Reject geometry/calibration traps before opening Photoshop; no image generation."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image
from asset_ops import inspect_alpha


def layers(groups):
    for group in groups:
        yield from group.get('layers', [])
        yield from layers(group.get('groups', []))


def rgb_offset_points(delta):
    if not isinstance(delta, (int, float)) or not math.isfinite(delta) or abs(delta) > 255:
        raise ValueError('RGB offset must be finite and within -255..255')
    return [[x, max(0, min(255, x+delta))] for x in [0, 32, 64, 96, 128, 160, 192, 224, 255]]


def similarity(matrix):
    m = np.asarray(matrix, dtype=float)
    if m.shape != (2, 3) or not np.isfinite(m).all():
        return False
    a = m[:, :2]
    sv = np.linalg.svd(a, compute_uv=False)
    return bool(np.linalg.det(a) > 0 and sv[1] > 0 and abs(sv[0]/sv[1]-1) < 1e-4)


def validate_scene(spec):
    errors = []
    size = (spec['width'], spec['height'])
    exports = set()
    patches = [i for i in layers(spec['groups']) if i.get('patch')]
    if spec.get('basePsd'):
        if not Path(spec['basePsd']).is_absolute() or not Path(spec['basePsd']).is_file():
            errors.append('basePsd requires an existing absolute PSD path')
        if not patches: errors.append('basePsd requires at least one patch image')
    elif patches: errors.append('patch requires basePsd')
    for item in layers(spec['groups']):
        label = item['name']
        def fail(message): errors.append(label+': '+message)
        if item.get('patch') and item['type'] != 'image': fail('only existing image layers support patch')
        if 'frame' in item:
            fail('frame fitting is disabled; supply registered full-canvas images')
        if 'registration' in item and not similarity(item['registration']):
            fail('registration must be a similarity transform (no XY warp/shear/reflection)')
        if item['type'] == 'image':
            with Image.open(item['path']) as im:
                if im.size != size: fail('image must match full scene canvas')
                if item.get('role') == 'object':
                    try: inspect_alpha(im)
                    except ValueError as ex: fail(str(ex))
        if 'rgbCurves' in item: fail('use rgbOffsets; arbitrary sparse calibration curves are unsupported')
        if 'rgbOffsets' in item:
            offsets = item['rgbOffsets']
            if not isinstance(offsets, list) or len(offsets) != 3:
                fail('rgbOffsets requires three channel offsets')
            else:
                for delta in offsets:
                    try: rgb_offset_points(delta)
                    except ValueError as ex: fail(str(ex))
        if ('rgbOffsets' in item or item.get('hueAdjustments')) and item['type'] != 'image':
            fail('color smart filters require an image smart object')
        for adj in item.get('hueAdjustments', []):
            if adj.get('channel') not in range(1, 7): fail('selective hue requires local channel 1..6')
            r = adj.get('range', [])
            if len(r) != 4 or any(not isinstance(v, int) or not 0 <= v <= 360 for v in r):
                fail('selective hue requires four explicit range angles')
            for key, limit in [('hue', 180), ('saturation', 100), ('lightness', 100)]:
                value = adj.get(key, 0)
                if not isinstance(value, int) or abs(value) > limit: fail('invalid '+key)
        if item.get('exportPng'):
            p = Path(item['exportPng'])
            if not p.is_absolute() or p.suffix.lower() != '.png': fail('exportPng needs an absolute PNG path')
            normalized = str(p.resolve()).casefold()
            if normalized in exports: fail('duplicate export path')
            exports.add(normalized)
            if item.get('clipped') or item.get('blend', 'NORMAL') != 'NORMAL' or item.get('opacity', 100) != 100:
                fail('isolated asset export requires unclipped normal layer at 100% opacity')
        if item.get('alphaBaseline') and not item.get('exportPng'):
            fail('alphaBaseline requires a final native exportPng')
    return errors


def compare_alpha(before, after):
    with Image.open(before) as a, Image.open(after) as b:
        inspect_alpha(a); inspect_alpha(b)
        return a.size == b.size and np.array_equal(np.asarray(a.getchannel('A')), np.asarray(b.getchannel('A')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene')
    args = parser.parse_args()
    issues = validate_scene(json.loads(Path(args.scene).read_text(encoding='utf-8-sig')))
    print(json.dumps({'ok': not issues, 'issues': issues}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(issues))
