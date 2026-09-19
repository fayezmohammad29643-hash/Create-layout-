# Quran Layout Builder — DOCX + Quranpedia SVG

هذا المشروع ينشئ ملف `layout` مستقلًا لكل سورة.

## مصدر كل نوع من البيانات

### DOCX
هو المصدر الأساسي لـ:

- ترتيب السور وأرقامها.
- أرقام الصفحات وانتقالات الصفحات المرسومة في المستند.
- انكسار السطور الحقيقي.
- `first word` و`end word` لكل سطر.
- `wordIndex` ورقم السورة والآية.
- `ayah_marker` عندما ينتهي السطر برقم الآية.

### Quranpedia quran-svg
هو المصدر الأساسي لإحداثيات السطر. أثناء GitHub Action لا تُرفع ملفات SVG إلى هذا المستودع؛ بل تُنزّل الصفحات المطلوبة مؤقتًا من:

`quranpedia/quran-svg` → `mushafs/qalon/kfqc/svg/{page}.svg`

ثم تُمرر إلى `scripts/extract_lines.py`.

بعد انتهاء التشغيل تُحذف ملفات SVG المؤقتة.

## البنية

```text
quran-layout-builder/
├── .github/
│   └── workflows/
│       └── build-layout.yml
├── input/
│   └── mushaf.docx
├── output/
├── scripts/
│   ├── build_layout.py
│   ├── extract_lines.py
│   └── check_layout.py
├── config.json
└── README.md
```

لا يوجد `input/svg/` لأن SVG يأتي من Quranpedia أثناء التشغيل.

## التشغيل في GitHub

1. ارفع ملف المصحف إلى `input/mushaf.docx`.
2. ارفع مجلد `scripts` والملفات الموجودة فيه.
3. ارفع `config.json`.
4. ارفع `.github/workflows/build-layout.yml`.
5. افتح **Actions** ثم **Build Quran Layouts**.
6. اضغط **Run workflow**.
7. بعد انتهاء التشغيل ستظهر ملفات JSON داخل `output/`، ملف مستقل لكل سورة.

## الناتج

مثال:

```text
output/
├── 001_الفاتحة.json
├── 002_البقرة.json
├── 003_ال عمران.json
...
└── 114_الناس.json
```

كل ملف يحافظ على بنية ملف الـlayout المرجعي: `surahId`, `surahName`, `ayaCount`, `startPage`, `endPage`, `pages`, ثم `lines`، ولكل سطر `start`, `end`, وإحداثيات `y_top`, `y_bottom`, `x_start`, `x_end`.

## مثال

```json
{
  "line": 1,
  "start": {
    "type": "word",
    "surah": 2,
    "ayah": 5,
    "word": "إِنَّ",
    "wordIndex": 1
  },
  "end": {
    "type": "word",
    "surah": 2,
    "ayah": 5,
    "word": "تُنذِرۡهُمُۥ",
    "wordIndex": 9
  },
  "y_top": 12.2,
  "y_bottom": 47.53,
  "x_start": 336.11,
  "x_end": 12.9,
  "verses_on_line": ["2:5"]
}
```

`englishName` و`type` يظلان `null` لأن الـDOCX المستخدم في هذا المشروع لا يقدّم هاتين المعلومتين.
