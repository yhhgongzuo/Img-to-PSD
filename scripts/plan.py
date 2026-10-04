"""Validate manually observed coverage/occlusion; never infer geometry from boxes."""
import argparse
import json
from collections import Counter
from pathlib import Path


def validate(m):
    errors = []
    elements = m['elements']
    ids = [e['id'] for e in elements]
    if len(ids) != len(set(ids)):
        errors.append('Duplicate element IDs')
    components = [c for e in elements for c in e['components']]
    if len(components) != len(set(components)):
        errors.append('Duplicate component IDs')
    routes = {'generated', 'generated-background', 'native-text', 'native-shape', 'effect'}
    for e in elements:
        if e['route'] not in routes or not e['components']:
            errors.append('Invalid route/components: ' + e['id'])
        if e.get('shadow') not in ('pair', 'none'):
            errors.append('Declare shadow pair/none: ' + e['id'])
        if e.get('shadow') == 'none' and not e.get('shadow_reason'):
            errors.append('Explain no shadow: ' + e['id'])
        if e.get('shadow') == 'pair' and not e.get('receiver'):
            errors.append('Missing receiver: ' + e['id'])
    expected = {e['id'] for e in elements if e['route'].startswith('generated')}
    batches = m['batches']
    if len({b['id'] for b in batches}) != len(batches):
        errors.append('Duplicate batch IDs')
    assignments = Counter(i for b in batches for i in b['elements'])
    if set(assignments) != expected:
        errors.append(f'Batch coverage missing={sorted(expected-set(assignments))}, extra={sorted(set(assignments)-expected)}')
    if any(n != 1 for n in assignments.values()):
        errors.append('Elements assigned more than once')
    conflicts = m['overlaps']
    for pair in conflicts:
        if len(pair) != 2 or pair[0] == pair[1] or not set(pair) <= set(ids):
            errors.append('Invalid overlap: ' + str(pair))
    for b in batches:
        if not b['elements'] or not b.get('background') or not b.get('color_reason'):
            errors.append('Batch needs members/background/color_reason: ' + b['id'])
        for pair in conflicts:
            if set(pair) <= set(b['elements']):
                errors.append('Occlusion conflict in ' + b['id'] + ': ' + str(pair))
        bg = [e for e in elements if e['id'] in b['elements'] and e['route'] == 'generated-background']
        if bg and len(b['elements']) != 1:
            errors.append('Scene background must have its own batch')
    return errors


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest')
    args = ap.parse_args()
    m = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    issues = validate(m)
    print(json.dumps({'ok': not issues, 'elements': len(m['elements']),
                      'components': sum(len(e['components']) for e in m['elements']),
                      'shadow_layers': 2*sum(e['shadow'] == 'pair' for e in m['elements']),
                      'expected_content_layers_excluding_reference': sum(len(e['components']) + (2 if e['shadow'] == 'pair' else 0) for e in m['elements']),
                      'batches': len(m['batches']), 'issues': issues}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(issues))
