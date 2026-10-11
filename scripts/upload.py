#!/usr/bin/env python3
"""Uploads the packages a build made (dist/new.txt) to the R2 bucket behind packages.rustgis.app.

    CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... python scripts/upload.py

Published packages never change: a package already in the bucket with the same bytes is skipped,
and one with different bytes stops the upload (bump the plugin's version instead). Each upload
is downloaded again and checked against its SHA-256. Run this before deploying the registry, so
registry.json never lists a package the bucket can't serve.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUCKET = "rustgis-plugins"
PACKAGES_URL = "https://packages.rustgis.app"
# An exact version: it runs with a token that can write to the bucket.
WRANGLER = "wrangler@4.145.0"
KEY_RE = re.compile(r"[a-z][a-z0-9-]{0,63}/[a-z][a-z0-9-]{0,63}-\d+(\.\d+){0,2}([-+][0-9A-Za-z]+(\.[0-9A-Za-z]+)*)*\.rgplugin")


def fetch(url: str) -> bytes | None:
    """Downloads a package from the bucket's domain, bypassing caches.

    Args:
        url: The package's URL.

    Returns:
        Its bytes, or None when the bucket doesn't have it.
    """
    # A fresh query string each time, so a cached answer (a 404 before the upload) isn't reused.
    req = urllib.request.Request(f"{url}?check={time.time_ns()}", headers={"User-Agent": "rustgis-plugins-upload", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main() -> int:
    """Uploads every new package; returns the exit code."""
    names = [n.strip() for n in (DIST / "new.txt").read_text().splitlines() if n.strip()]
    failures = 0
    for name in names:
        # <id>/<id>-<version>.rgplugin and nothing else: never a key outside the plugin's prefix.
        pid, _, file_name = name.partition("/")
        if not KEY_RE.fullmatch(name) or not file_name.startswith(f"{pid}-"):
            print(f"error: {name} isn't a package key (<id>/<id>-<version>.rgplugin)", file=sys.stderr)
            failures += 1
            continue
        path = DIST / "packages" / name
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        url = f"{PACKAGES_URL}/{name}"
        have = fetch(url)
        if have is not None:
            if hashlib.sha256(have).hexdigest() == digest:
                print(f"{name}: already published")
            else:
                print(f"error: {name} is already published with different contents; bump the plugin's version", file=sys.stderr)
                failures += 1
            continue
        subprocess.run(
            [
                "npx", "--yes", WRANGLER, "r2", "object", "put", f"{BUCKET}/{name}", "--file", str(path), "--remote",
                "--content-type", "application/zip", "--cache-control", "public, max-age=31536000, immutable",
            ],
            check=True,
        )
        got = fetch(url)
        if got is None or hashlib.sha256(got).hexdigest() != digest:
            print(f"error: {name} doesn't read back from {url} as uploaded", file=sys.stderr)
            failures += 1
        else:
            print(f"{name}: uploaded ({len(data):,} bytes)")
    print(f"{len(names)} packages, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
