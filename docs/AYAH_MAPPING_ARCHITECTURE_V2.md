# Ayah mapping architecture v2

The project keeps three concepts separate:

- **Reading / rawi**: Hafs, Warsh, Qalun, Douri.
- **Counting system**: Kufan, Last Madinan, First Madinan, Basran, etc.
- **Printed edition**: the exact SQLite/DOC pair used by the app.

Qiraat Ayah Map describes counting systems. Quran Text's `ayah-map.json` describes how the exact printed editions distribute the shared word stream into their numbered ayahs.

For attachment lookup, the persistent reference is Hafs/Kufan. The displayed reading translates that reference through the edition-exact mapping. This prevents a valid but different Douri or Warsh edition from being silently treated as interchangeable.
