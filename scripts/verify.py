#!/usr/bin/env python3
"""Independent verification of a built print PDF."""
import sys, os, io, json, math, re, subprocess, zlib
import numpy as np, pikepdf, pymupdf as fitz
from PIL import Image, ImageCms
TOOLS = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, TOOLS)
import build as B
from build import Color

VPPI = 300

def lab_of_rgb(col, rgb):
    return np.asarray(ImageCms.applyTransform(Image.fromarray(rgb, "RGB"), col.rgb2lab))

def lab_of_cmyk(col, cmyk):
    return np.asarray(ImageCms.applyTransform(Image.fromarray(cmyk, "CMYK"), col.cmyk2lab))

def to_lab_units(lab8):          # PIL LAB: L 0..255, a/b offset 128
    L = lab8[..., 0].astype(np.float32) * (100 / 255.0)
    a = lab8[..., 1].astype(np.uint8).view(np.int8).astype(np.float32); b = lab8[..., 2].astype(np.uint8).view(np.int8).astype(np.float32)
    return np.stack([L, a, b], -1)

def dE(l1, l2):
    return np.sqrt(((to_lab_units(l1) - to_lab_units(l2)) ** 2).sum(-1))

def render_out(path, cs, ppi, dieline=False):
    fitz.TOOLS.set_icc(cs is not fitz.csCMYK)   # raw CMYK pass-through for measurement
    if dieline:
        d = fitz.open(path)
    else:                                         # strip the non-printing dieline layer
        pdf = pikepdf.open(path); c = pdf.pages[0].obj.Contents.read_bytes()
        c = re.sub(rb'/OC /OCdie BDC.*?EMC', b'', c, flags=re.S)
        pdf.pages[0].obj.Contents = pdf.make_stream(c); b = io.BytesIO(); pdf.save(b)
        d = fitz.open('pdf', b.getvalue())
    pix = d[0].get_pixmap(matrix=fitz.Matrix(ppi / 72, ppi / 72), colorspace=cs, alpha=False)
    fitz.TOOLS.set_icc(True)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n).copy()

def preflight(path):
    pdf = pikepdf.open(path); pg = pdf.pages[0]; issues = []; info = {}
    info['pages'] = len(pdf.pages)
    mm = lambda v: round(float(v) / 72 * 25.4, 3)
    for k in ('/MediaBox', '/BleedBox', '/TrimBox'):
        b = [float(x) for x in pg.obj[k]]; info[k[1:]] = f'{mm(b[2]-b[0])} x {mm(b[3]-b[1])} mm @ ({mm(b[0])},{mm(b[1])})'
    fonts = 0; bad_gs = []; cspaces = set(); imgs = []; maxtac = 0.0
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Dictionary) or isinstance(obj, pikepdf.Stream):
            t = obj.get('/Type')
            if t == '/Font': fonts += 1
            if t == '/ExtGState':
                for key in ('/BM', '/ca', '/CA', '/SMask'):
                    if key in obj and not (key == '/BM' and obj[key] == '/Normal') and not (key in ('/ca', '/CA') and float(obj[key]) == 1):
                        bad_gs.append(key)
            if isinstance(obj, pikepdf.Stream) and obj.get('/Subtype') == '/Image' and obj.get('/ColorSpace') != '/DeviceGray':
                cs = obj.get('/ColorSpace'); cspaces.add(str(cs))
                raw = np.frombuffer(obj.read_bytes(), np.uint8)
                if str(cs) == '/DeviceCMYK':
                    maxtac = max(maxtac, float(raw.reshape(-1, 4).astype(np.uint16).sum(1).max()) / 2.55)
                imgs.append((int(obj.Width), int(obj.Height), '/SMask' in obj))
    content = pg.obj.Contents.read_bytes()
    ops = set(re.findall(rb'(?<![A-Za-z/])(rg|RG|scn|SCN|sc|SC|g|G|k|K|cs|CS|BT|Tf|sh|BI)(?![A-Za-z])', content))
    vec_tacs = [sum(float(x) for x in m) * 100 for m in re.findall(rb'([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) [kK]\b', content)]
    info.update(fonts=fonts, live_transparency_ops=sorted(set(bad_gs)), image_colorspaces=sorted(cspaces),
                colour_operators=sorted(o.decode() for o in ops), images=len(imgs),
                max_tac_images=round(maxtac, 1), max_tac_vectors=round(max(vec_tacs) if vec_tacs else 0, 1),
                output_intent=str(pdf.Root.OutputIntents[0].OutputConditionIdentifier),
                pdf_version=pdf.pdf_version, size_mb=round(os.path.getsize(path) / 1e6, 1))
    if fonts: issues.append('fonts present')
    if bad_gs: issues.append('live transparency in ExtGState')
    if cspaces - {'/DeviceCMYK'}: issues.append(f'non-CMYK images {cspaces}')
    if ops & {b'rg', b'RG', b'g', b'G', b'BT', b'Tf', b'sh', b'BI', b'sc', b'SC', b'scn'}: issues.append(f'unexpected operators {ops}')
    if maxtac > 300.5 or (vec_tacs and max(vec_tacs) > 300.5): issues.append('TAC > 300%')
    return info, issues

def main(meta_path, out_png_dir):
    meta = json.load(open(meta_path))
    B.configure(**meta['geometry'])
    BLEED, PT_U, TRIM = B.BLEED, B.PT_U, B.TRIM
    out, ref, src = meta['out'], meta['ref'], meta['src']
    tag = os.path.basename(out).replace('.pdf', '')
    pdf0 = pikepdf.open(src); col = Color(pdf0.pages[0].Resources.ColorSpace['/C1'][1].read_bytes())
    res = {'file': os.path.basename(out)}
    res['preflight'], res['issues'] = preflight(out)

    # 1) output (CMYK) vs reference composite (sRGB design incl. window/hole edits)
    cm = render_out(out, fitz.csCMYK, VPPI)
    dref = fitz.open(ref)
    z = VPPI / 72 * PT_U
    pr = dref[0].get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB, alpha=False)
    rgb = np.frombuffer(pr.samples, np.uint8).reshape(pr.h, pr.w, 3)
    h = min(cm.shape[0], rgb.shape[0]); w = min(cm.shape[1], rgb.shape[1])
    cm, rgb = cm[:h, :w], rgb[:h, :w]
    lab_out = lab_of_cmyk(col, cm)
    lab_expect = lab_of_cmyk(col, col.to_cmyk_img(rgb))       # what a perfect FOGRA39 conversion gives
    lab_design = lab_of_rgb(col, rgb)
    d_pipe = dE(lab_out, lab_expect); d_gamut = dE(lab_expect, lab_design)
    tz0 = int(round((TRIM[0] - BLEED[0]) * z)) + 2; tz1 = int(round((TRIM[2] - BLEED[0]) * z)) - 2
    ty0 = int(round((BLEED[3] - TRIM[3]) * z)) + 2; ty1 = int(round((BLEED[3] - TRIM[1]) * z)) - 2
    full_d_pipe = d_pipe; d_pipe = d_pipe[ty0:ty1, tz0:tz1]; d_gamut = d_gamut[ty0:ty1, tz0:tz1]
    res['dims_px'] = [w, h, cm.shape[1], rgb.shape[1]]
    res['pipeline_dE'] = dict(mean=round(float(d_pipe.mean()), 3), p99=round(float(np.percentile(d_pipe, 99)), 2),
                              p999=round(float(np.percentile(d_pipe, 99.9)), 2), max=round(float(d_pipe.max()), 1),
                              pct_over_2=round(float((d_pipe > 2).mean() * 100), 3), pct_over_5=round(float((d_pipe > 5).mean() * 100), 4))
    res['gamut_dE_sRGB_to_FOGRA39'] = dict(mean=round(float(d_gamut.mean()), 2), p95=round(float(np.percentile(d_gamut, 95)), 1))
    # isolated (non-edge) error clusters
    from scipy import ndimage
    big = ndimage.binary_opening(d_pipe > 5, iterations=2)
    lbl, n = ndimage.label(big)
    res['pipeline_error_blobs_over_dE5'] = int(n)
    g = lab_design[ty0:ty1, tz0:tz1, 0].astype(np.float32)
    edge = ndimage.binary_dilation(np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1)) > 20, iterations=2)
    flat = ~edge
    res['pipeline_dE_flat_areas'] = dict(share_of_page=round(float(flat.mean()*100), 1), mean=round(float(d_pipe[flat].mean()), 3),
        p99=round(float(np.percentile(d_pipe[flat], 99)), 2), max=round(float(d_pipe[flat].max()), 1), pct_over_3=round(float((d_pipe[flat] > 3).mean()*100), 4))
    np.save(os.path.join(out_png_dir, f'{tag}_dpipe.npy'), d_pipe.astype(np.float16))
    heat = np.clip(full_d_pipe * 25, 0, 255).astype(np.uint8)
    Image.fromarray(heat).resize((w // 3, h // 3)).save(os.path.join(out_png_dir, f'{tag}_dE_heatmap.png'))

    # 2) vs ORIGINAL Figma file (unmodified, frame region)
    d0 = fitz.open(src); z0 = VPPI / 72 * PT_U
    p0 = d0[0].get_pixmap(matrix=fitz.Matrix(z0, z0), colorspace=fitz.csRGB, alpha=False)
    o = np.frombuffer(p0.samples, np.uint8).reshape(p0.h, p0.w, 3)
    ox = int(round((0 - BLEED[0]) * z)); oy = int(round((BLEED[3] - B.FRAME_H) * z))
    hh = min(o.shape[0], h - oy); ww = min(o.shape[1], w - ox)
    lo_ = lab_of_cmyk(col, col.to_cmyk_img(np.ascontiguousarray(o[:hh, :ww])))
    d_orig = dE(lab_out[oy:oy + hh, ox:ox + ww], lo_)
    res['vs_original_dE'] = dict(mean=round(float(d_orig.mean()), 2), pct_over_5=round(float((d_orig > 5).mean() * 100), 2))
    Image.fromarray(np.clip(d_orig * 12, 0, 255).astype(np.uint8)).resize((ww // 3, hh // 3)).save(os.path.join(out_png_dir, f'{tag}_vs_original.png'))

    # 3) bleed coverage: any unprinted pixel in the bleed zone?
    tz0 = int(round((TRIM[0] - BLEED[0]) * z)); tz1 = int(round((TRIM[2] - BLEED[0]) * z))
    ty0 = int(round((BLEED[3] - TRIM[3]) * z)); ty1 = int(round((BLEED[3] - TRIM[1]) * z))
    inkless = cm.astype(np.uint16).sum(-1) < 3
    inkless[-1:, :] = False; inkless[:, -1:] = False            # partial last pixel row/col of the raster
    bleedzone = np.ones(inkless.shape, bool); bleedzone[ty0 + 2:ty1 - 2, tz0 + 2:tz1 - 2] = False
    res['bleed_unprinted_pct'] = round(float((inkless & bleedzone).sum() / bleedzone.sum() * 100), 3)
    yy, xx = np.nonzero(inkless & bleedzone)
    if len(yy): res['bleed_unprinted_bbox_px'] = [int(xx.min()), int(yy.min()), int(xx.max()), int(yy.max()), 'of', w, h]

    # 4) proofs: MuPDF (with dieline) + Apple Quartz render
    prf = render_out(out, fitz.csRGB, 150, dieline=True)
    Image.fromarray(prf).save(os.path.join(out_png_dir, f'{tag}_proof.png'))
    ap = os.path.join(out_png_dir, f'{tag}_apple.png')
    subprocess.run(['sips', '-s', 'format', 'png', '--resampleHeight', str(prf.shape[0]), out, '--out', ap], capture_output=True)
    a = np.asarray(Image.open(ap).convert('RGB'))
    if a.shape == prf.shape:
        g1 = a.astype(float).mean(-1); g2 = prf.astype(float).mean(-1)
        res['apple_vs_mupdf_luma_corr'] = round(float(np.corrcoef(g1.ravel(), g2.ravel())[0, 1]), 4)
        res['apple_blank_pct'] = round(float((a.min(-1) > 250).mean() * 100), 2)
    else:
        res['apple_render'] = f'shape {a.shape} vs {prf.shape}'
    print(json.dumps(res, indent=1))
    json.dump(res, open(meta_path.replace('.json', '_verify.json'), 'w'), indent=1)

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
