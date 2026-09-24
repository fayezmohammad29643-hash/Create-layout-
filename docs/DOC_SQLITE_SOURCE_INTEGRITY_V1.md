# DOC + SQLite source integrity

The DOC+SQLite builder depends on a matching text edition. Warsh and Douri are not interchangeable: their ayah segmentation/counting differs in places such as Sūrat al-Shams and al-Zalzalah.

V8 could produce a formally valid JSON stream when the wrong SQLite file was placed under the right filename, because the builder treated SQLite as the word/ayah authority and the checker validated against that same file.

This update adds exact SHA-256 fingerprints for the versioned Warsh and Douri source pairs. The builder now refuses a mismatched pair before layout generation, and the checker verifies the same hashes again.

Expected V8 source pairs:

- Warsh DOC: `UthmanicWarsh1 Ver05.doc`
- Warsh SQLite: `quran.sqlite`
- Douri DOC: `UthmanicDoori1 Ver05.doc`
- Douri SQLite: `quran.sqlite`

The fingerprints are stored in `config-warsh.json` and `config-douri.json`.
