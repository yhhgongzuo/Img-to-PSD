"""No generation calls or Photoshop changes: deterministic safety regression fixtures."""
import copy
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from asset_ops import KEYS, base_band, inspect_alpha, key_options, matte, split, refine_mixed_rgb
from plan import validate
from preflight import validate_scene, similarity, rgb_offset_points, compare_alpha


class RegressionTests(unittest.TestCase):
    def sheet(self, color, key):
        arr = np.zeros((32, 40, 3), np.uint8); arr[:] = KEYS[key]
        arr[8:24, 10:30] = color
        arr[13:17, 16:20] = KEYS[key]  # actual hole
        return Image.fromarray(arr)

    def test_purple_on_green(self):
        color = [180, 40, 200]
        result = np.asarray(matte(self.sheet(color, 'green'), 'green', [color]))
        self.assertEqual(result[10, 12, 3], 255)
        np.testing.assert_array_equal(result[10, 12, :3], color)
        self.assertEqual(result[14, 17, 3], 0)
        self.assertEqual(result[0, 0, 3], 0)
        self.assertNotIn('magenta', key_options([color]))

    def test_green_on_magenta(self):
        color = [30, 180, 40]
        result = np.asarray(matte(self.sheet(color, 'magenta'), 'magenta', [color]))
        self.assertEqual(result[10, 12, 3], 255)
        np.testing.assert_array_equal(result[10, 12, :3], color)
        self.assertEqual(result[0, 0, 3], 0)

    def test_conflicts(self):
        self.assertNotIn('magenta', key_options([[200, 20, 200], [20, 200, 20]]))
        self.assertNotIn('green', key_options([[200, 20, 200], [20, 200, 20]]))
        self.assertEqual(key_options(list(KEYS.values())), [])
        with self.assertRaises(ValueError):
            matte(self.sheet([200, 20, 200], 'magenta'), 'magenta', [[200, 20, 200]])

    def test_all_six_keys_partial_edge(self):
        for key, color in KEYS.items():
            image = np.asarray(self.sheet([100, 100, 100], key)).copy()
            image[8, 10] = np.rint(.5*np.array(color)+50)
            out = np.asarray(matte(Image.fromarray(image), key, [[100, 100, 100]]))
            self.assertLess(abs(int(out[8, 10, 3])-128), 3)
            self.assertEqual(out[0, 0, 3], 0)

    def test_fake_transparency_and_existing_alpha(self):
        board = (np.indices((32, 32)).sum(axis=0) % 2 * 80 + 160).astype(np.uint8)
        fake = Image.fromarray(np.repeat(board[:, :, None], 3, axis=2))
        with self.assertRaises(ValueError): inspect_alpha(fake)
        with self.assertRaises(ValueError): matte(fake, 'magenta', [[100, 100, 100]])
        rgba = matte(self.sheet([100, 100, 100], 'magenta'), 'magenta', [[100, 100, 100]])
        with self.assertRaises(ValueError): matte(rgba, 'magenta', [[100, 100, 100]])

    def test_split_keeps_small_island_and_hole(self):
        rgba = np.asarray(matte(self.sheet([100, 100, 100], 'magenta'), 'magenta', [[100, 100, 100]])).copy()
        rgba[4, 5] = [100, 100, 100, 110]  # tiny semitransparent detached thread
        with tempfile.TemporaryDirectory() as td:
            report = split(Image.fromarray(rgba), {'asset': [0, 0, 40, 32]}, Path(td))
            saved = np.asarray(Image.open(report['asset']['path']))
            np.testing.assert_array_equal(saved, rgba)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                split(Image.fromarray(rgba), {'asset': [7, 6, 34, 28]}, Path(td))

    def test_original_canvas_clipping_and_internal_cut(self):
        a = np.zeros((32, 40, 4), np.uint8)
        a[12:, 6:15] = [160, 120, 50, 255]  # original bottom-frame crop
        with tempfile.TemporaryDirectory() as td:
            result = split(Image.fromarray(a), {'asset': [0, 0, 40, 32]}, Path(td))
            np.testing.assert_array_equal(np.asarray(Image.open(result['asset']['path'])), a)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                split(Image.fromarray(a), {'asset': [0, 0, 40, 20]}, Path(td))
        rgb = np.asarray(self.sheet([120, 120, 120], 'magenta')).copy()
        rgb[20:, 10:30] = [120, 120, 120]
        keyed = np.asarray(matte(Image.fromarray(rgb), 'magenta', [[120, 120, 120]]))
        self.assertTrue(np.all(keyed[-1, 10:30, 3] == 255))

    def test_narrow_dark_rim_survives_color_reference(self):
        a = np.zeros((20, 20, 4), np.uint8)
        a[4:16, 5:15] = [230, 230, 230, 255]
        a[4:16, 4] = [30, 20, 10, 255]  # one-pixel dark rim must not be eroded
        a[4:16, 3] = [180, 25, 160, 110]
        fixed = np.asarray(refine_mixed_rgb(Image.fromarray(a)))
        np.testing.assert_array_equal(fixed[:, :, 3], a[:, :, 3])
        np.testing.assert_array_equal(fixed[a[:, :, 3] == 255], a[a[:, :, 3] == 255])
        np.testing.assert_array_equal(fixed[8, 3, :3], [30, 20, 10])

    def test_scene_geometry_and_filter_guards(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'a.png'; self.sheet([120, 120, 120], 'magenta').save(p)
            spec = {'width':40, 'height':32, 'groups':[{'name':'G','layers':[
                {'type':'image','name':'a','path':str(p),'rgbOffsets':[-1,0,2]}]}]}
            self.assertFalse(validate_scene(spec))
            item = spec['groups'][0]['layers'][0]
            item['frame'] = [0,0,40,30]
            self.assertTrue(validate_scene(spec)); item.pop('frame')
            spec['width'] = 41
            self.assertTrue(validate_scene(spec)); spec['width'] = 40
            item['hueAdjustments'] = [{'channel':6,'hue':90}]
            self.assertTrue(validate_scene(spec))
            item['hueAdjustments'][0]['range'] = [255,285,315,345]
            self.assertFalse(validate_scene(spec))
            self.assertFalse(similarity([[1,0,0],[0,1.2,0]]))
            self.assertFalse(similarity([[-1,0,0],[0,1,0]]))
            self.assertTrue(similarity([[.8,-.3,5],[.3,.8,-5]]))
            pts = rgb_offset_points(-1)
            self.assertEqual(len(pts),9)
            self.assertEqual(pts[4], [128,127])

    def test_color_only_alpha_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            a = np.asarray(matte(self.sheet([100,100,100], 'magenta'),'magenta',[[100,100,100]])).copy()
            p,q = Path(td)/'a.png', Path(td)/'b.png'
            Image.fromarray(a).save(p)
            a[10,12,:3] = [130,110,70]; Image.fromarray(a).save(q)
            self.assertTrue(compare_alpha(p,q))
            a[10,12,3] = 254; Image.fromarray(a).save(q)
            self.assertFalse(compare_alpha(p,q))

    def test_lower_contour_not_flat_baseline(self):
        a = np.zeros((40, 30), np.float32)
        for x in range(5, 25): a[5:10+x, x] = 1
        band = base_band(a, 1, 1)
        self.assertEqual(np.flatnonzero(band[:, 5])[-1], 15)
        self.assertEqual(np.flatnonzero(band[:, 24])[-1], 34)
        self.assertEqual(band[:, 0].sum(), 0)

    def test_manifest_coverage_overlap_duplicate(self):
        m = {'elements': [dict(id=i, route='generated', components=[i+'-body'], shadow='pair', receiver='floor') for i in ['a','b']],
             'overlaps': [['a','b']], 'batches': [dict(id=i, elements=[i], background='green', color_reason='sampled') for i in ['a','b']]}
        self.assertFalse(validate(m))
        bad = copy.deepcopy(m); bad['batches'] = [dict(id='X', elements=['a','b'], background='green', color_reason='sampled')]
        self.assertTrue(any('conflict' in e for e in validate(bad)))
        bad = copy.deepcopy(m); bad['batches'].pop()
        self.assertTrue(any('coverage' in e for e in validate(bad)))
        bad = copy.deepcopy(m); bad['batches'][1]['elements'].append('a')
        self.assertTrue(any('more than once' in e for e in validate(bad)))


if __name__ == '__main__': unittest.main(verbosity=2)
