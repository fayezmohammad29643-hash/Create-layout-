# Quran Layout Builder — Qalun + Douri + Warsh + Hafs

This repository contains four independent layout workflows. All four use the same DOC + SQLite layout engine where the legacy MS-DOC supplies physical page/line boundaries and the narration-specific SQLite supplies the canonical ordered Quran word/ayah sequence.

## 1. Qalun — DOC + SQLite workflow
Use `.github/workflows/build-layout.yml`.
Sources:
- `input/qalun/UthmanicQaloon1 Ver05.doc`
- `input/qalun/quran.sqlite`
- `config.json`

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

## 5. Ayah references for attachments
Use `.github/workflows/build-ayah-mapping.yml`.

This layer is intentionally separate from page layout. Its job is to answer: when the user is viewing a particular printed edition, which Hafs/Kufan ayah reference should a Tafsir, translation, note, or other attachment use?

### Source roles

**Qiraat Ayah Map** is the counting-system and boundary-evidence source. It provides structured mappings and provenance for Kufan, Last Madinan, First Madinan, Basran, and the other counting systems.

**Quran Text** is the edition-level source. Its `data/ayah-map.json` is built from shared word identity and describes the exact ayah numbering carried by its printed editions. The project uses it for the exact 6214-ayah Qalun edition and the 6217-ayah Douri edition.

### Current attachment mappings

- Hafs: direct Kufan numbering.
- Warsh 6213: local edition-exact mapping; the project intentionally keeps this SQLite edition instead of replacing it with the current 6214 Warsh release.
- Qalun 6214: exact quran-text edition mapping.
- Douri 6217: exact quran-text edition mapping for the 2022 `UthmanicDouri_V20` printing.

The workflow generates these files under `output/ayah_mapping/mappings/by-edition/`:

- `qalun-6214-to-hafs.json`
- `hafs-to-qalun-6214.json`
- `duri-6217-to-hafs.json`
- `hafs-to-duri-6217.json`
- `warsh-6213-to-hafs.json`
- `hafs-to-warsh-6213.json`

### Consumer rule

Persist attachments in **Hafs/Kufan numbering**. Translate to the selected reading at display time through the edition-exact map. Always handle `split`, `merged`, `shifted`, `unnumbered`, and reverse `covers_multiple`; never convert by adding or subtracting a fixed offset.

Quran Text explicitly notes that ayah numbers differ between printed riwayat while shared word numbers do not, and recommends joining data by word number rather than by `surah:ayah`. The project follows that rule for cross-edition reasoning. 

The generated output is not included in the source-only ZIPs. The workflow fetches the source, records the resolved generation date and SHA-256, generates the selected maps, and validates their per-surah counts against the project SQLite files.


## Runtime attachment mapping

The Flutter app consumes one generated file only for non-Hafs ayah-reference lookup:

`output/ayah_mapping/miltazim_ayah_mapping.json`

It maps the exact project editions `warsh-6213`, `qalun-6214`, and `douri-6217` to Hafs/Kufi ayah references. The detailed counting-system, rawi, edition, reverse, and source/provenance files remain build-time artefacts for validation and auditing.
