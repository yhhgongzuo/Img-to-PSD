"""Isolated synthetic Photoshop regression. Prepare, build, verify-base, patch, verify-patch."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
from asset_ops import shadow
from verify_psd import verify
from preflight import compare_alpha


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    path = lambda n: str(root/n)
    w,h = 360,240
    Image.new('RGB',(w,h),'#637574').save(path('background.png'))
    Image.new('RGB',(w,h),'#637574').save(path('source.png'))
    a = np.zeros((h,w,4),np.uint8)
    for i,v in enumerate([32,64,96,128,160,192,224]):
        a[70:120,20+i*20:40+i*20] = [v,v,v,255]
        a[69,20+i*20:40+i*20] = [v,v,v,110]
    Image.fromarray(a).save(path('gray.png'))
    shadow(Image.fromarray(a),'base',.5,.5).save(path('contact.png'))
    shadow(Image.fromarray(a),'base',8,5,10,5).save(path('soft.png'))
    b = np.zeros_like(a)
    colors = [[200,40,180],[200,50,40],[200,180,50],[40,180,60],[40,70,200],[130,130,130]]
    for i,col in enumerate(colors):
        b[145:180,30+i*45:60+i*45] = col+[255]
        b[144,30+i*45:60+i*45] = col+[110]
    Image.fromarray(b).save(path('hue.png'))
    manifest = {'elements':[], 'overlaps':[], 'batches':[]}
    for eid,route in [('gray','generated'),('hue','generated'),('bg','generated-background'),('title','native-text'),('shape','native-shape'),('light','effect')]:
        e = dict(id=eid,route=route,components=[eid+'-body'],shadow='pair' if eid=='gray' else 'none')
        if eid=='gray': e['receiver']='bg'
        else: e['shadow_reason']='synthetic test'
        manifest['elements'].append(e)
        if route.startswith('generated'):
            manifest['batches'].append(dict(id=eid,elements=[eid],background='scene' if eid=='bg' else 'native-alpha',color_reason='synthetic native alpha'))
    gray = dict(type='image',name='Gray body',path=path('gray.png'),component_id='gray-body',role='object',
                rgbOffsets=[-1,-3,-5],exportPng=path('base-gray.png'),alphaBaseline=path('gray.png'))
    hue = dict(type='image',name='Hue body',path=path('hue.png'),component_id='hue-body',role='object',
               exportPng=path('base-hue.png'),alphaBaseline=path('hue.png'))
    scene = dict(name='Native Skill regression',sourceImage=path('source.png'),width=w,height=h,resolution=72,
                 outputPsd=path('base.psd'),outputJpg=path('base.jpg'),qaPng=path('base.png'),qaMoveGroup='Gray group',
                 qaViews=[dict(path=path('base-background.png'),onlyRoot='Background')],groups=[
        dict(name='Background',layers=[dict(type='image',name='Background image',path=path('background.png'),component_id='bg-body')]),
        dict(name='Gray group',element_id='gray',layers=[
            dict(type='image',name='Soft shadow',path=path('soft.png'),role='soft-shadow',postBlur=4,opacity=28,blend='MULTIPLY'),
            dict(type='image',name='Contact shadow',path=path('contact.png'),role='contact-shadow',postBlur=1,opacity=65,blend='MULTIPLY'),gray]),
        dict(name='Hue group',layers=[hue]),
        dict(name='Graphics',layers=[
            dict(type='fill',name='Shape',component_id='shape-body',color='DABC84',box=[20,210,330,215]),
            dict(type='fill',name='Glow',component_id='light-body',color='F4D28B',shape='ellipse',box=[280,70,330,120],blur=7,opacity=20,blend='SCREEN')]),
        dict(name='Text',layers=[dict(type='text',name='Editable title',component_id='title-body',text='Skill regression',font='ArialMT',size=22,x=20,y=40,color='FFFFFF')]),
        dict(name='Reference',visible=False,layers=[dict(type='image',name='Reference image',role='reference',path=path('source.png'))])])
    patched = copy.deepcopy(scene)
    patched.update(basePsd=path('base.psd'),outputPsd=path('patched.psd'),outputJpg=path('patched.jpg'),qaPng=path('patched.png'))
    patched['qaViews'][0]['path'] = path('patched-background.png')
    patched['groups'][1]['layers'][-1]['exportPng'] = path('patched-gray.png')
    target = patched['groups'][2]['layers'][0]
    target.update(patch=True,exportPng=path('patched-hue.png'),alphaBaseline=path('base-hue.png'),hueAdjustments=[
        dict(channel=6,range=[255,285,315,345],hue=90,saturation=0,lightness=0),
        dict(channel=1,range=[315,345,10,30],hue=40,saturation=0,lightness=0)])
    write(root/'manifest.json',manifest);write(root/'scene.json',scene);write(root/'patch.json',patched)
    print('Build scene.json with build_psd.ps1, run native_test.py DIR --stage verify-base;')
    print('then build patch.json and run native_test.py DIR --stage verify-patch.')


def check(root, patch):
    report = verify(read(root/('patch.json' if patch else 'scene.json')), read(root/'manifest.json'))
    assert report['ok'], report['issues']
    original = np.asarray(Image.open(root/'gray.png').convert('RGBA'))
    result = np.asarray(Image.open(root/('patched-gray.png' if patch else 'base-gray.png')).convert('RGBA'))
    opaque = original[:,:,3] == 255
    error = np.abs(result[:,:,:3][opaque].astype(int)-(original[:,:,:3][opaque].astype(int)+[-1,-3,-5]))
    assert error.max() <= 1, ('RGB midtone bowing', error.max())
    assert compare_alpha(root/'gray.png', root/('patched-gray.png' if patch else 'base-gray.png'))
    report['rgb_offset_max_interior_error'] = int(error.max())
    if patch:
        assert read(root/'base-hash.json')['sha256'] == digest(root/'base.psd'), 'Source PSD modified'
        for name in ['gray','background']:
            before=np.asarray(Image.open(root/('base-'+name+'.png')))
            after=np.asarray(Image.open(root/('patched-'+name+'.png')))
            np.testing.assert_array_equal(before,after)
        before=np.asarray(Image.open(root/'base-hue.png'))
        after=np.asarray(Image.open(root/'patched-hue.png'))
        protected_error=[]
        for i in range(2,6):
            a=before[150:175,35+i*45:55+i*45].astype(int)
            b=after[150:175,35+i*45:55+i*45].astype(int)
            error=int(np.abs(a-b).max()); protected_error.append(error)
            # Native Hue/Saturation has a measured 2/255 rounding shift in this gold swatch.
            # Untargeted components above still require exact RGBA equality.
            assert error <= (2 if i==2 else 0), ('Protected swatch recolored', i, error)
        changes=[]
        for i in range(2):
            change=int(np.abs(before[160,40+i*45,:3].astype(int)-after[160,40+i*45,:3].astype(int)).max())
            assert change>20, 'Selective hue did not change intended swatch'
            changes.append(change)
        assert compare_alpha(root/'base-hue.png',root/'patched-hue.png')
        report.update(source_psd_unchanged=True,protected_swatch_max_channel_errors=protected_error,untargeted_gray_and_background_unchanged=True,
                      selective_hue_change=changes,color_only_alpha_equal=True)
    else:
        raw=np.asarray(Image.open(root/'hue.png')); native=np.asarray(Image.open(root/'base-hue.png'))
        # Photoshop may serialize different RGB under alpha=0; those colors are invisible.
        np.testing.assert_array_equal(raw[raw[:,:,3]>0],native[raw[:,:,3]>0])
        assert compare_alpha(root/'hue.png',root/'base-hue.png')
        write(root/'base-hash.json',{'sha256':digest(root/'base.psd')})
    write(root/('patch-report.json' if patch else 'base-report.json'),report)
    print(json.dumps({k:v for k,v in report.items() if k!='inventory'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory')
    p.add_argument('--stage',choices=['prepare','verify-base','verify-patch'],default='prepare')
    a=p.parse_args();r=Path(a.directory).resolve()
    if a.stage=='prepare':prepare(r)
    else:check(r,a.stage=='verify-patch')
