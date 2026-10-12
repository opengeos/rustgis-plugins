"""Audit and fill the plugins' translations (the `i18n` table in each `plugin.json`).

Placeholder mistakes always fail `audit`; missing translations fail it only with `--strict`
(a string with no translation shows in English, so a plugin may ship with gaps).

A plugin's translatable strings are the text in its manifest (name, description, command labels
and help, tool names, summaries, toolboxes, parameter labels and choices, tab, group and menu
labels) and the messages its code words with `req.tr(MANIFEST, "...")`. RustGIS shows each in
the interface language when the plugin's `i18n` has it, and in English otherwise.

    python scripts/i18n.py audit [-v] [--strict]   # coverage per plugin and language (CI)
    python scripts/i18n.py missing -o missing.json  # {plugin: {lang: {english: ""}}}
    python scripts/i18n.py insert translations.json # same shape, values filled in

`insert` adds to the existing tables (a language already stored with a region, such as
`zh-CN`, keeps its key), never overwrites a translation, and rewrites only the `i18n` block of
`plugin.json`, so the rest of the file keeps its layout.

Code messages are found only as a string literal passed straight to `tr`: a message built with
`concat!`, held in a `const`, or passed through a variable isn't seen, so `audit` can't vouch
for it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"

# RustGIS's interface languages besides English (crates/ui-egui/src/i18n in opengeos/RustGIS).
LANGS = ["de", "es", "fr", "it", "nl", "pt", "ru", "tr", "az", "id", "vi", "zh", "ja", "ko"]

# `req.tr(MANIFEST, "...")` in a plugin's source: the message is the first string literal.
TR_RE = re.compile(r'\.tr\(\s*[A-Za-z_][A-Za-z0-9_:]*\s*,\s*"((?:[^"\\]|\\.)*)"')
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def placeholders(s: str) -> tuple[list[str], int, int]:
    """A string's `{name}` placeholders, and the braces left outside them.

    The SDK fills `{name}` wherever it appears, so `{{name}}` would render as `{value}`: counting
    the stray braces makes a translation that wraps a placeholder in extra braces differ from
    the English.

    Args:
        s: An English string or a translation.

    Returns:
        The placeholders, sorted, and the counts of the other `{` and `}`.
    """
    rest = PLACEHOLDER_RE.sub("", s)
    return sorted(PLACEHOLDER_RE.findall(s)), rest.count("{"), rest.count("}")


def manifests() -> list[Path]:
    """The plugins' manifests.

    Returns:
        Each `plugins/*/plugin.json`, sorted.
    """
    return sorted(PLUGINS.glob("*/plugin.json"))


def rust_string(literal: str) -> str:
    """Decodes the escapes RustGIS plugin messages use in a Rust string literal.

    Args:
        literal: The text between the quotes.

    Returns:
        The string.
    """
    return re.sub(r'\\(["\\nt])', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), literal)


def strings(path: Path) -> list[str]:
    """Every translatable string of a plugin.

    Args:
        path: The plugin's `plugin.json`.

    Returns:
        The strings, in order of appearance, without duplicates or blanks.
    """
    m = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = [m.get("name", ""), m.get("description", "")]
    for c in m.get("commands", []):
        out += [c.get("label", ""), c.get("help", "")]
    for t in m.get("tools", []):
        out += [t.get("name", ""), t.get("summary", ""), t.get("toolbox", "")]
        for p in t.get("params", []):
            out.append(p.get("label", ""))
            out += [str(c) for c in p.get("choices", [])]
    tab = m.get("tab") or {}
    out.append(tab.get("label", ""))
    for g in tab.get("groups", []):
        out.append(g.get("label", ""))
        for item in g.get("items", []):
            if isinstance(item, dict):
                out += [item.get("menu", ""), item.get("help", "")]
    for src in sorted((path.parent / "src").rglob("*.rs")):
        out += [rust_string(s) for s in TR_RE.findall(src.read_text(encoding="utf-8"))]
    seen: set[str] = set()
    return [s for s in out if s.strip() and not (s in seen or seen.add(s))]


def table_for(i18n: dict, lang: str) -> dict | None:
    """The stored table that serves `lang`: its own code, or the language with a region.

    Args:
        i18n: The plugin's `i18n`.
        lang: A language code such as `zh`.

    Returns:
        The table, or None when the plugin has none for the language.
    """
    if lang in i18n:
        return i18n[lang]
    for key, table in i18n.items():
        if re.split("[-_]", key)[0].lower() == lang:
            return table
    return None


def audit(verbose: bool) -> tuple[int, int]:
    """Prints each plugin's coverage per language, and the translations that lose placeholders.

    Coverage counts the table RustGIS picks for each interface language; placeholders are
    checked in every stored table, so a regional one (`zh-CN` beside `zh`) can't hide a mistake.

    Args:
        verbose: Also list the missing strings and placeholder problems.

    Returns:
        How many strings are missing, and how many translations have the wrong placeholders.
    """
    missing_n, bad_n = 0, 0
    for path in manifests():
        m = json.loads(path.read_text(encoding="utf-8"))
        keys = strings(path)
        i18n = m.get("i18n", {})
        print(f"{m.get('id')}: {len(keys)} strings")
        for lang in LANGS:
            table = table_for(i18n, lang) or {}
            missing = [k for k in keys if not str(table.get(k, "")).strip()]
            done = len(keys) - len(missing)
            print(f"  {lang:5} {100 * done // max(len(keys), 1):3}%  missing {len(missing):3}")
            if verbose:
                for k in missing:
                    print(f"        missing: {k!r}")
            missing_n += len(missing)
        for lang, table in sorted(i18n.items()):
            bad = [k for k in keys if k in table and placeholders(k) != placeholders(str(table[k]))]
            if bad:
                print(f"  {lang:5} placeholder {len(bad)}")
                if verbose:
                    for k in bad:
                        print(f"        placeholder: {k!r} -> {table[k]!r}")
            bad_n += len(bad)
    return missing_n, bad_n


def missing(out: Path) -> None:
    """Writes the untranslated strings, to fill in and pass to `insert`.

    Args:
        out: Where to write `{plugin id: {lang: {english: ""}}}`.
    """
    result: dict = {}
    for path in manifests():
        m = json.loads(path.read_text(encoding="utf-8"))
        i18n = m.get("i18n", {})
        for lang in LANGS:
            table = table_for(i18n, lang) or {}
            todo = {k: "" for k in strings(path) if not str(table.get(k, "")).strip()}
            if todo:
                result.setdefault(m["id"], {})[lang] = todo
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {sum(len(t) for p in result.values() for t in p.values())} missing strings to {out}")


def write_i18n(path: Path, i18n: dict) -> None:
    """Replaces the `i18n` block of a manifest, keeping the rest of the file as it is.

    Args:
        path: The plugin's `plugin.json`.
        i18n: The new table.
    """
    text = path.read_text(encoding="utf-8")
    block = json.dumps(i18n, ensure_ascii=False, indent=2).replace("\n", "\n  ")
    m = json.loads(text)
    want = {**m, "i18n": i18n}
    start = text.rfind('"i18n"')
    out = text[:start] + f'"i18n": {block}\n}}\n' if list(m)[-1:] == ["i18n"] and start != -1 else ""
    try:
        kept = json.loads(out) == want
    except json.JSONDecodeError:
        kept = False
    # The search for the block can land on the wrong `"i18n"`; then rewrite the whole file.
    path.write_text(out if kept else json.dumps(want, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def insert(src: Path) -> None:
    """Adds translations to the manifests.

    Args:
        src: `{plugin id: {lang: {english: translation}}}`.
    """
    data = json.loads(src.read_text(encoding="utf-8"))
    for path in manifests():
        m = json.loads(path.read_text(encoding="utf-8"))
        new = data.get(m.get("id"), {})
        if not new:
            continue
        keys = set(strings(path))
        i18n = m.get("i18n", {})
        added = 0
        for lang, entries in new.items():
            key = next((k for k in i18n if k == lang or re.split("[-_]", k)[0].lower() == lang), lang)
            table = i18n.setdefault(key, {})
            for en, tr in entries.items():
                if en not in keys or not str(tr).strip() or str(table.get(en, "")).strip():
                    continue
                if placeholders(en) != placeholders(str(tr)):
                    print(f"{m['id']} {lang}: skipped (placeholders differ): {en!r} -> {tr!r}")
                    continue
                table[en] = tr
                added += 1
        write_i18n(path, {k: i18n[k] for k in sorted(i18n)})
        print(f"{m['id']}: +{added}")


def main() -> int:
    """Runs the command given on the command line.

    Returns:
        The exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="coverage per plugin and language")
    a.add_argument("-v", "--verbose", action="store_true", help="list the missing strings")
    a.add_argument("--strict", action="store_true", help="also fail when a string is missing")
    sub.add_parser("missing", help="write the untranslated strings").add_argument("-o", "--out", type=Path, required=True)
    sub.add_parser("insert", help="add translations").add_argument("file", type=Path)
    args = ap.parse_args()
    if args.cmd == "audit":
        missing_n, bad_n = audit(args.verbose)
        if bad_n:
            print(f"error: {bad_n} translations don't keep the English string's {{placeholders}}", file=sys.stderr)
        return 1 if bad_n or (missing_n and args.strict) else 0
    if args.cmd == "missing":
        missing(args.out)
    else:
        insert(args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
