# Ayah mapping attribution

This project uses two complementary upstream data layers:

## Qiraat Ayah Map
https://github.com/quran-ws/qiraat-ayah-map

Used for counting-system definitions, boundary evidence, rawi metadata, and generic Kufan↔counting-system mappings.

Dataset licence: CC BY 4.0.
Tooling licence: MIT.

## Quran Text
https://github.com/quran-ws/quran-text

Used for exact printed-edition ayah correspondence via `data/ayah-map.json` and the shared word identity across the seven printed riwayat.

Quran Text states that the displayed text comes from King Fahd Glorious Qur'an Printing Complex releases; redistribution of the Qur'an text is subject to KFGQPC terms. The project stores the map source metadata and SHA-256 in the generated manifest.

### Edition selection in this project

- Qalun: 6214-ayah exact edition map.
- Douri: 6217-ayah exact edition map.
- Warsh: project-specific 6213-ayah local override.
- Hafs: direct Kufan numbering.
