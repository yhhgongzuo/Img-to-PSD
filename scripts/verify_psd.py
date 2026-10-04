"""Structural/reopen checks; a passing report does not replace visual QA."""
import argparse
import json
from collections import Counter
from pathlib import Path
from PIL import Image, ImageChops, ImageOps, ImageStat
from psd_tools import PSDImage
from asset_ops import inspect_alpha
from plan import validate
from preflight import validate_scene, compare_alpha, layers


def verify(spec, manifest):
    issues = validate(manifest) + validate_scene(spec)
    psd = PSDImage.open(spec['outputPsd'])
    if psd.size != (spec['width'], spec['height']):
        issues.append('PSD canvas differs from scene')
    source = ImageOps.exif_transpose(Image.open(spec['sourceImage']))
    scale = min(1, 4096/max(source.size))
    expected_size = tuple(max(1, round(n*scale)) for n in source.size)
    if psd.size != expected_size:
        issues.append('Canvas does not follow source/4096 rule')
    elements = {e['id']: e for e in manifest['elements']}
    expected_components = {c: e for e in manifest['elements'] for c in e['components']}
    seen = Counter(); inventory = []; pair_groups = Counter()

    def check(parent, groups, prefix='', parent_visible=True):
        for conf in groups:
            matches = [l for l in parent if l.name == conf['name'] and l.is_group()]
            if len(matches) != 1:
                issues.append('Missing/duplicate group: ' + conf['name']); continue
            group = matches[0]
            visible = parent_visible and conf.get('visible', True)
            if group.visible != conf.get('visible', True):
                issues.append('Group visibility differs: ' + group.name)
            if len(group) != len(conf.get('layers', []))+len(conf.get('groups', [])):
                issues.append('Unexpected children: ' + group.name)
            roles = []
            for item in conf.get('layers', []):
                matches = [l for l in group if l.name == item['name'] and not l.is_group()]
                if len(matches) != 1:
                    issues.append('Missing/duplicate layer: ' + item['name']); continue
                layer = matches[0]; role = item.get('role'); roles.append(role)
                effective = visible and item.get('visible', True)
                if layer.visible != item.get('visible', True):
                    issues.append('Layer visibility differs: ' + layer.name)
                if item['type'] == 'text':
                    if layer.kind != 'type' or layer.text != item['text']:
                        issues.append('Text content/editability: ' + layer.name)
                elif item['type'] == 'image' or item.get('blur'):
                    if layer.kind != 'smartobject' or layer.smart_object.kind != 'data':
                        issues.append('Expected embedded smart object: ' + layer.name)
                elif item['type'] == 'fill' and layer.kind not in ('shape', 'solidcolor'):
                    issues.append('Expected native shape/fill: ' + layer.name)
                if item.get('path') and Path(item['path']).resolve() == Path(spec['sourceImage']).resolve():
                    if effective or role != 'reference':
                        issues.append('Original image must be hidden reference: ' + layer.name)
                if role == 'reference' and effective:
                    issues.append('Visible reference: ' + layer.name)
                cid = item.get('component_id')
                if cid:
                    seen[cid] += 1
                    e = expected_components.get(cid)
                    if e is None:
                        issues.append('Unknown component: ' + cid)
                    elif e['route'] == 'native-text' and layer.kind != 'type':
                        issues.append('Manifest text was rasterized: ' + cid)
                    elif e['route'] == 'native-shape' and layer.kind not in ('shape', 'solidcolor'):
                        issues.append('Manifest shape was rasterized: ' + cid)
                    elif e['route'] == 'generated':
                        if item['type'] != 'image':
                            issues.append('Generated component needs image: ' + cid)
                        else:
                            try: inspect_alpha(Image.open(item['path']))
                            except ValueError as ex: issues.append(cid + ': ' + str(ex))
                        if layer.has_mask():
                            issues.append('Unexpected object cutout mask: ' + cid)
                    if not effective:
                        issues.append('Required component hidden: ' + cid)
                inventory.append({'name': layer.name, 'group': prefix+'/'+group.name,
                                  'kind': layer.kind, 'component_id': cid, 'role': role})
            eid = conf.get('element_id')
            if eid and eid in elements and elements[eid]['shadow'] == 'pair':
                pair_groups[eid] += 1
                if roles.count('contact-shadow') != 1 or roles.count('soft-shadow') != 1:
                    issues.append('Missing exact shadow pair: ' + eid)
                if not set(elements[eid]['components']) <= {i.get('component_id') for i in conf.get('layers', [])}:
                    issues.append('Object and shadows must share movable group: ' + eid)
                ordered = list(group)
                indices = {layer.name: n for n, layer in enumerate(ordered)}
                by_role = {i.get('role'): i['name'] for i in conf.get('layers', [])}
                if 'contact-shadow' in by_role and 'soft-shadow' in by_role:
                    ci = indices.get(by_role['contact-shadow'], -1)
                    si = indices.get(by_role['soft-shadow'], -1)
                    bodies = [indices.get(i['name'], -1) for i in conf.get('layers', []) if i.get('component_id') in elements[eid]['components']]
                    # psd-tools iterates from bottom to top (Photoshop DOM is opposite).
                    if si < 0 or ci <= si or any(n <= ci for n in bodies):
                        issues.append('Expected body above contact above soft shadow: ' + eid)
            check(group, conf.get('groups', []), prefix+'/'+group.name, visible)

    if len(psd) != len(spec['groups']): issues.append('Unexpected root groups')
    check(psd, spec['groups'])
    if set(seen) != set(expected_components) or any(n != 1 for n in seen.values()):
        issues.append('Component coverage missing/duplicate/extra: ' + str(sorted(set(expected_components)-set(seen))))
    for e in elements.values():
        if e['shadow'] == 'pair' and pair_groups[e['id']] != 1:
            issues.append('Expected one movable shadow group: ' + e['id'])
    cached = psd.topil().convert('RGB'); reopened = Image.open(spec['qaPng']).convert('RGB')
    difference = None
    if cached.size != reopened.size:
        issues.append('Reopen canvas mismatch')
    else:
        difference = ImageStat.Stat(ImageChops.difference(cached, reopened)).mean
        if max(difference) > 1: issues.append('Reopen composite mismatch')
    for view in spec.get('qaViews', []):
        if not Path(view['path']).is_file(): issues.append('Missing QA view: '+view['path'])
    for item in layers(spec['groups']):
        if not item.get('exportPng'): continue
        try:
            with Image.open(item['exportPng']) as im:
                inspect_alpha(im)
                if im.size != psd.size: issues.append('Export lost full canvas: '+item['name'])
            if item.get('alphaBaseline') and not compare_alpha(item['alphaBaseline'], item['exportPng']):
                issues.append('Color-only edit changed alpha: '+item['name'])
        except (ValueError, OSError) as ex: issues.append(item['name']+': '+str(ex))
    return {'ok': not issues, 'issues': issues, 'size': list(psd.size),
            'content_layers': len(inventory), 'types': dict(Counter(x['kind'] for x in inventory)),
            'reopen_mean_RGB_error': difference, 'inventory': inventory,
            'visual_review': 'Required separately: original comparison, edges, receivers, shadows'}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('scene'); ap.add_argument('manifest'); ap.add_argument('report')
    a = ap.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
    report = verify(read(a.scene), read(a.manifest))
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'inventory'}, ensure_ascii=False, indent=2))
    raise SystemExit(not report['ok'])
