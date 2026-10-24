#!/usr/bin/env python3
"""Accurate soft proof: raw CMYK render -> sRGB via FOGRA39 (relative colorimetric + BPC)."""
import sys, os
import numpy as np, pymupdf as fitz
from PIL import Image, ImageCms
TOOLS = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, TOOLS)
from build import FOGRA
c2r = ImageCms.buildTransform(ImageCms.ImageCmsProfile(FOGRA), ImageCms.createProfile('sRGB'), 'CMYK', 'RGB',
                              renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
def proof(pdf, jpg, height_px):
    fitz.TOOLS.set_icc(False)                       # pass CMYK numbers through untouched
    d = fitz.open(pdf); r = d[0].rect
    z = height_px / r.height
    pix = d[0].get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csCMYK, alpha=False)
    fitz.TOOLS.set_icc(True)
    im = Image.frombytes('CMYK', (pix.w, pix.h), pix.samples)
    ImageCms.applyTransform(im, c2r).save(jpg, quality=92, subsampling=0)
if __name__ == '__main__':
    for pdf in sys.argv[1:]:
        jpg = pdf.replace('_Print_CMYK_', '_Proof_').replace('_Sticker_CMYK_', '_Sticker_Proof_').replace('.pdf', '.jpg')
        proof(pdf, jpg, 2600 if '_Print_CMYK_' in pdf else 2400); print('ok', os.path.basename(jpg))
