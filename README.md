# Quran Layout Builder — Qalun + Douri + Warsh

المشروع يبني بيانات Layout للروايات الثلاث مع الحفاظ على مسار قالون الحالي، واستخدام مصدر DOC + SQLite للدوري وورش.

## 1. قالون

يبقى `.github/workflows/build-layout.yml` مع `scripts/build_layout.py` كما هو.

المصدر:
- `input/mushaf.docx`
- `config.json`

الـDOCX هو مصدر الحقيقة لانكسار السطور والصفحات، مع بياناته الحالية الخاصة بقالون.

## 2. الدوري

يستخدم `.github/workflows/build-douri.yml`:

- `input/douri/UthmanicDoori1 Ver05.doc`
- `input/douri/quran.sqlite`
- `config-douri.json`

الـDOC هو المرجع لتقسيم الصفحات والأسطر. SQLite هو المرجع لتسلسل الكلمات و`wordIndex`. حقل `page` في SQLite لا يستخدم لفرض حدود PDF/صفحة.

## 3. ورش

يستخدم `.github/workflows/build-warsh.yml`:

- `input/warsh/UthmanicWarsh1 Ver05.doc`
- `input/warsh/quran.sqlite`
- `config-warsh.json`

المحرك المشترك هو `scripts/build_doc_layout.py`، والتحقق بواسطة `scripts/check_doc_layout.py`.

## الناتج

لكل رواية:

- `pages/page_001.json` ... `page_604.json`
- `surahs/001.json` ... `surahs/114.json`
- `manifest.json`

Schema السطر:

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
    "type": "ayah_marker",
    "surah": 114,
    "ayah": 6,
    "number": 6
  }
}
```

## مبدأ المطابقة

في الدوري وورش لا تعتمد الخوارزمية على المسافات داخل DOC وحدها. يتم تطبيع النص للسطر ثم مطابقته مع **تسلسل SQLite**، بحيث يمكن التعامل مع:

- كلمتين ملتصقتين في DOC رغم كونهما كلمتين في SQLite.
- كلمة واحدة مقسمة إلى أكثر من token في DOC.
- أرقام الآيات الملتصقة بكلمات بسبب RTL.
- الآية التي تعبر حد الصفحة.

يحتاج GitHub Actions إلى `antiword` لقراءة ملفات `.doc`.

تفاصيل المنهج في `docs/DOC_SQLITE_ALIGNMENT_V1.md`.
