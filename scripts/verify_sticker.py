#!/usr/bin/env python3
"""Check sticker PDFs built by sticker.py. Usage: verify_sticker.py WORK_DIR [--safe 1.5]"""
import sys, os, glob, json, argparse
import numpy as np, pikepdf

ap = argparse.ArgumentParser(); ap.add_argument('work'); ap.add_argument('--safe', type=float, default=1.5)
a = ap.parse_args()
ok_all = True
for jf in sorted(glob.glob(os.path.join(a.work, '*.json'))):
    rep = json.load(open(jf)); stem = os.path.splitext(os.path.basename(jf))[0]
    exp = np.load(os.path.join(a.work, f'{stem}_cmyk.npy'))
    pdf = pikepdf.open(rep['out']); pg = pdf.pages[0].obj
    mm = lambda b: [round(float(v) / 72 * 25.4, 2) for v in b]
    fonts = sum(1 for o in pdf.objects if isinstance(o, (pikepdf.Dictionary, pikepdf.Stream)) and o.get('/Type') == '/Font')
    imgs = [o for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.get('/Subtype') == '/Image']
    raw = np.frombuffer(imgs[0].read_bytes(), np.uint8)
    exact = raw.size == exp.size and np.array_equal(raw.reshape(exp.shape), exp)
    tac = raw.reshape(-1, 4).astype(np.uint16).sum(1).max() / 2.55
    d = rep['text_min_dist_to_cut_mm']; margin = min(d.values())
    issues = []
    if fonts: issues.append('fonts')
    if str(imgs[0].ColorSpace) != '/DeviceCMYK': issues.append('not CMYK')
    if not exact: issues.append('image data differs from intended CMYK')
    if tac > 300.5: issues.append(f'TAC {tac:.0f}%')
    if margin < a.safe - 0.01: issues.append(f'text {margin} mm from cut')
    if b'/CutContour' not in pdf.pages[0].obj.Resources.ColorSpace.CScut[1].unparse(): issues.append('no CutContour')
    ok_all &= not issues
    print(f"{stem}: media {mm(pg.MediaBox)[2:]} trim {mm(pg.TrimBox)} ppi {rep['ppi']} TAC {tac:.0f}% "
          f"text->cut min {margin} mm  {'OK' if not issues else 'ISSUES: ' + ', '.join(issues)}")
print('ALL OK' if ok_all else 'SOME FILES NEED ATTENTION')
sys.exit(0 if ok_all else 1)
