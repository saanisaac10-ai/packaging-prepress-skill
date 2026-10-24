#!/usr/bin/env python3
"""White underlay plate (spot 'White') for printing on clear film.
Front: white everywhere except the clear window; art that overlaps the window
(the cat etc.) keeps white under it. White is choked 0.1 mm under art edges."""
import sys, os, io, re, json, zlib
import numpy as np, pikepdf, pymupdf as fitz
from scipy import ndimage
from pikepdf import Name, Dictionary, Array
TOOLS = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, TOOLS)
import build as B
from build import path_points
CHOKE_PX = 5          # 0.1 mm at 1200 ppi

def render_region(pdf_bytes, irect, cs, zoom, icc):
    fitz.TOOLS.set_icc(icc)
    d = fitz.open('pdf', pdf_bytes)
    pix = d[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(*[v / zoom for v in irect]), colorspace=cs, alpha=False)
    fitz.TOOLS.set_icc(True)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)

def main(meta_path):
    meta = json.load(open(meta_path))
    B.configure(**meta['geometry'])
    BLEED, BW_U, BH_U, PT_U, Z = B.BLEED, B.BW_U, B.BH_U, B.PT_U, B.Z
    out = meta['out']; dst = out.replace('_Print_CMYK_', '_White_Underlay_')
    src = pikepdf.open(out); sp = src.pages[0].obj
    Wpt, Hpt = float(sp.MediaBox[2]), float(sp.MediaBox[3])
    o = pikepdf.new()
    sep = Array([Name.Separation, Name('/White'), Name.DeviceCMYK,
                 Dictionary(FunctionType=2, Domain=Array([0, 1]), C0=Array([0, 0, 0, 0]), C1=Array([0, 0, 0, 1]), N=1)])
    res = Dictionary(ColorSpace=Dictionary(CSw=sep))
    head = f'q {PT_U:.8f} 0 0 {PT_U:.8f} {-BLEED[0]*PT_U:.6f} {-BLEED[1]*PT_U:.6f} cm /CSw cs 1 scn'
    parts = [head.encode()]
    note = 'full coverage'
    if meta.get('window'):
        geo = meta['window'][0].encode()
        pts = path_points(list(pikepdf.parse_content_stream(pikepdf.Stream(o, geo))))
        xs, ys = zip(*pts)
        px0 = int((min(xs) - BLEED[0]) * Z) - 12; px1 = int((max(xs) - BLEED[0]) * Z) + 12
        py0 = int((BLEED[3] - max(ys)) * Z) - 12; py1 = int((BLEED[3] - min(ys)) * Z) + 12
        # window shape at 1200 ppi
        t = pikepdf.new(); pg = t.add_blank_page(page_size=(BW_U, BH_U)); pg.MediaBox = Array(BLEED)
        pg.Contents = t.make_stream(b'1 g ' + f'{BLEED[0]} {BLEED[1]} {BW_U} {BH_U} re f 0 g '.encode() + geo + b' f')
        b = io.BytesIO(); t.save(b)
        win = render_region(b.getvalue(), (px0, py0, px1, py1), fitz.csGRAY, Z, True)[..., 0] < 128
        # ink in the final print file (dieline stripped), same pixel grid
        p = pikepdf.open(out); c = p.pages[0].obj.Contents.read_bytes()
        p.pages[0].obj.Contents = p.make_stream(re.sub(rb'/OC /OCdie BDC.*?EMC', b'', c, flags=re.S)); b2 = io.BytesIO(); p.save(b2)
        zpt = 1200 / 72.0
        cm = render_region(b2.getvalue(), (px0, py0, px1, py1), fitz.csCMYK, zpt, False)
        h = min(win.shape[0], cm.shape[0]); w = min(win.shape[1], cm.shape[1]); win, cm = win[:h, :w], cm[:h, :w]
        ink = cm.astype(np.uint16).sum(-1) > 5
        need = (~win) | ink
        need = ndimage.binary_erosion(need, iterations=CHOKE_PX, border_value=1)
        bits = np.packbits(~need, axis=1)            # ImageMask: 0 = paint
        x0u = BLEED[0] + px0 / Z; y1u = BLEED[3] - py0 / Z; wu = w / Z; hu = h / Z
        st = pikepdf.Stream(o, zlib.compress(bits.tobytes(), 9))
        st.Type, st.Subtype, st.Width, st.Height, st.ImageMask, st.BitsPerComponent, st.Filter = \
            Name.XObject, Name.Image, w, h, True, 1, Name.FlateDecode
        res.XObject = Dictionary(Wm=st)
        parts.append(f'{BLEED[0]:.4f} {BLEED[1]:.4f} {BW_U:.4f} {BH_U:.4f} re {x0u:.4f} {y1u-hu:.4f} {wu:.4f} {hu:.4f} re f*'.encode())
        parts.append(f'q {wu:.6f} 0 0 {hu:.6f} {x0u:.6f} {y1u-hu:.6f} cm /Wm Do Q'.encode())
        clear_cm2 = (~need & win).sum() * (2.54 / 1200) ** 2
        note = f'white everywhere except the clear window ({clear_cm2:.1f} cm2 unprinted); white choked {CHOKE_PX/1200*25.4:.2f} mm under art edges'
    else:
        parts.append(f'{BLEED[0]:.4f} {BLEED[1]:.4f} {BW_U:.4f} {BH_U:.4f} re f'.encode())
    parts.append(b'Q')
    page = Dictionary(Type=Name.Page, MediaBox=sp.MediaBox, CropBox=sp.MediaBox, BleedBox=sp.BleedBox, TrimBox=sp.TrimBox,
                      Resources=res, Contents=o.make_stream(b'\n'.join(parts)))
    o.pages.append(pikepdf.Page(page))
    o.docinfo[Name.Title] = pikepdf.String(os.path.basename(dst)[:-4] + ' - spot colour White (shown as black)')
    o.save(dst, min_version='1.6')
    print(dst, round(os.path.getsize(dst) / 1e6, 2), 'MB;', note)
    meta['white'] = dst; meta['white_note'] = note
    json.dump(meta, open(meta_path, 'w'), indent=1)

if __name__ == '__main__':
    main(sys.argv[1])
