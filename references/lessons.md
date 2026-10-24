# Lessons and debugging notes

## Defects found in an earlier "super-resolution + CMYK" pipeline (do not repeat)
- Declared a 108 x 216 trim but wrote TrimBox = BleedBox = MediaBox (112.9 x 216): no trim, no
  top/bottom bleed. Always set TrimBox/BleedBox explicitly and check them.
- Output rendered only the bottom-left 25 % in Apple Preview (MuPDF looked fine). Always render in
  both engines.
- Claimed to strip Type 3 OCR fonts / StructTreeRoot - they were still there. Verify claims.
- "8,244 DPI" images: pointless bloat (logos 12 mm wide). Effective ppi >= 300 is enough.
- Kept live Overlay/HardLight + opacity groups in CMYK -> CorelDRAW import showed the window black,
  ignored form clipping (art spilled off the page), and saved RGB at 4x size. Flatten transparency.
- Assumed tube = 31.2 x 180 mm and "108 DPI"; dieline said 30 x 173 mm (PNG = 726 ppi).
- Non-uniform squash to hit a size = distortion. Uniform scale only; ask about crop vs size.

## Bugs hit while building this pipeline (already fixed in scripts - keep them fixed)
- uint8 overflow when negating an L* value turned every neutral grey into 100 % K. Cast to float.
- Pillow LAB images store a*/b* as signed int8 inside uint8 - view as int8 before computing dE.
- MuPDF colour-manages CMYK when ICC is on; measure raw CMYK with `fitz.TOOLS.set_icc(False)`.
- Naming a helper `inspect.py` shadows the stdlib and breaks pikepdf imports.
- pikepdf objects die when their Pdf is garbage-collected: keep the `Pdf` in a variable.
- Reference renders for dE must cover only the trim area (bleed is legitimately different) and
  ignore the last partial pixel row/column of a render.
- Measuring at a rounded ppi (929 vs 928.95) shifts edges by sub-pixels -> fake errors. Render at
  the exact zoom when checking pixel identity.

## Colour facts to tell users
- Preview/`sips` display CMYK dull and dark (e.g. 213,37,24 red shows as 185,61,53). Not a file problem.
- Out-of-gamut screen colours: vivid blue (26,96,226) prints ~ (53,94,169), dE 33; vivid oranges
  dE 9-13. Options: Pantone spot plate (pouch), extended-gamut CMYKOGV (digital labels), or accept.
- Pure black type -> 100 K only (registration-safe). Rich black only for large areas, if at all.

## Two-tone pattern rebuild
Gaussian (sigma 0.8 px) to kill JPEG speckle -> cubic zoom x k -> signed distance from
(value - mid)/|gradient| -> 1 px AA ramp between the two medians. Detect two-tone by: grayscale,
< 3 % of pixels in the middle 50 % band between the two tones.

## Transparency flattening (build.py)
Top-level blocks of `/X1` are classified; consecutive non-plain blocks form a run. For each run:
low-res diff of render(prefix before) vs render(prefix after) -> bbox; high-res renders of both;
mask = pixels that changed (dilated 2 px); place `after` (full rectangle, so it is still correct
if an importer ignores the mask) with an 8-bit SMask, at the run's stacking position.
