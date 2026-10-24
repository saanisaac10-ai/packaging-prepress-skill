# Templates

Adapt sizes, names and decisions to the job. Keep wording plain.

## Printer README (pouch / converter)
```
<BRAND> OUTER POUCH - PRINT-READY FILES (<W> x <H> mm)

FOLDERS
  <SKU>/Front and <SKU>/Back each contain:
   *_Print_CMYK_*.pdf      PRINT MASTER (send this)
   *_White_Underlay_*.pdf  White ink plate for clear film (shown as black = white ink)
   *_Proof_*.jpg           Visual proof only (not for print)

PAGE GEOMETRY
  Trim <W> x <H> mm (TrimBox); bleed 3 mm all sides -> sheet <W+6> x <H+6> mm (BleedBox = MediaBox)
  Artwork scaled uniformly (no stretch, no crop).

COLOUR
  CMYK only, output intent Coated FOGRA39 (ISO Coated v2), sRGB -> FOGRA39 relative colorimetric
  + black point compensation, total ink <= 300 %. Pure black text/lines = 100 % K only.

STRUCTURE
  0 fonts, no live blend modes / opacity / soft-mask groups. Flat shapes and type remain curves;
  areas that used transparency are pre-flattened to 1200 ppi CMYK bitmaps.

LAYERS / SPOT COLOURS
  "Dieline (non-printing)" layer: trim, hang hole, window outline, tear ticks (spot "Dieline", not printed).
  White_Underlay PDF: spot "White" - everywhere except the clear window, choked 0.1 mm.
  Use only for clear film.

PLEASE CHECK WITH THE CONVERTER
  - Side-seal width vs back-panel text distance from the trim.
  - Hang-hole punch size/shape, tear-notch position.
  - Any blank back-panel fields (batch, MRP ...) are left for coding.

OPENING IN CORELDRAW
  File > Open the *_Print_CMYK_*.pdf, then File > Save As > CDR. No font dialog (there are no fonts).
  Keep colour management on "Preserve CMYK numbers".
```

## Sticker README
```
<BRAND> TUBE STICKERS - PRINT-READY FILES (<W> x <H> mm)
Finished size <W> x <H> mm, corner radius <R> mm, bleed 2 mm -> artboard <W+4> x <H+4> mm.
Cut line: vector rounded rectangle in spot "CutContour", 0.25 pt, overprint (die/kiss-cut path; do not print).
Safe zone: all text at least 1.5 mm inside the cut line.
Artwork: uniform scale from the tube design, "body only" (seal strip and tear tab removed), original
pixels mapped 1:1 (no resampling, no AI upscaling), ~930 ppi at final size, mirrored bleed.
Colour: CMYK only, Coated FOGRA39, TAC <= 300 %, 0 fonts.
Substrate: white vinyl/paper -> print as is. Clear vinyl -> needs a white underprint.
Please confirm the sticker fits the flat area of the tube clear of the seals.
```

## READ ME FIRST (project folder guide)
```
01 Print Ready (send to printers)   final files + zips for sending
02 Design Source (originals)        editable originals - do NOT send to printers
03 Reference                        sample videos / photos
99 Archive - superseded             old faulty files, safe to delete
File-name codes: <SKU codes>
Viewing tip: Mac Preview shows CMYK PDFs darker than they print - judge colour from the Proof JPGs.
```

## Follow-up message to a printer after proofs change (print files unchanged)
```
Hi, quick follow-up. The print PDFs are final and unchanged. Please replace the proof JPGs with the
attached ones - the earlier proofs were rendered too dark. Colour reference is FOGRA39 CMYK as in the PDFs.
Before plates, please confirm:
1. Pouch size <W x H> mm
2. Side seal width (back-panel text is ~<x> mm from the side edges)
3. Film type: clear (use White Underlay files) or white/metallised
4. Window is clear (no ink) and the hang hole is punched, not printed
```

## Preflight_Report.csv columns
File, Trim, Bleed sheet, Fonts, Live transparency, Image colour space, Max TAC images %,
Max TAC vectors %, Mean dE flat areas, p99 dE flat areas, Bleed fully covered, Size MB
(built from each `*_verify.json`).
