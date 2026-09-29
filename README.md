# Quran Layout Builder — Qalun + Douri + Warsh + Hafs

This repository contains four independent layout workflows:

## 1. Qalun — existing DOCX workflow
The `.github/workflows/build-layout.yml` workflow is the Qalun workflow, and `scripts/build_layout.py` remains the DOCX builder.
It reads:
- `input/mushaf.docx`
- `config.json`

It continues to build the existing Qalun layout exactly from the DOCX source.

## 2. Douri — DOC + SQLite workflow
Use `.github/workflows/build-douri.yml`.
Sources:
- `input/douri/UthmanicDoori1 Ver05.doc`
- `input/douri/quran.sqlite`
- `config-douri.json`

The DOC is authoritative for physical page/line boundaries; SQLite supplies the canonical word/ayah sequence. The builder creates compact schema-v3 endpoints:
`first_word` and `end_word`.

## 3. Warsh — DOC + SQLite workflow
Use `.github/workflows/build-warsh.yml`.
Sources:
- `input/warsh/UthmanicWarsh1 Ver05.doc`
- `input/warsh/quran.sqlite`
- `config-warsh.json`

The same generic DOC + SQLite engine is used. The DOC supplies physical page/line boundaries and SQLite supplies the canonical word/ayah sequence.


## 4. Hafs — DOC + SQLite workflow
Use `.github/workflows/build-hafs.yml`.
Sources:
- `input/hafs/UthmanicHafs1 Ex1 Ver12.doc`
- `input/hafs/quran.sqlite`
- `config-hafs.json`

The supplied Hafs DOC is authoritative for the physical 604-page and line layout. SQLite supplies the canonical word/ayah sequence. For this exact Hafs source pair, SQLite `page` is used only as a secondary page-range constraint; it does not define line breaks. Because the DOC and SQLite use different Uthmanic/Unicode spellings in some places, `scripts/build_hafs_layout.py` uses constrained page-bounded fuzzy matching while emitting the same compact schema-v3 endpoints used by the DOC+SQLite workflows.

Hafs is built independently so the existing Qalun, Douri, and Warsh builders remain unchanged.

## Output
Qalun is written to `output/qalun/`.
Douri is written to `output/douri/`.
Warsh is written to `output/warsh/`.
Hafs is written to `output/hafs/`.

Each layout workflow produces its own `pages/`, `surahs/`, and manifest inside its output folder.

The DOC+SQLite workflows produce the compact page/surah JSON outputs below:
- `pages/page_001.json` ... `page_604.json`
- `surahs/001.json` ... `surahs/114.json`
- `manifest.json`

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
