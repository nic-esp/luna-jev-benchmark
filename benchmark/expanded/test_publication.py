"""Offline tests for publication redaction and provenance preservation."""
import html
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import publication as p


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.case = {"id": "rte-validation-0007", "experiment": "rte", "target": 1.0,
                     "target_kind": "label", "source": {"source_idx": 7},
                     "state": {"premise": 'A fictional "cedar" <grows> by the canal.',
                               "hypothesis": "A fictional cedar is growing."}}
        self.redactor = p.Redactor([self.case])

    def test_case_and_both_native_request_formats(self):
        case = self.redactor.redact(self.case)
        self.assertEqual(case["target"], 1.0)
        self.assertEqual(case["source"], self.case["source"])
        self.assertEqual(case["state"]["source_idx"], 7)
        for request in [
            {"model": "jev", "state": self.case["state"], "questions": {"q": "Entailed?"}},
            {"model": "luna", "messages": [{"role": "system", "content": "Answer as JSON."},
                {"role": "user", "content": json.dumps({"state": self.case["state"], "questions": {"q": "Entailed?"}})}]},
        ]:
            original = {"case_id": self.case["id"], "request": request,
                        "probability": .65, "latency_s": .24, "cost_usd": .00004,
                        "response": {"answer": {"type": "noul", "noul": .65}}}
            result = self.redactor.redact(original)
            self.assertTrue(result["public_export"]["request_source_text_omitted"])
            for key in ["probability", "latency_s", "cost_usd", "response"]:
                self.assertEqual(original[key], result[key])
            self.assertIsNone(self.redactor.pattern.search(json.dumps(result)))

    def test_nested_json_and_html_examples(self):
        text = self.case["state"]["premise"]
        variants = [text, html.escape(text), json.dumps(text), json.dumps(json.dumps(text)),
                    json.dumps(text).replace("<", "\\u003c")]
        for source in variants:
            output = self.redactor.bytes(Path("example.html"), source.encode()).decode()
            self.assertIn("RTE source text omitted", output)
            self.assertNotIn("cedar", output)

    def test_non_rte_json_is_byte_identical(self):
        raw = b'{"experiment":"boolq", "target":0.0, "state":{"passage":"Example."}}'
        self.assertEqual(raw, self.redactor.bytes(Path("example.json"), raw))

    def test_absolute_source_paths_in_dictionary_keys_become_relative(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / 'outputs'
        redactor = p.Redactor([self.case], source)
        relative = 'benchmark/multimodel/runs/quality/responses.jsonl'
        absolute = str(source / relative)
        original = {'input_sha256': {absolute: 'unchanged-original-hash'},
                    'nested': [{absolute: {'cost_usd': .0125614895, 'missing_bills': 18,
                                          'total_usd': None, 'counter': 9007199254740993}}],
                    'source_path': absolute}
        raw = json.dumps(original).encode()
        public = json.loads(redactor.bytes(Path('summary-verification.json'), raw))
        self.assertEqual(public['input_sha256'], {relative: 'unchanged-original-hash'})
        self.assertEqual(public['nested'][0][relative], original['nested'][0][absolute])
        self.assertEqual(public['source_path'], relative)
        self.assertNotIn(str(source), json.dumps(public))
        self.assertEqual(json.loads(raw), original)

    def test_normalized_dictionary_key_collision_is_rejected(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / 'outputs'
        redactor = p.Redactor([self.case], source)
        relative = 'benchmark/responses.jsonl'
        original = {'input_sha256': {str(source / relative): 'first-hash', relative: 'second-hash'}}
        raw = json.dumps(original).encode()
        with self.assertRaisesRegex(ValueError, 'dictionary-key normalization collision') as error:
            redactor.bytes(Path('summary-verification.json'), raw)
        self.assertNotIn(str(source), str(error.exception))
        self.assertEqual(json.loads(raw), original)

    def test_account_identity_removed_without_changing_failure_or_bill(self):
        record = {"http_status": 429, "valid": False, "probability": None,
                  "latency_s": .625, "cost_usd": None,
                  "response": {"error": {"code": 429}, "user_id": "[account identifier omitted]"},
                  "error": 'Provider error: {"user_id":"[account identifier omitted]","code":429}'}
        public = self.redactor.redact(record)
        self.assertNotIn('user_id', public['response'])
        self.assertNotIn('[account identifier omitted]', json.dumps(public))
        for key in ('http_status', 'valid', 'probability', 'latency_s', 'cost_usd'):
            self.assertEqual(record[key], public[key])
        self.assertEqual(public['response']['error']['code'], 429)

    def test_export_manifest_and_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp) / "original", Path(tmp) / "public"
            data = source / "benchmark/expanded/data"
            data.mkdir(parents=True)
            frozen = json.dumps(self.case) + "\n"
            (data / "rte.jsonl").write_text(frozen)
            (data / "manifest.json").write_text('{"cases_sha256":"historical-original-hash"}')
            (source / "comparison.html").write_text("<html><body>" + html.escape(self.case["state"]["premise"]) + "</body></html>")
            (source / "benchmark.zip").write_bytes(b"original excluded archive")
            (source / "work").mkdir()
            (source / "work/private.txt").write_text("excluded")
            (source / "__pycache__").mkdir()
            (source / "__pycache__/test.pyc").write_bytes(b"excluded")
            manifest = p.export(source, dest)
            self.assertEqual((data / "rte.jsonl").read_text(), frozen)
            self.assertEqual(json.loads((dest / "benchmark/expanded/data/manifest.json").read_text())["cases_sha256"], "historical-original-hash")
            self.assertFalse((dest / "work").exists())
            self.assertFalse((dest / "__pycache__").exists())
            self.assertIn("public-export-note", (dest / "comparison.html").read_text())
            self.assertTrue(manifest["rte_source_text_scan_passed"])
            for rel, record in manifest["files"].items():
                self.assertEqual(record["public_sha256"], p.sha((dest / rel).read_bytes()))
            with zipfile.ZipFile(dest / "benchmark.zip") as z:
                self.assertIn(p.MANIFEST, z.namelist())
                self.assertNotIn("benchmark.zip", z.namelist())
            with self.assertRaises(ValueError):
                p.finalize(source, source.parent, self.redactor)
            with self.assertRaises(ValueError):
                p.export(source, dest)


if __name__ == "__main__":
    unittest.main()
