# Quran Layout Builder — Qalun + Douri + Warsh + Hafs

This repository contains four independent layout workflows. All four now use the same DOC + SQLite layout engine where the legacy MS-DOC supplies physical page/line boundaries and the narration-specific SQLite supplies the canonical ordered Quran word/ayah sequence.

## 1. Qalun — DOC + SQLite workflow
Use `.github/workflows/build-layout.yml`.
Sources:
- `input/qalun/UthmanicQaloon1 Ver05.doc`
- `input/qalun/quran.sqlite`
- `config.json`

The Qalun DOC is authoritative for physical page/line boundaries; SQLite supplies the canonical Qalun word/ayah sequence. The workflow produces the same schema-v3 page/surah JSON contract used by Douri, Warsh, and Hafs.

## 2. Douri — DOC + SQLite workflow
Use `.github/workflows/build-douri.yml`.
Sources:
- `input/douri/UthmanicDoori1 Ver05.doc`
- `input/douri/quran.sqlite`
- `config-douri.json`

## 3. Warsh — DOC + SQLite workflow
Use `.github/workflows/build-warsh.yml`.
Sources:
- `input/warsh/UthmanicWarsh1 Ver05.doc`
- `input/warsh/quran.sqlite`
- `config-warsh.json`

## 4. Hafs — DOC + SQLite workflow
Use `.github/workflows/build-hafs.yml`.
Sources:
- `input/hafs/UthmanicHafs1 Ex1 Ver12.doc`
- `input/hafs/quran.sqlite`
- `config-hafs.json`

Hafs is built independently because its supplied DOC/SQLite pair requires constrained page-bounded fuzzy matching for some orthographic differences. It still emits the same schema-v3 endpoint format.

## Output
Each workflow writes to its own folder under `output/`: `qalun/`, `douri/`, `warsh/`, or `hafs/`.

Each layout workflow produces:
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

The Qalun source pair is pinned by SHA-256 in `config.json`, just like the other versioned DOC + SQLite pairs.
