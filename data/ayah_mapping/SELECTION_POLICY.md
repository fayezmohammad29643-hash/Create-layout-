# Mapping source selection

1. Use **Qiraat Ayah Map** for counting-system evidence and provenance.
2. Use **Quran Text `data/ayah-map.json`** for exact printed-edition correspondence when the project's SQLite edition matches the edition represented there.
3. Use the project-local **Warsh 6213 override** because the app intentionally uses a 6213-ayah Warsh edition rather than the current 6214-ayah Quran Text Warsh release.
4. Use direct Kufan numbering for Hafs.
5. Never infer correspondence with a numeric offset.

### Exact editions currently consumed

- Qalun 6214 → `mappings/by-edition/qalun-6214-to-hafs.json`
- Douri 6217 → `mappings/by-edition/duri-6217-to-hafs.json`
- Warsh 6213 → `mappings/by-edition/warsh-6213-to-hafs.json`

### Relation handling

The consumer must preserve `same`, `merged`, `split`, `shifted`, and `unnumbered`, and for reverse mappings it must preserve `covers_multiple`. A split may return two target ayahs; a merged target may cover multiple Hafs ayahs.
