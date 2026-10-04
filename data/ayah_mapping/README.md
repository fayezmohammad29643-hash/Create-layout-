# Ayah-count and edition mapping

This directory documents the attachment-reference layer used by the app.

## Two source layers

**Qiraat Ayah Map** is the scholarly/counting-system baseline. It records the six counting systems and their boundary differences, with structured `mapped`, `merged`, `split`, `shifted`, and `unnumbered` semantics.

**Quran Text** is the edition-level source used when the app's actual SQLite matches one of its printed KFGQPC editions. Its ayah map is built from the shared word identity and therefore preserves the exact printed edition's numbering. Quran Text explicitly advises joining data by shared word number rather than by `surah:ayah`.

## Current four layouts

- **Hafs**: Kufan; direct numbering, 6236 ayahs.
- **Warsh 6213**: the project's older coherent edition; exact local override is used only for the known al-Nahl difference.
- **Qalun 6214**: exact quran-text edition map.
- **Douri 6217**: exact quran-text edition map for the 2022 `UthmanicDouri_V20` printing, measured as First Madinan in its printed metadata. The qiraat-ayah-map Basran table is not substituted for this edition.

## Consumer rule

Persist attachments in **Hafs/Kufan references**. When a reading is displayed, translate the reference through that reading's **edition-exact** map. Always branch on relation/status; never collapse `split` or `covers_multiple` to one integer and never compute an offset.

The generated files are intentionally excluded from this source-only project ZIP. The workflow fetches the pinned/expected source, records its generation date and SHA-256, generates the exact edition mappings, and validates every edition's per-surah count against the project's SQLite.

## Miltazim runtime file

The Flutter app does not need to ship the detailed `by-counting-system`, `by-rawi`,
`by-edition`, reverse, or source/provenance artefacts. Those remain build-time outputs
for validation and auditing.

The single runtime file is:

`output/ayah_mapping/miltazim_ayah_mapping.json`

It contains only forward references from the project's three non-Hafs editions to
Hafs/Kufi:

- `warsh-6213`
- `qalun-6214`
- `douri-6217`

Runtime lookup is edition ayah -> one or more Hafs ayahs. It never uses a numeric offset.
