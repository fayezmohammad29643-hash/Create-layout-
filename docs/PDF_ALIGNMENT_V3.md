# PDF Quran Layout Extraction v3.1

This project now uses a rawdict-first PDF alignment pipeline for Douri/Warsh layouts.

## Core change

The PDF custom font text is not treated as Unicode Quran text. Instead, the extractor reads PyMuPDF `rawdict` parent text-line objects and promotes their geometry to **visual units**. In the current mushaf PDFs, these objects correspond closely to printed words and ayah-marker objects.

SQLite remains the canonical source for the ordered Quran words and ayah markers.

The page aligner therefore follows this order:

1. Detect the qiraah printed in the PDF header and compare it with the requested build.
2. Extract custom-font raw visual units without a fixed top/bottom clipping margin.
3. Cluster units into physical Quran lines by Y position.
4. Separate bismillah and ayah-marker units structurally.
5. Use the PDF visual word count directly when it matches the SQLite page word count.
6. Use a bounded page-level DP when the page delta is within a larger budget, but require the resulting corrections to remain local (limited per physical line and limited in the number of corrected lines).
7. Use marker position (`start`, `middle`, `end`) as semantic evidence without assuming that a marker rendered at the start of the next line belongs visually inside the previous line.

## Source safety

The builder fails before generation when the PDF qiraah does not match the configured qiraah. This prevents a different-rivayah PDF from being aligned against the requested SQLite database.

The Douri workflow also reports the page number and visual/SQLite counts when a correction budget is exceeded or when a proposed correction is not local enough. The page budget is intentionally separate from the per-line safety budget so that several small, independent corrections can be accepted without allowing one large corrupted line to pass.

`input/warsh/mushaf.pdf` identifies itself as **Warsh** and is compatible with `config-warsh.json`.

## Validation performed

The updated Warsh build generated:

- 604 pages
- 114 surahs
- 8,827 physical Quran lines
- 0 structural validation errors
- 6 pages retained in `review.json` because their raw visual word count required bounded local corrections

The builder records correction magnitude in `review.json` and rejects corrections that are too concentrated in a single physical line or touch too many lines.
