#!/usr/bin/env python3
"""Outer pouch: Figma print PDF -> CorelDRAW-ready CMYK print PDF.

Strategy (replaces the old "in-place CMYK rewrite" pipeline):
  * Plain opaque vector blocks stay vector; their sRGB colours are converted to
    FOGRA39 with LittleCMS (rel. colorimetric + BPC); exact neutrals -> K only.
  * Every block that uses transparency (blend modes, opacity, soft masks,
    images with alpha) is rendered *with its real backdrop* at 1200 ppi and
    placed back as an opaque-colour CMYK bitmap at the same stacking position,
    masked to exactly the pixels it changes. No live transparency survives.
  * Two-tone Overlay patterns are rebuilt at print resolution (JPEG speckle
    removed, edges reconstructed); small RGBA assets are pre-upscaled with
    premultiplied-alpha Lanczos so no dark halos appear.
  * Trim/bleed set per job (default 113 x 216 mm, 3 mm): side bleed uses Figma's real off-frame art,
    top/bottom bleed is an edge-pixel extension.
  * Window: film texture removed (clear, no ink). Hang hole: removed from ink,
    drawn on a non-printing "Dieline" spot layer with trim + window + tear ticks.
"""
import sys, os, io, zlib, math, json, time
import numpy as np, pikepdf, pymupdf as fitz
from PIL import Image, ImageCms
from scipy import ndimage
from pikepdf import Name, Dictionary, Array, PdfImage

TOOLS = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, TOOLS)
from blocks import split_blocks, classify, form_has

FOGRA = os.environ.get('PREPRESS_ICC', os.path.expanduser('~/.packaging-prepress/CoatedFOGRA39.icc'))
PPI = 1200                       # raster resolution for flattened transparency areas
ZL = 0.6                         # px per design unit for change detection
TAC = 300.0                      # total area coverage limit (%)

def configure(frame_w, frame_h, trim_w_mm, trim_h_mm, bleed_mm, fit='height'):
    """Set the page geometry. Design units = the source PDF's points; the artwork is scaled
    uniformly so the frame's height (or width) equals the trim height (or width)."""
    global FRAME_W, FRAME_H, MM_U, PT_U, Z, TRIM_W_MM, TRIM_H_MM, BLEED_MM, TRIM_W_U, TRIM_H_U, BLEED_U, TX0, TY0, TRIM, BLEED, BW_U, BH_U
    FRAME_W, FRAME_H = float(frame_w), float(frame_h)
    TRIM_W_MM, TRIM_H_MM, BLEED_MM = float(trim_w_mm), float(trim_h_mm), float(bleed_mm)
    MM_U = TRIM_H_MM / FRAME_H if fit == 'height' else TRIM_W_MM / FRAME_W
    PT_U = MM_U / 25.4 * 72.0
    Z = PPI / 72.0 * PT_U
    TRIM_W_U, TRIM_H_U, BLEED_U = TRIM_W_MM / MM_U, TRIM_H_MM / MM_U, BLEED_MM / MM_U
    TX0, TY0 = (FRAME_W - TRIM_W_U) / 2.0, (FRAME_H - TRIM_H_U) / 2.0
    TRIM = (TX0, TY0, TX0 + TRIM_W_U, TY0 + TRIM_H_U)
    BLEED = (TRIM[0] - BLEED_U, TRIM[1] - BLEED_U, TRIM[2] + BLEED_U, TRIM[3] + BLEED_U)
    BW_U, BH_U = BLEED[2] - BLEED[0], BLEED[3] - BLEED[1]
    return dict(frame_w=FRAME_W, frame_h=FRAME_H, trim_w_mm=TRIM_W_MM, trim_h_mm=TRIM_H_MM, bleed_mm=BLEED_MM, fit=fit)

configure(1276, 2441, 113, 216, 3)   # defaults, overridden per job

def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)

# ---------------------------------------------------------------- colour ---
class Color:
    def __init__(self, srgb_icc_bytes):
        self.src = ImageCms.ImageCmsProfile(io.BytesIO(srgb_icc_bytes))
        self.dst = ImageCms.ImageCmsProfile(FOGRA)
        I, F = ImageCms.Intent.RELATIVE_COLORIMETRIC, ImageCms.Flags.BLACKPOINTCOMPENSATION
        self.rgb2cmyk = ImageCms.buildTransform(self.src, self.dst, 'RGB', 'CMYK', renderingIntent=I, flags=F)
        lab = ImageCms.createProfile('LAB')
        self.rgb2lab = ImageCms.buildTransform(self.src, lab, 'RGB', 'LAB', renderingIntent=I)
        self.cmyk2lab = ImageCms.buildTransform(self.dst, lab, 'CMYK', 'LAB', renderingIntent=I)
        ramp = Image.fromarray(np.stack([np.zeros(256), np.zeros(256), np.zeros(256), np.arange(256)], 1).astype(np.uint8)[None], 'CMYK')
        self.k_L = np.asarray(ImageCms.applyTransform(ramp, self.cmyk2lab))[0, :, 0].astype(float)  # L (0..255) per K step
        self.cache = {}

    def to_cmyk_img(self, rgb):            # uint8 HxWx3 -> uint8 HxWx4, TAC limited
        out = np.empty(rgb.shape[:2] + (4,), np.uint8)
        step = 2048
        for y in range(0, rgb.shape[0], step):
            im = Image.fromarray(rgb[y:y + step], 'RGB')
            out[y:y + step] = np.asarray(ImageCms.applyTransform(im, self.rgb2cmyk))
        return tac_limit(out)

    def vec(self, rgb):                    # floats 0..1 -> CMYK floats 0..1
        key = tuple(round(v, 6) for v in rgb)
        if key in self.cache: return self.cache[key]
        r8 = [int(round(min(max(v, 0), 1) * 255)) for v in key]
        if r8[0] == r8[1] == r8[2]:        # exact neutral -> K only (registration-safe text/lines)
            if r8[0] == 255: res = (0, 0, 0, 0)
            elif r8[0] == 0: res = (0, 0, 0, 1)
            else:
                L = float(np.asarray(ImageCms.applyTransform(Image.new("RGB", (1, 1), tuple(r8)), self.rgb2lab))[0, 0, 0])
                k = float(np.interp(-L, -self.k_L, np.arange(256))) / 255.0
                res = (0, 0, 0, round(k, 4))
        else:
            c = np.asarray(ImageCms.applyTransform(Image.new('RGB', (1, 1), tuple(r8)), self.rgb2cmyk))[0, 0]
            c = tac_limit(c[None, None].copy())[0, 0]
            res = tuple(round(v / 255.0, 4) for v in c)
        res = tuple(float(x) for x in res)
        self.cache[key] = res
        return res

def tac_limit(c):
    f = c.astype(np.float32) * (100.0 / 255.0)
    s = f.sum(-1)
    over = s > TAC
    if over.any():
        cmy = f[..., :3][over]; k = f[..., 3][over]
        scale = np.clip((TAC - k) / np.maximum(cmy.sum(-1), 1e-6), 0, 1)
        f[..., :3][over] = cmy * scale[:, None]
        c = np.round(f * 2.55).clip(0, 255).astype(np.uint8)
    return c

# ---------------------------------------------------------- image tuning ---
def two_tone_rebuild(g, k):
    """Two-tone pattern: denoise, upscale by k, reconstruct sharp AA edges."""
    g = g.astype(np.float32)
    t = otsu(g)
    vd, vl = float(np.median(g[g <= t])), float(np.median(g[g > t]))
    mid = (vd + vl) / 2
    gs = ndimage.gaussian_filter(g, 0.8)
    up = ndimage.zoom(gs, k, order=3)
    gy, gx = np.gradient(up)
    sd = (up - mid) / np.maximum(np.hypot(gx, gy), 1e-3)     # ~signed distance in px
    del gx, gy
    a = np.clip(0.5 + sd, 0, 1)
    return np.round(vd + a * (vl - vd)).astype(np.uint8), (vd, vl)

def otsu(g):
    h, _ = np.histogram(g, 256, (0, 256)); p = h / h.sum(); w = np.cumsum(p); m = np.cumsum(p * np.arange(256))
    sb = (m[-1] * w - m) ** 2 / np.maximum(w * (1 - w), 1e-9)
    return float(np.argmax(sb))

def is_two_tone(rgb):
    g = rgb.mean(-1)
    if np.abs(rgb.astype(int) - g[..., None]).max() > 6: return False
    t = otsu(g); vd, vl = np.median(g[g <= t]), np.median(g[g > t])
    mid_band = (g > vd + 0.25 * (vl - vd)) & (g < vl - 0.25 * (vl - vd))   # only AA edges may sit here
    return mid_band.mean() < 0.03 and (vl - vd) > 40

def lanczos_premul(rgb, a, k):
    w, h = rgb.shape[1] * k, rgb.shape[0] * k
    af = a.astype(np.float32) / 255.0
    pm = rgb.astype(np.float32) * af[..., None]
    chans = [np.asarray(Image.fromarray(pm[..., i]).resize((w, h), Image.LANCZOS)) for i in range(3)]
    au = np.asarray(Image.fromarray(af).resize((w, h), Image.LANCZOS)).clip(0, 1)
    rgbu = np.stack(chans, -1) / np.maximum(au[..., None], 1e-4)
    rgbu = np.where(au[..., None] > 1e-4, rgbu, 0)
    return rgbu.round().clip(0, 255).astype(np.uint8), (au * 255).round().astype(np.uint8)

def put_image(pdf, xo, rgb=None, gray=None, alpha=None, cs=None):
    if gray is not None:
        data, ncs = gray.tobytes(), Name.DeviceGray
        h, w = gray.shape
    else:
        data, ncs = rgb.tobytes(), cs
        h, w = rgb.shape[:2]
    xo.write(zlib.compress(data, 6), filter=Name.FlateDecode)
    xo.Width, xo.Height, xo.BitsPerComponent, xo.ColorSpace = w, h, 8, ncs
    for key in ('/DecodeParms', '/Decode'):
        if key in xo: del xo[key]
    if alpha is not None:
        sm = pikepdf.Stream(pdf, zlib.compress(alpha.tobytes(), 6))
        sm.Type, sm.Subtype, sm.Width, sm.Height = Name.XObject, Name.Image, w, h
        sm.ColorSpace, sm.BitsPerComponent, sm.Filter = Name.DeviceGray, 8, Name.FlateDecode
        xo.SMask = sm
    elif '/SMask' in xo:
        del xo['/SMask']

def improve_images(pdf, x1, blocks, src_path, report):
    res = x1.Resources
    # 1) two-tone Overlay patterns
    for b in blocks:
        cx, kinds, dos, gss = classify(b, res)
        if not any(str(res.ExtGState[g].get('/BM', '')) == '/Overlay' for g in gss): continue
        stack = [res.XObject[d] for d in dos]
        while stack:
            xo = stack.pop()
            for n, s in xo.get('/Resources', {}).get('/XObject', {}).items():
                if s.Subtype == '/Form': stack.append(s); continue
                pil = PdfImage(s).as_pil_image().convert('RGB'); rgb = np.asarray(pil)
                if not is_two_tone(rgb): continue
                # placement scale: largest cm in this form stream
                cms = [o for o, op in pikepdf.parse_content_stream(xo) if str(op) == 'cm']
                span = max(math.hypot(float(o[0]), float(o[1])) for o in cms)
                ppi = rgb.shape[1] / (span * MM_U / 25.4)
                k = int(min(12, math.ceil(PPI / ppi)))
                g, (vd, vl) = two_tone_rebuild(rgb.mean(-1), k)
                put_image(pdf, s, rgb=np.repeat(g[..., None], 3, -1), cs=s.ColorSpace)
                if '/SMask' in s: del s['/SMask']
                report.append(f'pattern {rgb.shape[1]}x{rgb.shape[0]} @ {ppi:.0f} ppi -> two-tone rebuilt x{k} = {g.shape[1]}x{g.shape[0]} ({ppi*k:.0f} ppi), tones {vd:.0f}/{vl:.0f}')
    # 2) small RGBA assets below 800 ppi -> premultiplied Lanczos
    d = fitz.open(src_path); ppi_by = {}
    for inf in d[0].get_image_info(xrefs=True):
        if not inf['xref']: continue
        tr = inf['transform']; dw = math.hypot(tr[0], tr[1]) * MM_U / 25.4; dh = math.hypot(tr[2], tr[3]) * MM_U / 25.4
        if dw and dh:
            p = min(inf['width'] / dw, inf['height'] / dh)
            ppi_by[inf['xref']] = min(p, ppi_by.get(inf['xref'], 1e9))
    for xref, p in ppi_by.items():
        if p >= 800: continue
        s = pdf.get_object(xref, 0)
        if s.get('/Subtype') != '/Image' or s.get('/ColorSpace') == '/DeviceRGB': continue   # DeviceRGB = window film / pattern
        k = int(min(3, math.ceil(PPI / p)))
        im = PdfImage(s).as_pil_image().convert('RGB'); rgb = np.asarray(im)
        a = np.asarray(PdfImage(s.SMask).as_pil_image().convert('L')) if '/SMask' in s else np.full(rgb.shape[:2], 255, np.uint8)
        if a.shape != rgb.shape[:2]: continue
        rgbu, au = lanczos_premul(rgb, a, k)
        put_image(pdf, s, rgb=rgbu, alpha=au if '/SMask' in s else None, cs=s.ColorSpace)
        report.append(f'asset xref {xref} {rgb.shape[1]}x{rgb.shape[0]} @ {p:.0f} ppi -> x{k} premultiplied Lanczos ({p*k:.0f} ppi)')

# ------------------------------------------------------------- geometry ---
def path_points(ops):
    pts = []; cm = None
    for o, op in ops:
        n = str(op)
        if n == 'cm' and cm is None: cm = [float(v) for v in o]
        if n in ('m', 'l', 'c', 'v', 'y'):
            v = [float(x) for x in o]
            pts += list(zip(v[0::2], v[1::2]))
    a, b, c, d, e, f = cm or [1, 0, 0, 1, 0, 0]
    return [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]

def find_window(res, blocks):
    for i, b in enumerate(blocks):
        cx, kinds, dos, gss = classify(b, res)
        for dn in dos:
            xo = res.XObject[dn]
            if xo.get('/Subtype') == '/Form' and form_has(xo, lambda g: str(g.get('/BM', '')) == '/HardLight'):
                for sn, sub in xo.Resources.XObject.items():
                    ops = list(pikepdf.parse_content_stream(sub))
                    if any(str(op) == 'f' for _, op in ops) and not any(str(op) == 'Do' for _, op in ops):
                        geo = [(o, op) for o, op in ops if str(op) in ('cm', 'm', 'l', 'c', 'v', 'y', 'h')]
                        return i, geo
    return None, None

def find_hole(blocks):
    for i, b in enumerate(blocks):
        cols = [tuple(round(float(v), 3) for v in o) for o, op in b if str(op) in ('scn', 'sc')]
        if cols and cols[0] == (0.118, 0.118, 0.118):
            pts = path_points(b); xs, ys = zip(*pts)
            if (max(xs) - min(xs)) > 0.03 * FRAME_W and abs((max(xs) + min(xs)) / 2 - FRAME_W / 2) < 0.03 * FRAME_W and min(ys) > 0.8 * FRAME_H:
                return i, ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(xs) - min(xs)) / 2)
    return None, None

def find_tear(blocks):
    for b in blocks:
        cols = [tuple(round(float(v), 3) for v in o) for o, op in b if str(op) in ('scn', 'sc')]
        if cols == [(0.0, 0.0, 0.0)]:
            pts = path_points(b); xs, ys = zip(*pts)
            if max(xs) - min(xs) > 0.9 * FRAME_W and max(ys) - min(ys) < 0.005 * FRAME_H:
                return (max(ys) + min(ys)) / 2
    return None

# ----------------------------------------------------------- rendering ---
class Renderer:
    def __init__(self, pdf_bytes, block_bytes):
        self.doc = fitz.open('pdf', pdf_bytes)
        self.xref = [x[0] for x in self.doc[0].get_xobjects() if x[1] == 'X1'][0]
        self.bb = block_bytes; self.k = None
    def set_k(self, k):
        if k != self.k:
            self.doc.update_stream(self.xref, b'\n'.join(self.bb[:k]) if k else b' ')
            self.k = k
    def render(self, k, zoom, irect=None):
        self.set_k(k)
        page = self.doc[0]
        clip = fitz.Rect(*[v / zoom for v in irect]) if irect else None
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False, colorspace=fitz.csRGB)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[..., :3].copy()
        if irect and (pix.x, pix.y, pix.x + pix.w, pix.y + pix.h) != tuple(irect):
            raise RuntimeError(f'clip mismatch {pix.irect} vs {irect}')
        return a

def units_rect_from_px(px0, py0, px1, py1, zoom):
    return (BLEED[0] + px0 / zoom, BLEED[3] - py1 / zoom, (px1 - px0) / zoom, (py1 - py0) / zoom)

# --------------------------------------------------------------- build ---
def build(src, out_dir, base, side, opts):
    t0 = time.time(); report = []
    pdf = pikepdf.open(src)
    pg = pdf.pages[0]; x1 = pg.Resources.XObject['/X1']; res = x1.Resources
    mb = [float(v) for v in pg.MediaBox]
    geometry = configure(mb[2] - mb[0], mb[3] - mb[1], opts['trim_w'], opts['trim_h'], opts['bleed'], opts.get('fit', 'height'))
    icc = pg.Resources.ColorSpace['/C1'][1].read_bytes()
    col = Color(icc)
    blocks, loose = split_blocks(x1)
    assert not loose, 'unexpected top-level ops'

    win_i, win_geo = find_window(res, blocks) if side == 'Front' else (None, None)
    hole_i, hole = find_hole(blocks)
    tear_y = find_tear(blocks)
    report.append(f'blocks {len(blocks)}; window block {win_i}; hang-hole block {hole_i} {hole}; tear line y {tear_y}')

    if win_i is not None and opts['clear_window']:
        geo = pikepdf.unparse_content_stream(win_geo)
        blocks[win_i] = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, b'q\n1 1 1 scn\n' + geo + b'\nf\nQ')))
        report.append('window: film texture removed -> no-ink knockout (vector)')
    if hole_i is not None and opts['hole_to_dieline']:
        blocks[hole_i] = None
        report.append('hang hole: black dot removed from ink, drawn on Dieline layer')
    blocks = [b for b in blocks if b is not None]

    improve_images(pdf, x1, blocks, src, report)

    # working page: bleed-sized, X1 unclipped to bleed, no page soft mask, no hidden text
    pg.MediaBox = Array(BLEED)
    for k in ('/CropBox', '/TrimBox', '/BleedBox', '/ArtBox', '/StructParents', '/Tabs', '/Annots'):
        if k in pg.obj: del pg.obj[k]
    pg.Contents = pdf.make_stream(b'/C1 CS /C1 cs q /X1 Do Q')
    for k in ('/Font', '/ExtGState'):
        if k in pg.Resources: del pg.Resources[k]
    x1.BBox = Array(BLEED)
    for k in ('/StructTreeRoot', '/MarkInfo'):
        if k in pdf.Root: del pdf.Root[k]
    bb = [pikepdf.unparse_content_stream(b) for b in blocks]
    buf = io.BytesIO(); pdf.save(buf); work_bytes = buf.getvalue()
    R = Renderer(work_bytes, bb)

    flags = [classify(b, res)[0] for b in blocks]
    runs = []; i = 0
    while i < len(blocks):
        if flags[i]:
            j = i
            while j + 1 < len(blocks) and flags[j + 1]: j += 1
            runs.append((i, j)); i = j + 1
        else: i += 1

    W_hi, H_hi = int(math.ceil(BW_U * Z)), int(math.ceil(BH_U * Z))
    out = pikepdf.new(); images = {}; seq = []          # seq: ('v', bytes) or ('i', name, rect_units)
    def add_img(cmyk, alpha, rect):
        h, w = cmyk.shape[:2]
        st = pikepdf.Stream(out, zlib.compress(cmyk.tobytes(), 6))
        st.Type, st.Subtype, st.Width, st.Height = Name.XObject, Name.Image, w, h
        st.ColorSpace, st.BitsPerComponent, st.Filter = Name.DeviceCMYK, 8, Name.FlateDecode
        if alpha is not None and alpha.min() < 255:
            sm = pikepdf.Stream(out, zlib.compress(alpha.tobytes(), 6))
            sm.Type, sm.Subtype, sm.Width, sm.Height = Name.XObject, Name.Image, w, h
            sm.ColorSpace, sm.BitsPerComponent, sm.Filter = Name.DeviceGray, 8, Name.FlateDecode
            st.SMask = sm
        name = f'/Im{len(images) + 1}'; images[name] = st
        return name

    # top / bottom bleed strips (edge-pixel extension), drawn first = underneath
    top_edge = int(math.ceil((BLEED[3] - FRAME_H) * Z)); bot_edge = int(math.floor(BLEED[3] * Z))
    strips = []
    bands = []
    if 0 < top_edge < H_hi - 2: bands.append((top_edge + 1, (0, top_edge + 2)))     # frame top inside bleed box
    if 2 < bot_edge < H_hi: bands.append((bot_edge - 2, (bot_edge - 1, H_hi)))        # frame bottom inside bleed box
    for row, (y0, y1) in bands:
        line = R.render(len(blocks), Z, (0, row, W_hi, row + 1))
        band = np.repeat(line, y1 - y0, 0)
        name = add_img(col.to_cmyk_img(band), None, None)
        strips.append(('i', name, units_rect_from_px(0, y0, W_hi, y1, Z)))
    report.append(f'bleed: sides = real off-frame artwork; top/bottom = {BLEED_MM} mm edge extension')

    # transparency runs -> masked CMYK rasters
    lo = {}
    def lo_render(k):
        if k not in lo: lo[k] = R.render(k, ZL)
        return lo[k]
    piece_for_run = {}
    total_px = 0
    for (s, e) in runs:
        d = np.any(lo_render(s) != lo_render(e + 1), -1)
        if not d.any():
            piece_for_run[s] = None; continue
        ys, xs = np.nonzero(d)
        m = 4
        px0 = max(0, int((xs.min() - m) / ZL * Z)); px1 = min(W_hi, int((xs.max() + 1 + m) / ZL * Z))
        py0 = max(0, int((ys.min() - m) / ZL * Z)); py1 = min(H_hi, int((ys.max() + 1 + m) / ZL * Z))
        before = R.render(s, Z, (px0, py0, px1, py1)); after = R.render(e + 1, Z, (px0, py0, px1, py1))
        mask = np.any(before != after, -1); del before
        if not mask.any():
            piece_for_run[s] = None; continue
        mask = ndimage.binary_dilation(mask, iterations=2)
        yy, xx = np.nonzero(mask)
        cy0, cy1, cx0, cx1 = yy.min(), yy.max() + 1, xx.min(), xx.max() + 1
        rgb = after[cy0:cy1, cx0:cx1]; al = (mask[cy0:cy1, cx0:cx1] * 255).astype(np.uint8); del after
        name = add_img(col.to_cmyk_img(rgb), al, None)
        rect = units_rect_from_px(px0 + cx0, py0 + cy0, px0 + cx1, py0 + cy1, Z)
        piece_for_run[s] = ('i', name, rect); total_px += rgb.shape[0] * rgb.shape[1]
        log(f'   run {s}-{e}: {rgb.shape[1]}x{rgb.shape[0]} px, {mask[cy0:cy1, cx0:cx1].mean()*100:.0f}% masked-in')
    report.append(f'transparency runs: {len(runs)} -> {sum(1 for v in piece_for_run.values() if v)} masked CMYK rasters @ {PPI} ppi ({total_px/1e6:.1f} Mpx)')

    # vector blocks -> CMYK
    nvec = 0
    for i, b in enumerate(blocks):
        if flags[i]:
            run = next(r for r in runs if r[0] <= i <= r[1])
            if i == run[0] and piece_for_run.get(i): seq.append(piece_for_run[i])
            continue
        new = []
        for o, op in b:
            n = str(op)
            if n in ('cs', 'CS'):
                if str(o[0]) != '/C1': raise RuntimeError(f'unexpected colour space {o[0]}')
                continue
            if n in ('scn', 'sc', 'SCN', 'SC', 'rg', 'RG', 'g', 'G'):
                v = [float(x) for x in o]
                if len(v) == 1: v = v * 3
                if len(v) != 3: raise RuntimeError(f'unexpected colour {n} {v}')
                c = col.vec(v)
                new.append(([*c], pikepdf.Operator('K' if n in ('SCN', 'SC', 'RG', 'G') else 'k')))
                continue
            new.append((o, op))
        seq.append(('v', pikepdf.unparse_content_stream(new))); nvec += 1
    report.append(f'vector blocks kept as editable curves: {nvec}')

    # ---- assemble output page
    Wpt, Hpt = BW_U * PT_U, BH_U * PT_U
    bpt = BLEED_U * PT_U
    parts = [f'q {PT_U:.8f} 0 0 {PT_U:.8f} {-BLEED[0]*PT_U:.6f} {-BLEED[1]*PT_U:.6f} cm'.encode(),
             f'{BLEED[0]:.4f} {BLEED[1]:.4f} {BW_U:.4f} {BH_U:.4f} re W n'.encode()]
    for it in strips + seq:
        if it[0] == 'v': parts.append(it[1])
        else:
            x, y, w, h = it[2]
            parts.append(f'q {w:.6f} 0 0 {h:.6f} {x:.6f} {y:.6f} cm {it[1]} Do Q'.encode())
    # dieline layer (non-printing spot, overprint)
    lw = 0.25 / PT_U
    dl = [b'/OC /OCdie BDC q /CSdie CS 1 SCN /GSop gs', f'{lw:.4f} w'.encode(),
          f'{TRIM[0]:.4f} {TRIM[1]:.4f} {TRIM_W_U:.4f} {TRIM_H_U:.4f} re S'.encode()]
    if hole and opts['hole_to_dieline']:
        cx, cy, r = hole; kk = 0.5523 * r
        dl.append((f'{cx+r:.3f} {cy:.3f} m {cx+r:.3f} {cy+kk:.3f} {cx+kk:.3f} {cy+r:.3f} {cx:.3f} {cy+r:.3f} c '
                   f'{cx-kk:.3f} {cy+r:.3f} {cx-r:.3f} {cy+kk:.3f} {cx-r:.3f} {cy:.3f} c '
                   f'{cx-r:.3f} {cy-kk:.3f} {cx-kk:.3f} {cy-r:.3f} {cx:.3f} {cy-r:.3f} c '
                   f'{cx+kk:.3f} {cy-r:.3f} {cx+r:.3f} {cy-kk:.3f} {cx+r:.3f} {cy:.3f} c S').encode())
    if win_geo:
        dl.append(b'q ' + pikepdf.unparse_content_stream(win_geo) + b' S Q')
    if tear_y:
        dl.append(f'{BLEED[0]:.3f} {tear_y:.3f} m {TRIM[0]:.3f} {tear_y:.3f} l {TRIM[2]:.3f} {tear_y:.3f} m {BLEED[2]:.3f} {tear_y:.3f} l S'.encode())
    dl.append(b'Q EMC')
    parts += dl + [b'Q']
    content = b'\n'.join(parts)

    page = pikepdf.Dictionary(Type=Name.Page, MediaBox=Array([0, 0, Wpt, Hpt]), CropBox=Array([0, 0, Wpt, Hpt]),
                              BleedBox=Array([0, 0, Wpt, Hpt]), TrimBox=Array([bpt, bpt, Wpt - bpt, Hpt - bpt]))
    ocg = out.make_indirect(Dictionary(Type=Name.OCG, Name=pikepdf.String('Dieline (non-printing)'),
                                       Usage=Dictionary(Print=Dictionary(PrintState=Name.OFF), View=Dictionary(ViewState=Name.ON))))
    sep_fn = Dictionary(FunctionType=2, Domain=Array([0, 1]), C0=Array([0, 0, 0, 0]), C1=Array([0, 1, 0, 0]), N=1)
    page.Resources = Dictionary(
        XObject=Dictionary({k: v for k, v in images.items()}),
        ExtGState=Dictionary(GSop=Dictionary(Type=Name.ExtGState, OP=True, op=True, OPM=1)),
        ColorSpace=Dictionary(CSdie=Array([Name.Separation, Name('/Dieline'), Name.DeviceCMYK, sep_fn])),
        Properties=Dictionary(OCdie=ocg))
    page.Contents = out.make_stream(content)
    out.pages.append(pikepdf.Page(page))
    out.Root.OCProperties = Dictionary(OCGs=Array([ocg]), D=Dictionary(ON=Array([ocg]), Order=Array([ocg]),
                                       AS=Array([Dictionary(Event=Name.Print, OCGs=Array([ocg]), Category=Array([Name.Print]))])))
    icc_st = pikepdf.Stream(out, open(FOGRA, 'rb').read()); icc_st.N = 4
    out.Root.OutputIntents = Array([Dictionary(Type=Name.OutputIntent, S=Name.GTS_PDFX,
        OutputConditionIdentifier=pikepdf.String('FOGRA39'), RegistryName=pikepdf.String('http://www.color.org'),
        Info=pikepdf.String('Coated FOGRA39 (ISO 12647-2:2004), TAC limited to 300%'), DestOutputProfile=icc_st)])
    with out.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta['dc:title'] = f'{base} {side} - Print CMYK {TRIM_W_MM:g}x{TRIM_H_MM:g} mm'
    out.docinfo[Name.Title] = pikepdf.String(f'{base} Outer {side} - CMYK print master {TRIM_W_MM:g} x {TRIM_H_MM:g} mm + {BLEED_MM:g} mm bleed')
    out.docinfo[Name.Producer] = pikepdf.String('Packaging prepress pipeline')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{base}_Outer_{side}_Print_CMYK_{TRIM_W_MM:g}x{TRIM_H_MM:g}mm.pdf')
    out.save(out_path, min_version='1.6')

    # keep the reference composite for verification + white plate
    ref_path = os.path.join(opts['work'], f'{base}_{side}_reference.pdf')
    R.set_k(len(blocks)); R.doc.save(ref_path, garbage=0)
    meta = dict(geometry=geometry, src=src, out=out_path, ref=ref_path, window=[pikepdf.unparse_content_stream(win_geo).decode()] if win_geo else None,
                hole=hole, tear_y=tear_y, report=report, seconds=round(time.time() - t0))
    json.dump(meta, open(os.path.join(opts['work'], f'{base}_{side}.json'), 'w'), indent=1)
    for r in report: log('  ', r)
    log(f'  -> {out_path} ({os.path.getsize(out_path)/1e6:.1f} MB) in {time.time()-t0:.0f}s')
    return meta

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Figma/Canva print PDF -> CorelDRAW-ready CMYK print PDF')
    ap.add_argument('src'); ap.add_argument('out_dir'); ap.add_argument('base', help='file-name prefix, e.g. C')
    ap.add_argument('side', help='Front or Back'); ap.add_argument('work', help='scratch folder for metadata/reference renders')
    ap.add_argument('--trim', default='113x216', help='trim W x H in mm (default 113x216)')
    ap.add_argument('--bleed', type=float, default=3.0)
    ap.add_argument('--fit', choices=['height', 'width'], default='height', help='which frame dimension maps onto the trim')
    ap.add_argument('--keep-window', action='store_true', help='print the window film texture instead of a clear window')
    ap.add_argument('--print-hole', action='store_true', help='keep the hang-hole dot as printed ink')
    a = ap.parse_args()
    tw, th = (float(v) for v in a.trim.lower().split('x'))
    os.makedirs(a.work, exist_ok=True)
    build(a.src, a.out_dir, a.base, a.side, dict(clear_window=not a.keep_window, hole_to_dieline=not a.print_hole, work=a.work,
                                                 trim_w=tw, trim_h=th, bleed=a.bleed, fit=a.fit))
