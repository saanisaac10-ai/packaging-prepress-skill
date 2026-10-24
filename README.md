# packaging-prepress

A Claude skill (also usable as plain Python scripts) that turns packaging and label artwork
(Figma/Canva print PDFs, PNG exports) into verified, print-ready CMYK files:

- trim/bleed sizing with uniform scaling (no distortion)
- sRGB -> Coated FOGRA39 conversion, total ink limit 300 %, exact-neutral blacks as K only
- transparency and blend modes flattened so files open cleanly in CorelDRAW
- dieline layer, white underprint plate for clear film, sticker cut lines (spot `CutContour`)
- colour-accurate proofs and independent preflight checks (fonts, transparency, TAC, bleed, colour error)

See `SKILL.md` for the workflow, `scripts/` for the tools, `references/` for notes and templates.

## Install
```bash
git clone <this repo> ~/.claude/skills/packaging-prepress
bash ~/.claude/skills/packaging-prepress/scripts/setup.sh   # venv, libraries, FOGRA39 profile
```
`setup.sh` downloads Adobe's `CoatedFOGRA39.icc` from Adobe's site (not bundled here) and installs
numpy, pillow, pikepdf, pymupdf and scipy. Note PyMuPDF is AGPL-licensed.

## Scope
The vector workflow (`scripts/build.py`) expects Figma-style PDFs where the page draws one form
XObject `/X1` containing the design. Other sources: see `SKILL.md`. There is no CDR writer here:
output is a CorelDRAW-ready PDF to open in CorelDRAW and save as CDR.
