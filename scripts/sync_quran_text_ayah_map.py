#!/usr/bin/env python3
"""Fetch the exact printed-edition ayah map from quran-text.

Qiraat Ayah Map is the scholarly/counting-system baseline. quran-text is the
edition-level source used here when the app's SQLite matches a published
printed edition (notably Qalun 6214 and Douri 6217).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(
                url,
                headers={"User-Agent": "Multazim-Ayah-Mapping/1.0", "Accept": "application/json,text/plain,*/*"},
            )
            with urlopen(request, timeout=timeout) as response:
                data = response.read()
                if not data:
                    raise RuntimeError(f"Empty response from {url}")
                return data
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=Path("config-ayah-mapping.json"), type=Path)
    ap.add_argument("--output-dir", default=Path("output/ayah_mapping"), type=Path)
    args = ap.parse_args()

    config = json.loads(args.config.resolve().read_text(encoding="utf-8"))
    source = config["exact_edition_source"]
    out = args.output_dir.resolve() / "sources/quran-text"
    out.mkdir(parents=True, exist_ok=True)

    map_url = source["ayah_map_url"]
    map_bytes = fetch(map_url)
    map_doc = json.loads(map_bytes.decode("utf-8"))
    if map_doc.get("format") != "quran-ayah-map":
        raise SystemExit("Unexpected quran-text ayah-map format")
    expected_generated = source.get("expected_generated")
    actual_generated = map_doc.get("generated")
    if expected_generated and actual_generated != expected_generated:
        raise SystemExit(
            f"quran-text source date changed: expected {expected_generated}, got {actual_generated}. "
            "Update config-ayah-mapping.json only after reviewing the new source."
        )

    map_path = out / "ayah-map.json"
    map_path.write_bytes(map_bytes)

    metadata = {
        "source": source,
        "resolved_generated": actual_generated,
        "sha256": sha256_bytes(map_bytes),
        "bytes": len(map_bytes),
        "url_used": map_url,
    }
    (out / "manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Fetched quran-text ayah-map {actual_generated} ({len(map_bytes)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
