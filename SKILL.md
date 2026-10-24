---
name: packaging-prepress
description: Turn packaging or label artwork (Figma/Canva print PDFs, PNG exports) into verified, print-ready CMYK files for a printer - pouches, sachets, tubes, stickers, labels. Handles trim/bleed sizing without distortion, FOGRA39 CMYK conversion with ink limit, flattening of blend modes/transparency so files open cleanly in CorelDRAW (the usual "CDR" request), dielines and cut lines, white underprint plates for clear film, colour-accurate proofs, preflight/verification, and tidy delivery folders and zips. Use this whenever someone wants packaging files "print ready", "high-res", "CMYK", "for the printer", "in CDR/CorelDRAW format", asks to check whether existing print files meet a size like 216 x 108 mm, to make a sticker or label from a design, or asks why CMYK files look dark - even if they don't say "prepress".
---

# Packaging prepress

Convert design-tool exports into print files that a converter (pouch/gravure/flexo) or a sticker
printer can run without surprises, and **prove** they are right before anyone sends them.

The user is usually a brand owner, not a prepress operator. Speak plainly, make decisions that are
yours to make, ask only about things that change the printed product, and never claim a check you
did not run.

## Ground rules (learned the hard way)

- **Measure, don't trust labels.** "108 DPI" in a PNG header means nothing; effective resolution =
  pixels / physical size. A 858 px wide PNG for a 30 mm tube is ~726 ppi.
- **Render every deliverable in two engines** (Apple `sips` = what Preview shows, and MuPDF). A
  previous pipeline shipped PDFs that showed only a quarter of the page in Preview.
- **No fake detail.** Never "AI upscale" to impressive DPI numbers. 300-450 ppi suffices for photos;
  1200 ppi for flattened line art. Upscale only genuinely low-res assets, with methods that cannot
  invent content (two-tone edge rebuild, premultiplied-alpha Lanczos).
- **Uniform scaling only.** If the art's aspect ratio doesn't match the pouch, ask (see Decisions).
- **There is no CDR writer outside CorelDRAW.** Deliver a CorelDRAW-ready PDF (0 fonts, no live
  blend modes/opacity groups, CMYK, real boxes). Tell the user: CorelDRAW > File > Open the PDF >
  Save As CDR. Check `/Applications` first; if CorelDRAW exists you may automate it, otherwise say so.
- **Mac Preview shows CMYK dull/dark** regardless of profile. Judge colour from proofs made by
  `proof.py` (FOGRA39 -> sRGB, relative colorimetric), never from `sips` renders.
- **Never delete or overwrite the user's files.** New outputs go in new folders. Replaced files go
  to an archive folder; duplicates go to the Trash only after a byte-for-byte `cmp`.

## Setup (once per machine)

```bash
bash <skill>/scripts/setup.sh          # venv + libs + FOGRA39 profile in ~/.packaging-prepress
PY=~/.packaging-prepress/venv/bin/python
```
Run scripts as `$PY -I <skill>/scripts/<name>.py ...`. Networks can be slow; setup retries.
`PREPRESS_ICC` overrides the profile path.

## Step 1 - Inventory and diagnose

1. List files; identify per flavour/SKU: source PDFs ("(print).pdf"), PNG mock-ups, dielines,
   any earlier "high-res"/CDR attempts.
2. For each PDF: page size (MediaBox/TrimBox/BleedBox in mm), fonts, image list with effective ppi
   at final size (`page.get_image_info()`), transparency (ExtGState BM/ca/SMask), live text layers.
3. Render existing "final" files in both engines and look at them. For `.cdr.zip` files read
   `metadata/metadata.xml` (page size) and `color/color.xml` (RGB vs CMYK) and view
   `metadata/thumbnails/*.bmp` - this is how broken CorelDRAW imports are caught.
4. Read the dieline: real dimensions, seals, hang hole, window ("transparent area"), tear notch.
5. Report findings plainly before rebuilding (what is wrong, why it matters in print).

## Step 2 - Decisions to put to the user (AskUserQuestion, recommended option first)

Only ask what changes the printed product; propose defaults for the rest:
- **Size/fit** when art aspect != pouch aspect: keep art proportions (e.g. 113 x 216), crop sides
  (check text margins first), or shrink + extend. Check how close text sits to each edge first.
- **Window**: clear window (remove mock-up film texture, no ink, + white plate) vs print as designed.
- **Hang hole**: punched (move dot to non-printing Dieline layer) vs printed dot.
- **Stickers**: body only (drop seal/tear/OPEN tab) vs full design; sticker size.
If an answer doesn't arrive, go with the recommended option and say so; keep choices as flags.

## Workflow A - Vector print PDF (outer pouch from Figma/Canva PDF)

```bash
$PY -I scripts/build.py SRC.pdf OUT_DIR C Front WORK --trim 113x216 --bleed 3 [--fit height|width] [--keep-window] [--print-hole]
$PY -I scripts/verify.py WORK/C_Front.json WORK/vimg      # independent checks
$PY -I scripts/whiteplate.py WORK/C_Front.json            # spot "White" underlay (clear film)
$PY -I scripts/proof.py OUT_DIR/C_Outer_Front_Print_CMYK_113x216mm.pdf
```
What build.py does (expects the Figma structure: page draws one form `/X1` holding the design):
plain vector blocks stay vector with sRGB->FOGRA39 colour (exact neutrals -> K only); every block
using transparency is rendered **with its real backdrop** at 1200 ppi and placed back as a CMYK
bitmap masked to exactly the pixels it changes; two-tone Overlay patterns are rebuilt cleanly;
small RGBA assets get premultiplied Lanczos; side bleed uses Figma's real off-frame art, top/bottom
bleed is edge extension; hidden accessibility text/fonts dropped; Dieline layer (trim, hang hole,
window, tear ticks) as non-printing spot; OutputIntent FOGRA39; TAC <= 300 %.
If the source isn't Figma-structured (no single `/X1` form), adapt `split_blocks` input or fall
back to Workflow B on a high-res render - and tell the user text will be raster.

**Pass criteria (verify.py JSON)** - report these numbers to the user:
- `issues` empty: 0 fonts, no live transparency, DeviceCMYK only, TAC <= 300.5
- `pipeline_dE_flat_areas.mean` < 1.5 and `p99` < 3 (colour fidelity vs a perfect FOGRA39 conversion)
- `bleed_unprinted_pct` == 0
- `apple_blank_pct` explained (e.g. equals the clear-window area)
- Look at the `_vs_original` heatmap: only intended edits (window, hole) may light up.
Residual high dE only on 1-px edges or distressed textures is sampling, not error - confirm by
zooming both renders at 1200 ppi before dismissing anything.

## Workflow B - Sticker/label from flattened PNG art

Digital label presses print ~800 dpi, so raster type at >= 700 ppi is fine; no vectorising needed.
```bash
$PY -I scripts/sticker.py --work WORK --art 30x173 --size 24x140 --radius 2 --bleed 2 --safe 1.5 \
   --top-clean-mm 17.3 --text-rgb 248,231,162 \
   --job SRC.png OUT.pdf "Title" [--job ...]          # all SKUs together -> one common scale
$PY -I scripts/verify_sticker.py WORK
$PY -I scripts/proof.py OUT.pdf
```
sticker.py maps original pixels 1:1 (no resampling), picks the largest uniform scale that keeps
every type pixel >= `--safe` mm inside the cut, aligns art to the bottom, fills the cleaned top
strip with the detected background colour, mirrors edges into the bleed, and draws the cut path as
spot `CutContour` (overprint). Inspect the text mask if margins look odd (photo highlights in the
text colour can masquerade as type; the mask excludes pixels within 0.9 mm of photos).

## Delivery

- Folder per job: `<Flavour>/<Front|Back>/` with `*_Print_CMYK_*.pdf`, `*_White_Underlay_*.pdf`,
  `*_Proof_*.jpg`; plus a printer README and `Preflight_Report.csv` (templates in
  `references/templates.md`). Zip with `zip -r -X ... -x "*.DS_Store"` and `unzip -t` it.
- Organise the project as `01 Print Ready` / `02 Design Source` / `03 Reference` /
  `99 Archive - superseded`, with a `READ ME FIRST.txt` (template in references).
- In the final message: what was fixed and why, the measured results, and the short list of things
  the printer must confirm (size, seals vs text margin, substrate/white, window, hole, gamut).
- Gamut: report colours that CMYK cannot reach (dE from sRGB) and offer spot/extended-gamut options.

## References
- `references/lessons.md` - failure modes found in earlier pipelines and debugging tips (read when
  a check fails or numbers look strange).
- `references/templates.md` - printer README, sticker README, READ ME FIRST, follow-up email.
