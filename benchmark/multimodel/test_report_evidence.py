"""Offline evidence export integrity and explorer binding tests."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import report_evidence as export


def case(case_id, target=1):
    return {"id": case_id, "experiment": "policy", "state": {"number": 20},
            "question": "Is the number at least 10?", "criteria": {"true": "At least 10", "false": "Below 10"},
            "target": target, "target_kind": "label"}


def row(index, c, model="Model A"):
    return {"case_id": c["id"], "experiment": c["experiment"], "target": c["target"], "target_kind": c["target_kind"],
            "model_label": model, "phase": "quality", "sequence": index, "valid": True,
            "probability": .875, "latency_s": .12345678901234567,
            "cost_usd": None if index == 3 else .0000001,
            "request": {"model": "test/model", "state": c["state"], "questions": {"answer": {
                "type": "noul", "instructions": c["question"], "criteria": c["criteria"]}}},
            "usage": {"prompt_tokens": 9007199254740993}, "response": {"value": 1.2345678901234567},
            "provider": "Test", "started_at": "2026-10-02T00:00:00Z"}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cases = [case("main-a"), case("main-b", 0)]
        self.case_path = self.base / "cases.jsonl"
        self.cache_path = self.base / "cache-cases.jsonl"
        self.case_path.write_text("\n".join(json.dumps(c) for c in self.cases) + "\n")
        self.cache_path.write_text(json.dumps(case("cache-a")) + "\n")
        self.qdir = self.base / "quality"
        self.qdir.mkdir()
        (self.qdir / "metadata.json").write_text(json.dumps({"status": "complete"}))
        self.original = [row(i, self.cases[i % 2], "Model A" if i % 2 else "Model B") for i in range(101)]
        self.lines = [(json.dumps(r, separators=(",", ":")).replace('"cost_usd":1e-07', '"cost_usd":0.0000001000') + "\n").encode() for r in self.original]
        (self.qdir / "responses.jsonl").write_bytes(b"".join(self.lines))

    def build(self, **kwargs):
        options = {"quality_dirs": [self.qdir], "supplementary_timing_dirs": [], "supplementary_cache_dirs": [],
                   "setup_dirs": [], "original_dirs": [], "cases_path": self.case_path,
                   "cache_cases_path": self.cache_path, "expected_case_count": 3, "output_dir": self.base / "out"}
        options.update(kwargs)
        return export.build_evidence(**options)

    def test_raw_chunks_preserve_numerical_literals_and_global_indexes(self):
        before = hashlib.sha256((self.qdir / "responses.jsonl").read_bytes()).hexdigest()
        result = self.build()
        self.assertEqual(result["evidence"]["call_count"], 101)
        self.assertEqual(result["evidence"]["case_count"], 3)
        out = self.base / "out"
        self.assertEqual(len(json.loads((out / "records-000.json").read_text())), 100)
        self.assertEqual(len(json.loads((out / "records-001.json").read_text())), 1)
        data = (out / "records-000.json").read_bytes()
        self.assertIn(b'"cost_usd":0.0000001000', data)
        self.assertIn(b'9007199254740993', data)
        for index, original in enumerate(self.original):
            compact = result["evidence"]["records"][index]
            raw = json.loads((out / compact["raw"]["path"]).read_text())[compact["raw"]["index"]]
            self.assertEqual(raw["record_index"], index)
            self.assertEqual({k: v for k, v in raw.items() if k not in ("run", "record_index")}, original)
            self.assertEqual(compact["record_file"], "benchmark/multimodel/report-data/" + compact["raw"]["path"])
        self.assertEqual(hashlib.sha256((self.qdir / "responses.jsonl").read_bytes()).hexdigest(), before)
        for c in result["evidence"]["cases"]:
            self.assertTrue(all(result["evidence"]["records"][i]["case_id"] == c["id"] for i in c["records"]))

    def test_manifest_is_small_has_hashes_and_does_not_embed_responses(self):
        result = self.build()
        out = self.base / "out"
        manifest = json.loads((out / "evidence.json").read_text())
        self.assertNotIn("records", manifest)
        self.assertEqual(manifest["record_chunks"], ["records-000.json", "records-001.json"])
        for name, description in manifest["files"].items():
            self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), description["sha256"])
        self.assertLess((out / "evidence.json").stat().st_size, (out / "records-000.json").stat().st_size)
        self.assertNotIn(str(self.base), json.dumps(manifest))

    def test_missing_bill_is_null_and_blank_not_zero(self):
        result = self.build()
        self.assertIsNone(result["records"][3]["cost_usd"])
        self.assertFalse(result["evidence"]["records"][3]["billing_observed"])
        with (self.base / "out/requests.csv").open() as stream:
            records = list(csv.DictReader(stream))
        self.assertEqual(records[3]["cost_usd"], "")
        self.assertNotEqual(records[0]["cost_usd"], "")

    def test_supplementary_cases_are_bound_and_selectors_are_dynamic(self):
        supplement = self.base / "old-timing"
        supplement.mkdir()
        c = case("supplement-only")
        r = row(0, c, "Model with <markup>")
        r["phase"] = "timing_warmup"
        (supplement / "responses.jsonl").write_text(json.dumps(r) + "\n")
        result = self.build(supplementary_timing_dirs=[supplement])
        compact = result["evidence"]["records"][-1]
        self.assertEqual(result["supplementary_cases"][compact["case_ref"]]["id"], c["id"])
        self.assertEqual(compact["session_label"], "Supplementary timing")
        self.assertIn('Model with &lt;markup&gt;', result["explorer_html"])
        self.assertIn('value="timing_warmup"', result["explorer_html"])
        self.assertNotIn('value="Luna cached"', result["explorer_html"])
        raw = json.loads((self.base / "out/records-001.json").read_text())
        self.assertEqual([r["record_index"] for r in raw], [100, 101])

    def test_duplicate_source_and_incomplete_primary_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "assigned more than once"):
            self.build(original_dirs=[self.qdir])
        (self.qdir / "metadata.json").write_text('{"status":"running"}')
        with self.assertRaisesRegex(ValueError, "completed run"):
            self.build()

    def test_aborted_cache_is_discovered_and_retained_as_supplementary(self):
        paths = {}
        for status in ('aborted', 'complete', 'running'):
            directory = self.base / 'runs' / ('cache-' + status)
            directory.mkdir(parents=True)
            (directory / 'metadata.json').write_text(json.dumps({'status': status, 'experiment': 'multimodel_cache_policy'}))
            record = row(0, self.cases[0], 'Luna cached')
            record.update(phase='measured', valid=False, probability=None, cost_usd=None, error='HTTP 400: cache key too long')
            (directory / 'responses.jsonl').write_text(json.dumps(record) + '\n')
            paths[status] = directory
        with patch.object(export, 'ROOT', self.base), patch.object(export, 'BENCHMARK', self.base / 'empty-benchmark'):
            discovered = export.discover_supplementary_caches()
        self.assertEqual(set(discovered), {paths['aborted'], paths['complete']})
        result = self.build(primary_cache_dir=paths['complete'], supplementary_cache_dirs=discovered)
        sessions = result['manifest']['sessions']
        aborted = next(s for s in sessions if s['status'] == 'aborted')
        self.assertEqual(aborted['role'], 'supplementary_cache')
        self.assertEqual(aborted['record_count'], 1)
        self.assertEqual(sum(s['role'] == 'primary_cache' for s in sessions), 1)
        record = next(r for r in result['evidence']['records'] if r['session_id'] == aborted['id'])
        self.assertEqual(record['error'], 'HTTP 400: cache key too long')
        self.assertIsNone(record['cost_usd'])
        self.assertIn('cache-aborted', result['explorer_html'])

    def test_secret_export_fails_closed_without_rewriting_original(self):
        secret = "sk-or-v1-" + "a" * 48
        records = deepcopy_rows(self.original[:1])
        records[0]["error"] = secret
        path = self.qdir / "responses.jsonl"
        path.write_text(json.dumps(records[0]) + "\n")
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Credential-like string"):
            self.build()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.base / "out/records-000.json").exists())


def deepcopy_rows(rows):
    return json.loads(json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
