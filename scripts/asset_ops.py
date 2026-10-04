"""Technical operations ONLY on newly generated assets, never original-photo cutouts."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

KEYS = {'magenta': (255, 0, 255), 'green': (0, 255, 0),
        'cyan': (0, 255, 255), 'blue': (0, 0, 255),
        'yellow': (255, 255, 0), 'red': (255, 0, 0)}


def score(rgb, key):
    channels = np.array(KEYS[key]) > 0
    return np.min(rgb[..., channels], axis=-1) - np.max(rgb[..., ~channels], axis=-1)


def key_options(samples):
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 3 or not len(arr) or np.any((arr < 0) | (arr > 255)):
        raise ValueError('Supply representative FOREGROUND RGB samples, values 0..255')
    # Conservative: any appreciable key chroma in a sampled subject is a conflict.
    return [k for k in KEYS if float(score(arr / 255, k).max()) <= .04]


def inspect_alpha(im):
    if 'A' not in im.getbands():
        raise ValueError('No alpha channel; PNG/checkerboard is not transparency')
    a = np.asarray(im.getchannel('A'))
    if a.min() != 0 or a.max() == 0:
        raise ValueError('Expected real transparent background and nonempty foreground')
    return {'mode': im.mode, 'size': list(im.size), 'extrema': [int(a.min()), int(a.max())],
            'clear': int((a == 0).sum()), 'opaque': int((a == 255).sum()),
            'partial': int(((a > 0) & (a < 255)).sum())}


def matte(im, key, samples):
    if key not in key_options(samples):
        raise ValueError('Foreground conflicts with key; select another key/batch/native alpha')
    if 'A' in im.getbands() and im.getchannel('A').getextrema()[0] < 255:
        raise ValueError('Already has alpha: inspect and preserve it instead of rematting')
    rgb = np.asarray(im.convert('RGB'), dtype=np.float32) / 255
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    qborder = score(border, key)
    # Original-frame clipped objects may occupy the border. Estimate from key candidates.
    candidates = border[qborder > .5]
    if len(candidates) < max(8, len(border)*.2):
        raise ValueError('Insufficient safe background on border; inspect or change extraction method')
    bg_color = np.median(candidates, axis=0)
    if np.quantile(np.linalg.norm(candidates-bg_color, axis=1), .95) > .12:
        raise ValueError('Background has texture/gradient/checkerboard; regenerate')
    q = score(rgb, key)
    # Excludes desaturated mixed edges; actual matte varies slightly across generated sheets.
    bg = (q > .55) & (np.linalg.norm(rgb-bg_color, axis=2) < .16)
    sigma = max(2., min(im.size) / 45)
    weights = cv2.GaussianBlur(bg.astype(np.float32), (0, 0), sigma)
    field = np.empty_like(rgb)
    for c in range(3):
        numer = cv2.GaussianBlur(rgb[:, :, c]*bg, (0, 0), sigma)
        field[:, :, c] = np.where(weights > .0001, numer/np.maximum(weights, .0001), bg_color[c])
    alpha = np.clip(1-np.maximum(q, 0)/np.maximum(score(field, key), .4), 0, 1)
    alpha[bg] = 0
    alpha[alpha < .008] = 0
    fg = np.clip((rgb-(1-alpha[:, :, None])*field)/np.maximum(alpha[:, :, None], .008), 0, 1)
    fg[alpha == 0] = 0
    result = Image.fromarray(np.uint8(np.rint(np.dstack((fg, alpha))*255)))
    inspect_alpha(result)
    return result


def refine_mixed_rgb(im):
    """Optional nearest opaque RGB for diagnosed mixed edges; preserves alpha exactly.

    Approximation for one material/component, not a universal glass/blur despiller.
    Never erode the reference core: that discards narrow dark rims beside highlights.
    """
    inspect_alpha(im)
    rgba = np.asarray(im.convert('RGBA')).copy()
    core = rgba[:, :, 3] >= 250
    if not core.any():
        raise ValueError('No opaque color reference; use native selective correction')
    _, labels = cv2.distanceTransformWithLabels((~core).astype(np.uint8), cv2.DIST_L2, 5,
                                               labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros((int(labels.max())+1, 3), np.uint8)
    lookup[labels[core]] = rgba[:, :, :3][core]
    edge = (rgba[:, :, 3] > 0) & ~core
    rgba[:, :, :3][edge] = lookup[labels[edge]]
    return Image.fromarray(rgba)


def split(im, regions, out):
    inspect_alpha(im)
    boxes = []
    for name, box in regions.items():
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= im.width and 0 <= y0 < y1 <= im.height):
            raise ValueError('Region outside sheet: ' + name)
        for b in boxes:
            if min(x1, b[2]) > max(x0, b[0]) and min(y1, b[3]) > max(y0, b[1]):
                raise ValueError('Overlapping split regions')
        boxes.append(box)
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid asset name')
    out.mkdir(parents=True, exist_ok=True)
    occupied = np.zeros((im.height, im.width), bool)
    result = {}
    for name, box in regions.items():
        piece = im.crop(box)
        a = np.asarray(piece.getchannel('A'))
        # Only internal separators must be transparent; original canvas clipping is valid.
        x0, y0, x1, y1 = box
        edges = [a[0] if y0 > 0 else [], a[-1] if y1 < im.height else [],
                 a[:, 0] if x0 > 0 else [], a[:, -1] if x1 < im.width else []]
        if any(np.any(np.asarray(edge) > 0) for edge in edges):
            raise ValueError('Asset touches region edge; increase safe margin: ' + name)
        bounds = piece.getchannel('A').getbbox()
        if not bounds:
            raise ValueError('Empty asset: ' + name)
        # Keep the original canvas and exact coordinates, including tiny detached islands.
        padded = Image.new('RGBA', im.size)
        padded.paste(piece, (x0, y0))
        path = out / (name + '.png')
        if path.exists():
            raise FileExistsError(path)
        padded.save(path)
        x0, y0, x1, y1 = box
        occupied[y0:y1, x0:x1] = True
        result[name] = {'path': str(path.resolve()), 'region': box, 'alpha': inspect_alpha(padded)}
    if np.any((np.asarray(im.getchannel('A')) > 0) & ~occupied):
        raise ValueError('Unassigned visible pixels: review omitted small parts (outputs are provisional)')
    return result


def base_band(alpha, back, front, min_y=0):
    h, w = alpha.shape
    band = np.zeros_like(alpha, dtype=np.float32)
    for x in range(w):
        ys = np.flatnonzero(alpha[:, x] > .45)
        if len(ys) and ys[-1] >= min_y:
            y = int(ys[-1])
            band[max(0, y-back):min(h, y+front+1), x] = 1
    return band


def shadow(im, mode, dx, dy, back=2, front=2, min_y=0):
    inspect_alpha(im)
    a = np.asarray(im.getchannel('A'), dtype=np.float32) / 255
    if mode == 'base':
        a = base_band(a, back, front, min_y)
    a = cv2.warpAffine(a, np.float32([[1, 0, dx], [0, 1, dy]]), im.size, flags=cv2.INTER_LINEAR)
    rgba = np.zeros((im.height, im.width, 4), np.uint8)
    rgba[:, :, :3] = (10, 14, 14)
    rgba[:, :, 3] = np.uint8(np.rint(np.clip(a, 0, 1)*255))
    return Image.fromarray(rgba)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('choose'); p.add_argument('samples')
    p = sub.add_parser('inspect'); p.add_argument('image')
    p = sub.add_parser('refine-mixed'); p.add_argument('image'); p.add_argument('output')
    p = sub.add_parser('matte'); p.add_argument('image'); p.add_argument('output')
    p.add_argument('--key', required=True, choices=KEYS); p.add_argument('--samples', required=True)
    p = sub.add_parser('split'); p.add_argument('image'); p.add_argument('regions'); p.add_argument('outdir')
    p = sub.add_parser('shadow'); p.add_argument('image'); p.add_argument('output')
    p.add_argument('--mode', choices=['base', 'flat'], required=True)
    p.add_argument('--dx', type=float, required=True); p.add_argument('--dy', type=float, required=True)
    p.add_argument('--back', type=int, default=2); p.add_argument('--front', type=int, default=2)
    p.add_argument('--min-y', type=int, default=0)
    a = ap.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
    if a.cmd == 'choose':
        options = key_options(read(a.samples))
        report = {'safe_candidates': options, 'preferred': options[0] if options else 'native-alpha-or-split'}
    else:
        im = Image.open(a.image)
        if a.cmd == 'inspect':
            report = inspect_alpha(im)
        elif a.cmd == 'split':
            report = split(im, read(a.regions), Path(a.outdir))
        else:
            if a.cmd == 'matte': result = matte(im, a.key, read(a.samples))
            elif a.cmd == 'refine-mixed': result = refine_mixed_rgb(im)
            else: result = shadow(im, a.mode, a.dx, a.dy, a.back, a.front, a.min_y)
            dest = Path(a.output)
            if dest.exists():
                raise FileExistsError(dest)
            dest.parent.mkdir(parents=True, exist_ok=True); result.save(dest)
            report = {'output': str(dest.resolve()), 'alpha': inspect_alpha(result)}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
