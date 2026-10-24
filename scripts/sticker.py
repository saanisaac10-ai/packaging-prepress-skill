#!/usr/bin/env python3
"""Flattened label/tube artwork (PNG) -> sticker print PDF (defaults = a 30 x 173 mm tube).

  * Default sticker 24 x 140 mm, 2 mm rounded corners, 2 mm bleed, cut line as spot 'CutContour'.
  * Uniform scale (no distortion); the original pixels map 1:1 onto the output grid
    (no resampling) -> ~930 ppi at sticker size.
  * Top seal strip (OPEN tab, dark tab, dashed tear line) replaced with the design's flat red.
  * Scale chosen so every text element sits >= 1.5 mm inside the cut line.
  * sRGB -> FOGRA39 (rel. col. + BPC), TAC 300 %.
"""
import sys, os, io, zlib, math, json
import numpy as np, pikepdf
from PIL import Image, ImageCms
from scipy import ndimage
from pikepdf import Name, Dictionary, Array
TOOLS = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, TOOLS)
from build import tac_limit, FOGRA

ART_W_MM, ART_H_MM = 30.0, 173.0          # physical size the PNG represents (tube dieline)
W_MM, H_MM, R_MM, BLEED, SAFE = 24.0, 140.0, 2.0, 2.0, 1.5
RED = np.array([213, 37, 24], np.uint8)     # fill for the cleaned top strip; auto-detected per file
CREAM = np.array([248, 231, 162])           # text/logo colour used for the safe-zone check
TOP_CLEAN_MM = 17.3                         # strip above this (tab, tear line) is replaced by FILL
PT = 72 / 25.4

def flat_palette(rgb, min_share=0.003):
    """Exact colours covering >= 0.3 % of the image = the design's flat brand colours."""
    u, c = np.unique(rgb.reshape(-1, 3), axis=0, return_counts=True)
    return [u[i].astype(int) for i in np.nonzero(c >= min_share * c.sum())[0]]

def fill_colour(rgb, y_px):
    """Most common colour on the row where the top strip is cut off."""
    u, c = np.unique(rgb[y_px].reshape(-1, 3), axis=0, return_counts=True)
    return u[np.argmax(c)].astype(np.uint8)

def load(path):
    a = np.asarray(Image.open(path).convert('RGBA'))
    rgb, al = a[..., :3].copy(), a[..., 3]
    bad = al < 255                          # Figma frame AA on right column / rounded corners
    if bad.any():
        _, (iy, ix) = ndimage.distance_transform_edt(bad, return_indices=True)
        rgb = rgb[iy, ix]
    return rgb, int(bad.sum())

def text_mask(rgb):
    """Cream type/logo pixels on flat background; anything in/next to a photo is excluded."""
    x = rgb.astype(int)
    cream = ndimage.binary_opening(np.abs(x - CREAM).sum(-1) < 90)
    flat = flat_palette(rgb) + [CREAM, np.array([0, 0, 0])]
    dmin = np.min([np.abs(x - f.astype(int)).sum(-1) for f in flat], 0)
    # anti-aliased type edges lie on a line between cream and the local flat colour -> not "photo"
    photo = ndimage.binary_opening(dmin > 60, iterations=3)
    photo = ndimage.binary_dilation(photo, iterations=25)          # 0.9 mm keep-away from photos
    m = cream & ~photo
    lbl, n = ndimage.label(m)
    sizes = ndimage.sum(m, lbl, range(1, n + 1))
    return np.isin(lbl, np.nonzero(sizes >= 12)[0] + 1)

def max_scale(masks, art_px_w, art_px_h):
    px = ART_W_MM / art_px_w
    upper, lower = [], [0.0]
    for m in masks:
        ys, xs = np.nonzero(m)
        x0, x1 = xs.min() * px, (xs.max() + 1) * px
        y0, y1 = ys.min() * px, (ys.max() + 1) * px
        # centred horizontally: (W - 30k)/2 + x0 k >= SAFE  and  (W - 30k)/2 + x1 k <= W - SAFE
        upper.append((W_MM / 2 - SAFE) / (ART_W_MM / 2 - x0))
        upper.append((W_MM / 2 - SAFE) / (x1 - ART_W_MM / 2))
        # bottom-aligned art: top text  H - 173k + y0 k >= SAFE ; bottom text (173 - y1) k >= SAFE
        upper.append((H_MM - SAFE) / (ART_H_MM - y0))
        lower.append(SAFE / (ART_H_MM - y1))
    k = min(upper)
    assert k >= max(lower), f'no scale satisfies all margins: {k} < {max(lower)}'
    return k

def build(src, out_pdf, title, k, col, report):
    rgb, nbad = load(src)
    H, Wp = rgb.shape[:2]
    pxmm_art = Wp / ART_W_MM
    fill = fill_colour(rgb, int(TOP_CLEAN_MM * pxmm_art)) if TOP_CLEAN_MM > 0 else None
    if fill is not None:
        rgb[: int(TOP_CLEAN_MM * pxmm_art)] = fill   # remove OPEN tab, dark tab, tear line
    ppmm = pxmm_art / k                          # output px per sticker mm (1 art px = 1 out px)
    cw, ch = int(math.ceil((W_MM + 2 * BLEED) * ppmm)), int(round((H_MM + 2 * BLEED) * ppmm))
    ox = int(round((BLEED + (W_MM - ART_W_MM * k) / 2) * ppmm))
    oy = int(round((BLEED + H_MM) * ppmm)) - H  # art bottom on the sticker's bottom edge
    def mirror(i, n):                        # mirrored bleed: continues photo texture without streaks
        i = np.abs(i); i = np.where(i >= n, 2 * n - 2 - i, i)
        return np.clip(i, 0, n - 1)
    yy = mirror(np.arange(ch) - oy, H); xx = mirror(np.arange(cw) - ox, Wp)
    canvas = rgb[yy[:, None], xx[None, :]]
    if fill is not None: canvas[: max(oy, 0)] = fill   # space above the art = design background
    cmyk = np.empty(canvas.shape[:2] + (4,), np.uint8)
    for y in range(0, ch, 1024):
        cmyk[y:y + 1024] = np.asarray(ImageCms.applyTransform(Image.fromarray(np.ascontiguousarray(canvas[y:y + 1024]), 'RGB'), col))
    cmyk = tac_limit(cmyk)

    pdf = pikepdf.new()
    Wpt, Hpt = (W_MM + 2 * BLEED) * PT, (H_MM + 2 * BLEED) * PT
    img = pikepdf.Stream(pdf, zlib.compress(cmyk.tobytes(), 6))
    img.Type, img.Subtype, img.Width, img.Height = Name.XObject, Name.Image, cw, ch
    img.ColorSpace, img.BitsPerComponent, img.Filter = Name.DeviceCMYK, 8, Name.FlateDecode
    iw, ih = cw / ppmm * PT, ch / ppmm * PT
    x0, y0, x1, y1, r = BLEED * PT, BLEED * PT, (BLEED + W_MM) * PT, (BLEED + H_MM) * PT, R_MM * PT
    c = 0.5523 * r
    cut = (f'{x0+r:.4f} {y0:.4f} m {x1-r:.4f} {y0:.4f} l {x1-r+c:.4f} {y0:.4f} {x1:.4f} {y0+r-c:.4f} {x1:.4f} {y0+r:.4f} c '
           f'{x1:.4f} {y1-r:.4f} l {x1:.4f} {y1-r+c:.4f} {x1-r+c:.4f} {y1:.4f} {x1-r:.4f} {y1:.4f} c '
           f'{x0+r:.4f} {y1:.4f} l {x0+r-c:.4f} {y1:.4f} {x0:.4f} {y1-r+c:.4f} {x0:.4f} {y1-r:.4f} c '
           f'{x0:.4f} {y0+r:.4f} l {x0:.4f} {y0+r-c:.4f} {x0+r-c:.4f} {y0:.4f} {x0+r:.4f} {y0:.4f} c h S')
    content = (f'q 0 0 {Wpt:.4f} {Hpt:.4f} re W n q {iw:.4f} 0 0 {ih:.4f} 0 {Hpt-ih:.4f} cm /Im1 Do Q Q\n'
               f'q /CScut CS 1 SCN /GSop gs 0.25 w {cut} Q').encode()
    sep = Array([Name.Separation, Name('/CutContour'), Name.DeviceCMYK,
                 Dictionary(FunctionType=2, Domain=Array([0, 1]), C0=Array([0, 0, 0, 0]), C1=Array([0, 1, 0, 0]), N=1)])
    page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, Wpt, Hpt]), CropBox=Array([0, 0, Wpt, Hpt]),
                      BleedBox=Array([0, 0, Wpt, Hpt]), TrimBox=Array([x0, y0, x1, y1]),
                      Resources=Dictionary(XObject=Dictionary(Im1=img), ColorSpace=Dictionary(CScut=sep),
                                           ExtGState=Dictionary(GSop=Dictionary(Type=Name.ExtGState, OP=True, op=True, OPM=1))),
                      Contents=pdf.make_stream(content))
    pdf.pages.append(pikepdf.Page(page))
    icc = pikepdf.Stream(pdf, open(FOGRA, 'rb').read()); icc.N = 4
    pdf.Root.OutputIntents = Array([Dictionary(Type=Name.OutputIntent, S=Name.GTS_PDFX,
        OutputConditionIdentifier=pikepdf.String('FOGRA39'), RegistryName=pikepdf.String('http://www.color.org'),
        Info=pikepdf.String('Coated FOGRA39, TAC limited to 300%'), DestOutputProfile=icc)])
    pdf.docinfo[Name.Title] = pikepdf.String(title)
    pdf.docinfo[Name.Producer] = pikepdf.String('Packaging prepress pipeline (sticker)')
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    pdf.save(out_pdf, min_version='1.6')
    report.update(src=src, out=out_pdf, k=k, fill=None if fill is None else fill.tolist(), ppi=round(ppmm * 25.4), canvas=[cw, ch], ox=ox, oy=oy, alpha_fixed_px=nbad,
                  size_mb=round(os.path.getsize(out_pdf) / 1e6, 2))
    return canvas, cmyk, ppmm, ox, oy

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='PNG artwork -> sticker print PDFs (one common scale for all jobs)')
    ap.add_argument('--job', nargs=3, action='append', required=True, metavar=('SRC_PNG', 'OUT_PDF', 'TITLE'))
    ap.add_argument('--work', required=True)
    ap.add_argument('--art', default='30x173', help='physical size the PNG represents, W x H mm')
    ap.add_argument('--size', default='24x140', help='sticker W x H mm'); ap.add_argument('--radius', type=float, default=2.0)
    ap.add_argument('--bleed', type=float, default=2.0); ap.add_argument('--safe', type=float, default=1.5)
    ap.add_argument('--top-clean-mm', type=float, default=17.3, help='replace everything above this with the background (0 = keep)')
    ap.add_argument('--text-rgb', default='248,231,162', help='colour of the type/logo to keep inside the safe zone')
    a = ap.parse_args()
    ART_W_MM, ART_H_MM = (float(v) for v in a.art.lower().split('x'))
    W_MM, H_MM = (float(v) for v in a.size.lower().split('x'))
    R_MM, BLEED, SAFE, TOP_CLEAN_MM = a.radius, a.bleed, a.safe, a.top_clean_mm
    CREAM = np.array([int(v) for v in a.text_rgb.split(',')])
    os.makedirs(a.work, exist_ok=True)
    srgb = ImageCms.createProfile('sRGB'); fog = ImageCms.ImageCmsProfile(FOGRA)
    col = ImageCms.buildTransform(srgb, fog, 'RGB', 'CMYK', renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                  flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
    masks, shapes = {}, {}
    for src, out, title in a.job:
        rgb, _ = load(src)
        m = text_mask(rgb); m[: int(TOP_CLEAN_MM * rgb.shape[1] / ART_W_MM)] = False
        masks[src] = m; shapes[src] = rgb.shape
    hs = {s[0] for s in shapes.values()}; ws = {s[1] for s in shapes.values()}
    assert len(hs) == 1 and len(ws) == 1, 'all jobs must share one artwork size'
    k_lim = max_scale(list(masks.values()), ws.pop(), hs.pop())
    k = math.floor(k_lim * 1000) / 1000
    print(f'max uniform scale for {SAFE} mm text safety: {k_lim:.4f} -> using {k}')
    for src, out, title in a.job:
        rep = {}
        canvas, cmyk, ppmm, ox, oy = build(src, out, title, k, col, rep)
        m = masks[src]; ys, xs = np.nonzero(m)
        sx = (xs + ox) / ppmm - BLEED; sy = (ys + oy) / ppmm - BLEED
        rep['text_min_dist_to_cut_mm'] = dict(left=round(float(sx.min()), 2), right=round(float(W_MM - (sx.max() + 1 / ppmm)), 2),
                                              top=round(float(sy.min()), 2), bottom=round(float(H_MM - (sy.max() + 1 / ppmm)), 2))
        stem = os.path.splitext(os.path.basename(out))[0]
        np.save(os.path.join(a.work, f'{stem}_cmyk.npy'), cmyk)
        json.dump(rep, open(os.path.join(a.work, f'{stem}.json'), 'w'), indent=1)
        print(stem, rep)
