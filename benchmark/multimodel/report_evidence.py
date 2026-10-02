#!/usr/bin/env python3
"""Build small, public-relative evidence files for the comparison report.

No HTTP, credentials, or source mutations. Full records retain their original
JSON numeric literals in arrays of at most 100 records. The browser receives a
compact index and frozen case definitions; each index row links to its raw row.
"""
import csv
import hashlib
import html
import json
from pathlib import Path
import re
from collections import Counter

ROOT = Path(__file__).resolve().parent
BENCHMARK = ROOT.parent
DEFAULT_CASES = BENCHMARK / "expanded/data/cases.jsonl"
DEFAULT_CACHE_CASES = BENCHMARK / "expanded/runs/cache-20261002T095722Z/cases.jsonl"
ROLES = {
    "quality": "Quality evaluation",
    "primary_timing": "Primary timing",
    "primary_cache": "Primary caching",
    "supplementary_timing": "Supplementary timing",
    "supplementary_cache": "Supplementary caching",
    "setup": "Setup",
    "original": "Supplementary protocol checks",
}
SECRET_PATTERN = re.compile(r"\bsk-or-(?:v1-)?[A-Za-z0-9_-]{16,}|\bBearer\s+sk-[A-Za-z0-9_-]{12,}")
SECRET_FIELDS = {"authorization", "api_key", "apikey", "access_token", "refresh_token"}
COMPACT_FIELDS = (
    "case_id", "experiment", "target", "target_kind", "model_label", "base_model_label",
    "requested_model", "response_model", "provider", "phase", "sequence", "started_at",
    "probability", "valid", "error", "cost_usd", "usage", "latency_s", "http_status",
    "generation_id", "finish_reason", "answer", "repeat", "block", "pair_id", "cache_mode",
    "cached_tokens", "cache_write_tokens", "reported_reasoning_tokens", "visible_reasoning",
    "reasoning_verified_disabled", "prefix_sha256", "request_sha256",
)
CSV_FIELDS = (
    "record_index", "run", "record_id", "session_id", "session_role", "session_label", "case_ref", "case_id",
    "experiment", "model_label", "base_model_label", "phase", "sequence", "repeat", "block", "pair_id",
    "target", "target_kind", "probability", "valid", "correct_at_0_5", "absolute_error",
    "squared_error", "latency_s", "cost_usd", "billing_observed", "provider", "requested_model",
    "response_model", "http_status", "finish_reason", "reported_reasoning_tokens",
    "cached_tokens", "cache_write_tokens", "raw_path", "raw_index", "error",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


def check_secrets(value, context):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if SECRET_PATTERN.search(text):
        raise ValueError(f"Credential-like string detected in {context}; evidence export stopped")
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS and item not in (None, "", "[REDACTED]"):
                raise ValueError(f"Credential field detected in {context}; evidence export stopped")
            if isinstance(item, (dict, list)):
                check_secrets(item, context)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                check_secrets(item, context)


def public_source(path):
    path = Path(path).resolve()
    try:
        return "benchmark/" + str(path.relative_to(BENCHMARK.resolve()))
    except ValueError:
        return path.name


def write_artifact(out, relative, data, files):
    if len(data) >= 95_000_000:
        raise ValueError(f"Artifact {relative} exceeds the 95 MB publication size guard")
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    files[relative] = {"bytes": len(data), "sha256": sha(data)}


def read_cases(paths):
    cases, sources = {}, []
    for source in paths:
        path = Path(source)
        data = path.read_bytes()
        rows = [json.loads(s) for s in data.splitlines() if s.strip()]
        check_secrets(rows, path.name)
        sources.append({"path": public_source(path), "sha256": sha(data), "cases": len(rows)})
        for row in rows:
            case_id = row["id"]
            if case_id in cases:
                raise ValueError(f"Duplicate main case definition: {case_id}")
            cases[case_id] = row
    return cases, sources


def request_input(row):
    """Recover the actually sent shared input for supplementary case binding."""
    payload = row.get("request") or {}
    if "state" in payload and "questions" in payload:
        return {"state": payload["state"], "questions": payload["questions"]}
    users = [m for m in payload.get("messages", []) if m.get("role") == "user"]
    if len(users) != 1:
        return None
    content = users[0].get("content")
    if isinstance(content, list):
        if any(not isinstance(b, dict) or b.get("type") != "text" or not isinstance(b.get("text"), str) for b in content):
            return None
        content = "".join(b["text"] for b in content)
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and "state" in data and "questions" in data else None


def bind_case(row, session_id, cases, supplementary):
    case_id = row.get("case_id")
    if case_id in cases:
        expected = cases[case_id]
        for key in ("target", "target_kind"):
            if row.get(key) != expected.get(key):
                raise ValueError(f"Target differs from frozen case: {case_id}")
        return "main:" + case_id
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("Every evidence record must identify its case")
    shared = request_input(row)
    if shared is None:
        raise ValueError(f"No frozen definition or recoverable input for {session_id}/{case_id}")
    question = shared["questions"].get("answer")
    if not isinstance(question, dict):
        raise ValueError(f"Missing answer question for {session_id}/{case_id}")
    candidate = {"id": case_id, "experiment": row.get("experiment"), "target": row.get("target"),
                 "target_kind": row.get("target_kind"), "state": shared["state"],
                 "question": question.get("instructions"), "criteria": question.get("criteria"),
                 "definition_source": "Sent request in the cited measurement session"}
    # A setup may reuse a case label with changed state. Distinguish those exact
    # definitions, rather than silently binding both to the first one seen.
    content_id = sha(json_bytes(candidate))[:16]
    case_ref = f"supplementary:{session_id}:{case_id}:{content_id}"
    supplementary.setdefault(case_ref, candidate)
    return case_ref


def compact_row(row, session, index, case_ref, raw_path, raw_index):
    result = {k: row[k] for k in COMPACT_FIELDS if k in row}
    result.update(record_id=f"{session['id']}:{index}", session_id=session["id"],
                  session_role=session["role"], session_label=session["label"],
                  case_ref=case_ref, raw={"path": raw_path, "index": raw_index},
                  source_line=index + 1)
    p, q = row.get("probability"), row.get("target")
    usable = row.get("valid") is True and isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1
    if row.get("target_kind") == "label":
        result["correct_at_0_5"] = bool(usable and ((p >= .5) == bool(q)))
    else:
        result["correct_at_0_5"] = None
    result["absolute_error"] = abs(p - q) if usable else None
    result["squared_error"] = (p - q) ** 2 if usable else None
    result["billing_observed"] = row.get("cost_usd") is not None
    details = (row.get("usage") or {}).get("prompt_tokens_details") or {}
    for key in ("cached_tokens", "cache_write_tokens"):
        if key not in result:
            result[key] = details.get(key)
    return result


def explorer_markup(cases, records, supplementary_count):
    template = (BENCHMARK / "expanded/explorer.html").read_text()
    template = template.replace("benchmark/expanded/report-data/", "benchmark/multimodel/report-data/")
    def options(control, values, first):
        nonlocal template
        contents = '<option value="all">' + html.escape(first) + '</option>' + ''.join(
            '<option value="' + html.escape(value, quote=True) + '">' + html.escape(label) + '</option>'
            for value, label in values)
        template = re.sub(r'(<select id="' + control + r'">).*?(</select>)', lambda m: m[1] + contents + m[2], template, flags=re.S)
    options("call-run", [(x, x) for x in dict.fromkeys(r["run"] for r in records)], "All sessions")
    options("call-model", [(x, x) for x in sorted({r["model_label"] for r in records})], "All models / arms")
    options("call-phase", [(x, x.replace("_", " ")) for x in sorted({r["phase"] for r in records})], "All phases")
    task_names = {"boolq": "BoolQ", "rte": "RTE", "wic": "WiC", "policy": "Simple policies",
                  "probability": "Exact probabilities", "expanded_cache_policy": "Long policy / cache"}
    tasks = Counter(c["experiment"] for c in cases)
    options("case-task", [(key, f"{task_names.get(key, key)} ({count:,})") for key, count in tasks.items()], "All experiments")
    template = re.sub(r'<p>Inspect all .*?</p>',
        f'<p>Inspect all <strong>{len(cases):,} benchmark and cache cases</strong>, with their inputs, reference answers, provenance and linked model outputs. Supplementary measurements and setup inputs remain available in the API call explorer below. Filters affect this appendix only; the paper’s tables use their stated protocol samples.</p>', template, count=1)
    template = template.replace("Download all evidence as JSON", "Download the evidence manifest")
    template = template.replace('href="benchmark/multimodel/report-data/evidence.json" download>Download all case data', 'href="benchmark/multimodel/report-data/cases.json" download>Download all case data')
    template = template.replace("Export full request and response records (JSON)", "Evidence manifest and raw record chunks")
    return template


def discover_supplementary_caches():
    """Find closed cache sessions, including failed administrative attempts."""
    earlier = BENCHMARK / "expanded/runs/cache-20261002T095722Z"
    found = [earlier] if earlier.exists() else []
    for candidate in sorted((ROOT / "runs").glob("cache-*")):
        metadata_path = candidate / "metadata.json"
        if not metadata_path.exists() or not (candidate / "responses.jsonl").exists():
            continue
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("experiment") == "multimodel_cache_policy" and metadata.get("status") in ("complete", "stopped", "aborted"):
            found.append(candidate)
    return found


def build_evidence(quality_dirs, primary_timing_dir=None, primary_cache_dir=None, *,
                   supplementary_timing_dirs=None, supplementary_cache_dirs=None, setup_dirs=None, original_dirs=None,
                   cases_path=DEFAULT_CASES, cache_cases_path=DEFAULT_CACHE_CASES,
                   output_dir=ROOT / "report-data", expected_case_count=4881, chunk_size=100):
    """Export each supplied response source once; return report integration data.

    Source arguments accept run directories or a responses JSONL path. A source
    cannot be assigned twice. Earlier measurements retain distinct session roles
    and are never pooled into primary timing/caching by this helper.
    """
    if chunk_size != 100:
        raise ValueError("Public raw evidence chunks must contain at most 100 records (fixed chunk_size=100)")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cases, case_sources = read_cases([cases_path, cache_cases_path])
    if expected_case_count is not None and len(cases) != expected_case_count:
        raise ValueError(f"Expected {expected_case_count} main cases, found {len(cases)}")
    def existing(paths):
        return [p for p in paths if p.exists()]
    if supplementary_timing_dirs is None:
        supplementary_timing_dirs = existing([BENCHMARK / "expanded/runs/timing-20261002T095011Z"])
    if supplementary_cache_dirs is None:
        supplementary_cache_dirs = discover_supplementary_caches()
    if setup_dirs is None:
        setup_dirs = existing([BENCHMARK / "runs/setup-probes.jsonl"]) + sorted((ROOT / "runs").glob("probes-*/responses.jsonl"))
    if original_dirs is None:
        original_dirs = existing([BENCHMARK / "runs/20261002T084223Z", BENCHMARK / "runs/cache-20261002T084958Z"])
    primary_sources = {Path(p).resolve() for p in (primary_timing_dir, primary_cache_dir) if p}
    supplementary_timing_dirs = [p for p in supplementary_timing_dirs if Path(p).resolve() not in primary_sources]
    supplementary_cache_dirs = [p for p in supplementary_cache_dirs if Path(p).resolve() not in primary_sources]
    groups = [("quality", quality_dirs), ("primary_timing", [primary_timing_dir] if primary_timing_dir else []),
              ("primary_cache", [primary_cache_dir] if primary_cache_dir else []),
              ("supplementary_timing", supplementary_timing_dirs), ("supplementary_cache", supplementary_cache_dirs),
              ("setup", setup_dirs), ("original", original_dirs)]
    sources, seen = [], set()
    for role, entries in groups:
        if isinstance(entries, (str, Path)):
            entries = [entries]
        for entry in entries:
            path = Path(entry)
            path = (path / "responses.jsonl" if path.is_dir() else path).resolve()
            if path in seen:
                raise ValueError("The same response source was assigned more than once: " + path.name)
            seen.add(path)
            if not path.is_file():
                raise ValueError("Response source does not exist: " + path.name)
            label = path.parent.name if path.name == "responses.jsonl" else path.stem
            session_id = re.sub(r"[^A-Za-z0-9_-]", "-", role + "-" + label)
            if any(s["id"] == session_id for s in sources):
                raise ValueError("Session identifiers collide; give input run folders unique names")
            sources.append({"id": session_id, "role": role, "label": ROLES[role], "path": path})
    records, all_records, supplementary, sessions, files, chunk = [], [], {}, [], {}, []
    for source in sources:
        path = source["path"]
        metadata_path = path.parent / "metadata.json"
        source_metadata = json.loads(metadata_path.read_text()) if path.name == "responses.jsonl" and metadata_path.exists() else {}
        if source["role"] in ("quality", "primary_timing", "primary_cache") and source_metadata.get("status") != "complete":
            raise ValueError("Primary evidence must come from a completed run: " + source["id"])
        source_hash = hashlib.sha256()
        session = {k: source[k] for k in ("id", "role", "label")}
        session.update(source_path=public_source(path), metadata_sha256=sha(metadata_path.read_bytes()) if source_metadata else None,
                       status=source_metadata.get("status"), raw_chunks=[], record_count=0,
                       models={}, phases={}, record_start=len(records))
        model_counts, phase_counts = Counter(), Counter()
        run_name = session["label"] + " · " + (path.parent.name if path.name == "responses.jsonl" else path.stem)
        with path.open("rb") as raw:
            for source_line, line in enumerate(raw, 1):
                source_hash.update(line)
                if not line.strip():
                    continue
                row = json.loads(line)
                check_secrets(row, source["id"] + ":" + str(source_line))
                # Check portability without reserializing the raw record itself.
                json.dumps(row, allow_nan=False)
                if "record_index" in row or "run" in row:
                    raise ValueError("Raw source uses reserved evidence wrapper fields")
                record_index = len(records)
                if not chunk:
                    chunk_path = f"records-{record_index // chunk_size:03d}.json"
                case_ref = bind_case(row, source["id"], cases, supplementary)
                compact = compact_row(row, session, session["record_count"], case_ref, chunk_path, len(chunk))
                compact["source_line"] = source_line
                compact.update(record_index=record_index, run=run_name,
                               record_file="benchmark/multimodel/report-data/" + chunk_path)
                records.append(compact)
                all_records.append({**row, "run": run_name, "record_index": record_index})
                original = line.strip()
                if not original.endswith(b"}"):
                    raise ValueError("Raw response must be a JSON object")
                chunk.append(original[:-1] + b',"run":' + json.dumps(run_name, ensure_ascii=False).encode() +
                             b',"record_index":' + str(record_index).encode() + b'}')
                if chunk_path not in session["raw_chunks"]:
                    session["raw_chunks"].append(chunk_path)
                session["record_count"] += 1
                model_counts[str(row.get("model_label"))] += 1
                phase_counts[str(row.get("phase"))] += 1
                if len(chunk) == chunk_size:
                    write_artifact(out, chunk_path, b"[\n" + b",\n".join(chunk) + b"\n]\n", files)
                    chunk = []
        session.update(source_sha256=source_hash.hexdigest(), models=dict(model_counts), phases=dict(phase_counts), record_end=len(records))
        sessions.append(session)
    if chunk:
        write_artifact(out, chunk_path, b"[\n" + b",\n".join(chunk) + b"\n]\n", files)
    audit = []
    audit_path = BENCHMARK / "data/label-audit.md"
    if audit_path.exists():
        for line in audit_path.read_text().splitlines():
            if line.startswith("| boolq-dev-"):
                values = [x.strip() for x in line.strip("|").split("|")]
                audit.append(dict(zip(("id", "label", "category", "evidence"), values)))
    audit_lookup = {a["id"]: a for a in audit}
    bycase = {case_id: [] for case_id in cases}
    for row in records:
        if row["case_ref"].startswith("main:"):
            bycase[row["case_id"]].append(row["record_index"])
    case_list = [{**case, "records": bycase[case_id], "audit": audit_lookup.get(case_id)} for case_id, case in cases.items()]
    write_artifact(out, "records.json", json_bytes(records), files)
    write_artifact(out, "cases.json", json_bytes(case_list), files)
    write_artifact(out, "supplementary-cases.json", json_bytes(supplementary), files)
    csv_path = out / "requests.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow({**row, "raw_path": row["raw"]["path"], "raw_index": row["raw"]["index"]})
    csv_bytes = csv_path.read_bytes()
    if len(csv_bytes) >= 95_000_000:
        raise ValueError("Compact CSV exceeds publication size guard")
    files["requests.csv"] = {"bytes": len(csv_bytes), "sha256": sha(csv_bytes)}
    manifest = {"schema_version": 1, "generator_sha256": sha(Path(__file__).read_bytes()),
                "record_count": len(records), "main_case_count": len(cases), "supplementary_case_count": len(supplementary),
                "chunk_size": chunk_size, "case_sources": case_sources, "sessions": sessions,
                "roles": ROLES, "files": files, "case_file": "cases.json", "compact_record_file": "records.json",
                "record_chunks": [name for name in files if re.fullmatch(r"records-\d+\.json", name)],
                "definitions": {"raw": "Complete original response records in arrays of at most100, with run and record_index wrappers. Original JSON numeric literals are retained verbatim.",
                    "compact": "Explorer index. Every record links to a raw chunk and zero-based array index; full request/response envelopes remain in that chunk.",
                    "case_ref": "main:<case_id> resolves in cases.json; supplementary:<session>:<case_id>:<definition hash> resolves in supplementary-cases.json.",
                    "session_roles": "Quality sessions supply the declared quality sample. Only explicitly selected timing/caching sessions are primary; earlier sessions and setup remain separately labeled.",
                    "missing": "Missing bills or probabilities remain null in JSON and blank in CSV. No unknown charge is replaced by zero.",
                    "score": "correct_at_0_5 uses probability>=0.5 for binary targets and counts invalid attempts as incorrect. Error scores require a valid probability."}}
    (out / "evidence.json").write_bytes(json_bytes(manifest))
    evidence = {"cases": case_list, "records": records, "audit": audit, "case_count": len(cases), "call_count": len(records)}
    return {"manifest": manifest, "evidence": evidence, "records": all_records, "cases": cases,
            "supplementary_cases": supplementary, "explorer_html": explorer_markup(case_list, records, len(supplementary)),
            "paths": {name: str(out / name) for name in ("evidence.json", "records.json", "cases.json", "supplementary-cases.json", "requests.csv")}}
