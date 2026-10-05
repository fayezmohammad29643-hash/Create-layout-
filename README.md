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

## 5. Unified ayah mapping for attachments
Use `.github/workflows/build-ayah-mapping.yml`.

The old multi-file JSON mapping method has been retired. The workflow now creates one SQLite database:

`output/ayah_mapping/ayah_mapping.sqlite`

This database combines all four project editions:

| edition_id | edition | ayah count |
|---:|---|---:|
| 1 | Hafs | 6236 |
| 2 | Warsh | 6213 |
| 3 | Qalun | 6214 |
| 4 | Douri | 6217 |

Hafs is the reference numbering system. Warsh, Qalun, and Douri are matched to Hafs by aligning their actual project SQLite ayah texts after conservative Arabic normalization. No numeric offset is used and the project source SQLite files are not replaced.

### Runtime database schema

`ayah_mapping.sqlite` contains three tables:

- `metadata` — compact build and schema information.
- `editions` — numeric edition IDs used by the runtime database.
- `ayah_hafs_map` — one or more Hafs references for each source edition/surah/ayah.

The runtime table is:

```sql
CREATE TABLE ayah_hafs_map (
    edition_id INTEGER NOT NULL,
    surah INTEGER NOT NULL,
    ayah INTEGER NOT NULL,
    hafs_ayah INTEGER NOT NULL,
    PRIMARY KEY (edition_id, surah, ayah, hafs_ayah)
) WITHOUT ROWID;
```

If one source ayah contains more than one Hafs ayah, it has multiple rows. For example, the intended mapping includes:

```text
Warsh 1:1 → Hafs 1:2
Warsh 1:6 → Hafs 1:7
Warsh 1:7 → Hafs 1:7
Warsh 2:1 → Hafs 2:1 and Hafs 2:2
Warsh 16:123 → Hafs 16:123 and Hafs 16:124
Douri 67:9 → Hafs 67:9
```

The app does not display Hafs numbers to the user. They are internal keys used to resolve Tafsir, translations, and other Hafs-indexed attachments.

For Al-Fatihah, Hafs ayah 1 is the basmalah while the numbered first ayah in the non-Hafs project editions is `الحمد لله رب العالمين`; therefore the non-Hafs `1:1` maps to Hafs `1:2`.

## Runtime rule

The Flutter app needs only:

`ayah_mapping.sqlite`

for cross-edition ayah references. The build scripts, source SQLite files, DOC files, and validation report remain part of the build project and are not required by the runtime feature.
