# Quran Layout Builder — Qalun + Douri + Warsh

This repository contains three independent layout workflows:

## 1. Qalun — existing DOCX workflow
The existing `.github/workflows/build-layout.yml` and `scripts/build_layout.py` are preserved.
It reads:
- `input/mushaf.docx`
- `config.json`

It continues to build the existing Qalun layout exactly from the DOCX source.

## 2. Douri — new PDF workflow
Use `.github/workflows/build-douri.yml`.
Sources:
- `input/douri/mushaf.pdf`
- `input/douri/quran.sqlite`
- `config-douri.json`

The PDF text is NOT trusted as Quran text. PDF geometry supplies line evidence; SQLite supplies the canonical word/ayah sequence. The builder creates compact schema-v3 endpoints:
`first_word` and `end_word`.
Pages with weak evidence are recorded in `output/douri/review.json`.

## 3. Warsh — new PDF workflow
Use `.github/workflows/build-warsh.yml`.
Sources:
- `input/warsh/mushaf.pdf` (must be supplied)
- `input/warsh/quran.sqlite`
- `config-warsh.json`

The same generic PDF + SQLite engine is used. A page is never silently considered verified when the geometric evidence is weak.

## Output
Qalun keeps its existing `output/` location.
Douri is written to `output/douri/`.
Warsh is written to `output/warsh/`.

Each PDF workflow produces:
- `pages/page_001.json` ... `page_604.json`
- `surahs/001.json` ... `surahs/114.json`
- `manifest.json`
- `review.json`

The output line schema is compact:

```json
{
  "line": 6,
  "first_word": {
    "type": "word",
    "surah": 114,
    "ayah": 1,
    "word": "قُلۡ",
    "wordIndex": 1
  },
  "end_word": {
    "type": "word",
    "surah": 114,
    "ayah": 3,
    "word": "إِلَٰهِ",
    "wordIndex": 1
  }
}
```

Ayah markers use:

```json
{
  "type": "ayah_marker",
  "surah": 114,
  "ayah": 6,
  "number": 6
}
```
