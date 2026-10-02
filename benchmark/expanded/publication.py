#!/usr/bin/env python3
"""Create a public reproducibility copy without redistributing RTE source text.

No network access or model calls. Original study files are read only. Rebuilding
original-source tests requires fetch_sources.py and datasets.py in a local copy;
their frozen hashes describe the original data, not this transformed export.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

DEFAULT_SOURCE = Path(__file__).resolve().parents[2]
SOURCE_URL = "https://huggingface.co/datasets/aps/super_glue/viewer/rte/validation"
OMISSION = "RTE source text omitted from public export; retrieve the original row from the linked source."
MANIFEST = "PUBLICATION_MANIFEST.json"
ABSOLUTE_HOME = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+/")
SENSITIVE_TOKEN = re.compile(
    r"\bsk-or-v1-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
NOTE = (
    "This is a transformed public copy. RTE premise/hypothesis text and copies in "
    "requests are omitted because exact upstream redistribution terms were not "
    "verified. All numerical results and reference labels are retained. Hashes "
    "recorded by the original study identify original local artifacts, not these "
    "public bytes; PUBLICATION_MANIFEST.json records the public file hashes. "
    "Local artifact paths have been made relative to the public package."
)
NOTICE = """# Third-party benchmark data

This repository is a public research reproducibility copy. The complete original
study remains preserved locally. Numerical results, IDs, labels, probability
outputs, usage, charges, timing, prompt templates and retrieval code are retained.
Public request records for RTE contain an explicit source-text omission and must
not be described as byte-identical copies of the submitted requests.

## BoolQ

BoolQ by Christopher Clark, Kenton Lee, Ming-Wei Chang, Tom Kwiatkowski,
Michael Collins and Kristina Toutanova (NAACL 2019) is released under
[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
[Author repository](https://github.com/google-research-datasets/boolean-questions).
The development split was normalized into the study schema; question/passage
text and published labels were retained. These adapted dataset portions remain
under CC BY-SA 3.0, separately from original study code and writing.

## WiC

WiC by Mohammad Taher Pilehvar and Jose Camacho-Collados (NAACL 2019) is
licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
[Author dataset page](https://pilehvar.github.io/wic/).
The validation split was normalized and target occurrences marked with
`[TARGET]` / `[/TARGET]`; published labels were retained. The WiC source-text
portions are shared for noncommercial research under that license. Their
presence does not grant permission for commercial reuse or relicense them
under any license assigned to this repository's original code.

## RTE / SuperGLUE

The exact original RTE redistribution terms were not verified. The
[SuperGLUE dataset card](https://huggingface.co/datasets/aps/super_glue)
refers users to original dataset licenses and declares its license as `other`.
[NIST's original data pages](https://tac.nist.gov/2009/RTE/past_data/index.html)
and [TAC data-agreement information](https://tac.nist.gov/2009/)
provide additional provenance; a download link is not treated as a blanket
redistribution grant. This is a conservative publication choice, not a finding
that research redistribution is prohibited.

RTE premises and hypotheses, including copies in API requests and report data,
are omitted from this public copy. Row IDs, labels and every numerical result
are preserved. `benchmark/expanded/fetch_sources.py` records exact retrieval
URLs and expected source hashes, so readers may retrieve the source from its
distributor under the applicable terms. References include Dagan et al. (2006),
Bar Haim et al. (2006), Giampiccolo et al. (2007), Bentivogli et al. (2009), and
Wang et al. (2019, SuperGLUE).

## Original synthetic cases and artifact hashes

Synthetic policy/probability cases were generated for this study. They do not
contain private user records. Benchmark source passages can discuss public
biographical or medical facts and should not be described as all fictional.

Original run metadata, checks and freeze manifests remain historical provenance.
Their original hash values are not rewritten to claim the public copy was the
data sent to the models. `PUBLICATION_MANIFEST.json` identifies transformed
files and supplies SHA-256 hashes of the public bytes. Reproducing original-data
tests requires locally retrieving and rebuilding the original datasets first.
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_rte(source: Path) -> list[dict]:
    path = source / "benchmark/expanded/data/rte.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows or any(set(row.get("state", {})) != {"premise", "hypothesis"} for row in rows):
        raise ValueError("Export must use the complete, original local RTE dataset as its source")
    return rows


def literal_pattern(values):
    """Factor common prefixes so scans of large response logs stay practical."""
    trie = {}
    for value in values:
        node = trie
        for char in value:
            node = node.setdefault(char, {})
        node[""] = None

    def emit(node):
        branches = []
        for char, child in node.items():
            if not char:
                continue
            run = char
            while "" not in child and len(child) == 1:
                char, child = next(iter(child.items()))
                run += char
            branches.append(re.escape(run) + emit(child))
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")" if branches else ""
        return "(?:" + body + ")?" if "" in node and branches else body

    return re.compile(emit(trie))


class Redactor:
    def __init__(self, cases: list[dict], source: Path | None = None):
        self.cases = {c["id"]: c for c in cases}
        self.source_prefixes = ({str(source.absolute()) + "/", str(source.resolve()) + "/"}
                                if source is not None else set())
        self.text_to_id = {s: c["id"] for c in cases for s in c["state"].values()}
        variants = set()
        for text in self.text_to_id:
            # Covers ordinary text, JSON strings nested inside JSON, HTML examples,
            # and the report's script-safe Unicode escapes.
            forms = {text, html.escape(text), html.escape(text, quote=False)}
            for _ in range(3):
                forms |= {json.dumps(v, ensure_ascii=a)[1:-1] for v in list(forms) for a in (True, False)}
            forms |= {html.escape(v) for v in list(forms)}
            forms |= {v.replace("<", "\\u003c") for v in list(forms)}
            forms |= {v.replace(">", "\\u003e") for v in list(forms)}
            variants.update(forms)
        self.pattern = literal_pattern(sorted(variants, key=len, reverse=True))

    def public_text(self, text):
        for prefix in sorted(self.source_prefixes, key=len, reverse=True):
            text = text.replace(prefix, "")
        # API errors can echo the account identifier. It is not study evidence.
        text = re.sub(r'\borg_[A-Za-z0-9]{8,}\b', '[account identifier omitted]', text)
        return text

    def verify_text(self, text, path):
        if self.pattern.search(text):
            raise ValueError(f"RTE source-text scan failed: {path}")
        if SENSITIVE_TOKEN.search(text):
            raise ValueError(f"Credential-pattern scan failed: {path}; matching content was not logged")
        if ABSOLUTE_HOME.search(text):
            raise ValueError(f"Absolute home-path scan failed: {path}; matching content was not logged")

    def placeholder(self, case_id=None):
        case = self.cases.get(case_id, {})
        return {"source_text_omitted": OMISSION, "retrieve_from": SOURCE_URL,
                "source_idx": case.get("source", {}).get("source_idx")}

    def redact(self, value, case_id=None):
        if isinstance(value, dict):
            ident = value.get("case_id", value.get("id"))
            if ident in self.cases:
                case_id = ident
            if "premise" in value and "hypothesis" in value:
                case_id = case_id or self.text_to_id.get(value["premise"]) or self.text_to_id.get(value["hypothesis"])
                return self.placeholder(case_id)
            out = {}
            for key, item in value.items():
                if key == 'user_id':
                    continue
                public_key = self.public_text(key) if isinstance(key, str) else key
                if public_key in out:
                    # Never silently overwrite evidence after path normalization.
                    # Keep the original keys out of the error message.
                    raise ValueError("Public dictionary-key normalization collision; conflicting keys were not logged")
                out[public_key] = self.redact(item, case_id)
            if case_id and "request" in value:
                out["public_export"] = {"request_source_text_omitted": True,
                    "original_request_preserved_locally": True}
            return out
        if isinstance(value, list):
            return [self.redact(v, case_id) for v in value]
        if isinstance(value, str):
            # Luna encodes the shared state as JSON in a user-message string.
            if '"state"' in value and '"premise"' in value:
                try:
                    return json.dumps(self.redact(json.loads(value), case_id), ensure_ascii=False, sort_keys=True)
                except (ValueError, TypeError):
                    pass
            return self.public_text(self.pattern.sub("[RTE source text omitted from public export]", value))
        return value

    def bytes(self, path: Path, raw: bytes) -> bytes:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.verify_text(raw.decode("latin-1"), path)
            return raw
        if path.suffix == ".jsonl":
            rows = [self.redact(json.loads(line)) for line in text.splitlines() if line.strip()]
            changed = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        elif path.suffix == ".json":
            changed = json.dumps(self.redact(json.loads(text)), ensure_ascii=False, indent=2) + "\n"
        else:
            changed = self.public_text(self.pattern.sub("[RTE source text omitted from public export]", text))
        self.verify_text(changed, path)
        # Preserve byte-identical files when semantic traversal made no changes.
        if path.suffix in {".json", ".jsonl"}:
            original = [json.loads(s) for s in text.splitlines() if s.strip()] if path.suffix == ".jsonl" else json.loads(text)
            parsed = [json.loads(s) for s in changed.splitlines() if s.strip()] if path.suffix == ".jsonl" else json.loads(changed)
            if parsed == original:
                return raw
        return changed.encode("utf-8")


def excluded(path: Path) -> bool:
    return (any(p in {"work", "__pycache__", ".git", ".DS_Store"} or p.startswith(".env") for p in path.parts)
            or path.suffix in {".pyc", ".pyo", ".zip", ".pem", ".key", ".p12"})


def banner(page: Path):
    text = page.read_text()
    for model in ("Jev", "Luna"):
        text = text.replace(f"RTE · {model} exact request", f"RTE · {model} request (source text omitted)")
    if 'id="public-export-note"' not in text:
        box = '<aside id="public-export-note" style="margin:1rem;padding:1rem;border:1px solid #777">' + html.escape(NOTE) + ' <a href="THIRD_PARTY_DATA.md">Dataset licenses</a>.</aside>'
        text = re.sub(r"(<body\b[^>]*>)", lambda m: m.group(1) + box, text, count=1, flags=re.I)
    page.write_text(text)


def finalize(source: Path, output: Path, redactor: Redactor, make_zip=True):
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Public output must be outside the original outputs directory")
    if not output.is_dir():
        raise ValueError("Public output directory does not exist")
    # Skipping private files while leaving them in the directory would not stop
    # a later `git add .` from publishing them. Reject unexpected additions.
    for path in output.rglob("*"):
        rel = path.relative_to(output)
        if ".git" in rel.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink in public output: {rel}")
        if path.is_file() and excluded(rel) and rel != Path("benchmark.zip"):
            raise ValueError(f"Excluded artifact was added to public output: {rel}; remove it before finalizing")
    # Sanitize regenerated evidence and rendered examples too. This makes finalize
    # useful after the caller rerenders the cloned report or adds a landing page.
    for path in sorted(output.rglob("*")):
        if path.is_file() and not excluded(path.relative_to(output)) and path.name != MANIFEST:
            raw = path.read_bytes()
            sanitized = redactor.bytes(path, raw)
            if sanitized != raw:
                path.write_bytes(sanitized)
    for name in ("comparison.html", "index.html"):
        if (output / name).exists():
            banner(output / name)
    (output / "THIRD_PARTY_DATA.md").write_text(NOTICE)
    manifest_path = output / "benchmark/expanded/data/manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text())
        data["public_export"] = {"transformed": True, "note": NOTE,
                                 "public_hash_manifest": MANIFEST}
        manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    files = {}
    for path in sorted(output.rglob("*")):
        rel = path.relative_to(output)
        if not path.is_file() or excluded(rel) or path.name == MANIFEST:
            continue
        raw = path.read_bytes()
        redactor.verify_text(raw.decode("utf-8", errors="replace"), rel)
        original = source / rel
        original_hash = sha(original.read_bytes()) if original.is_file() else None
        files[str(rel)] = {"public_sha256": sha(raw), "bytes": len(raw),
                          "original_local_sha256": original_hash,
                          "transformed_or_added": original_hash != sha(raw)}
    manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "note": NOTE, "rte_source_rows": len(redactor.cases),
                "rte_source_text_scan_passed": True,
                "credential_pattern_scan_passed": True,
                "absolute_home_path_scan_passed": True,
                "symlink_and_unexpected_archive_checks_passed": True,
                "original_rte_cases_sha256": sha((source / "benchmark/expanded/data/rte.jsonl").read_bytes()),
                "upstream_hashes_unchanged": True,
                "zip_note": "benchmark.zip contains this manifest and every public file; its hash is excluded to avoid a circular hash dependency.",
                "files": files}
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    if make_zip:
        with zipfile.ZipFile(output / "benchmark.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(output.rglob("*")):
                if path.is_file() and not excluded(path.relative_to(output)):
                    bundle.write(path, str(path.relative_to(output)))
        with zipfile.ZipFile(output / "benchmark.zip") as bundle:
            expected = set(files) | {MANIFEST}
            if set(bundle.namelist()) != expected:
                raise ValueError("Public ZIP membership differs from its manifest")
            for name in bundle.namelist():
                raw = bundle.read(name)
                if name != MANIFEST and sha(raw) != files[name]["public_sha256"]:
                    raise ValueError(f"Public ZIP byte hash mismatch: {name}")
                redactor.verify_text(raw.decode("utf-8", errors="replace"), "benchmark.zip:" + name)
    return manifest


def export(source: Path, output: Path, build_report=False, make_zip=True):
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Public output must be separate from the original outputs directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be empty; use --finalize only to refresh an existing public copy")
    redactor = Redactor(load_rte(source), source)
    output.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if not path.is_file() or excluded(rel):
            continue
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink in original outputs: {rel}")
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(redactor.bytes(path, path.read_bytes()))
    if build_report:
        subprocess.run([sys.executable, str(output / "benchmark/expanded/build_report.py")], check=True,
                       cwd=output, env={**__import__('os').environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return finalize(source, output, redactor, make_zip)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-report", action="store_true")
    parser.add_argument("--finalize", action="store_true", help="Resanitize, verify and repackage an existing public copy")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if args.finalize:
        result = finalize(source, output, Redactor(load_rte(source), source), not args.no_zip)
    else:
        result = export(source, output, args.build_report, not args.no_zip)
    print(json.dumps({"output": str(output), "public_files": len(result["files"]),
                      "rte_source_rows": result["rte_source_rows"], "source_text_scan_passed": True}))


if __name__ == "__main__":
    main()
