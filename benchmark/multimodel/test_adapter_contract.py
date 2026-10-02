"""Offline adapter contract tests against frozen payloads and fake responses."""
from copy import deepcopy
import json
import unittest

import adapter
import cache_protocol


def model(label="Luna"):
    return {"label": label, "id": "openai/gpt-6-luna" if label == "Luna" else "typesafe/jev-1.13",
            "provider": "OpenAI" if label == "Luna" else "TypeSafe",
            "provider_name": "OpenAI" if label == "Luna" else "TypeSafe", "reasoning": {"effort": "none"} if label == "Luna" else None,
            "cache_control": "explicit" if label == "Luna" else "unsupported",
            "prices": {"prompt": "0.0000001", "completion": "0.0000004"}}


CASE = {"id": "test", "experiment": "policy", "state": {"number": 20},
        "question": "Is the number at least 10?", "criteria": {"true": "At least 10", "false": "Below 10"},
        "target": 1, "target_kind": "label"}


class FakeResponse:
    def __init__(self, data, status=200):
        self.status, self.data = status, data

    def read(self):
        return json.dumps(self.data).encode()


class FakeConnection:
    def __init__(self, response):
        self.response, self.calls, self.closed = response, [], False

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def good_response(spec):
    return {"model": spec["id"], "provider": spec["provider_name"],
            "usage": {"cost": .00001, "completion_tokens_details": {"reasoning_tokens": 0}},
            "choices": [{"finish_reason": "stop", "message": {"content": '{"type":"noul","noul":0.75}'}}]}


class AdapterContractTests(unittest.TestCase):
    def call(self, data, status=200, spec=None):
        spec = spec or model()
        client = adapter.Adapter("sk-or-offline-test-placeholder")
        connection = FakeConnection(FakeResponse(data, status))
        client.connection = connection
        request = client.build_request(CASE, spec)
        row = client.run(CASE, spec, spec["label"], "quality", 0, request["endpoint"], request["payload"])
        self.assertEqual(len(connection.calls), 1)
        return row

    def test_existing_luna_and_jev_quality_payloads_are_unchanged(self):
        client = adapter.Adapter("sk-or-offline-test-placeholder")
        for label in ("Luna", "Jev"):
            expected_endpoint, expected_payload = adapter.runner.payload_for(CASE, label)
            actual = client.build_request(CASE, model(label))
            self.assertEqual(actual, {"endpoint": expected_endpoint, "payload": expected_payload})

    def test_cache_payload_control_and_segmentation_match_original(self):
        import cache_experiment
        cases, _, _, _ = cache_protocol.load_source()
        case = cache_protocol.with_prefix(cases[0], "offline-fixed-nonce")
        client = adapter.Adapter("sk-or-offline-test-placeholder")
        for mode in ("cached", "uncached"):
            endpoint, payload = cache_experiment.cache_payload(case, "Luna " + mode, "fixed-key")
            self.assertEqual(client.build_request(case, model(), mode, "fixed-key"), {"endpoint": endpoint, "payload": payload})

    def test_valid_exact_probability_retains_actual_billing(self):
        row = self.call(good_response(model()))
        self.assertTrue(row["valid"])
        self.assertEqual(row["probability"], .75)
        self.assertEqual(row["cost_usd"], .00001)
        self.assertTrue(row["reasoning_verified_disabled"])

    def test_schema_and_disabled_reasoning_failures_retain_charge(self):
        mutations = [
            lambda d: d["choices"][0]["message"].update(content='{"type":"noul","noul":true}'),
            lambda d: d["choices"][0]["message"].update(content='{"type":"noul","noul":0.7,"extra":1}'),
            lambda d: d["choices"][0]["message"].update(content='{"type":"noul","noul":1.1}'),
            lambda d: d["choices"][0].update(finish_reason="length"),
            lambda d: d["usage"]["completion_tokens_details"].update(reasoning_tokens=1),
            lambda d: d["choices"][0]["message"].update(reasoning="hidden work"),
        ]
        for mutation in mutations:
            data = good_response(model())
            mutation(data)
            with self.subTest(data=data):
                row = self.call(data)
                self.assertFalse(row["valid"])
                self.assertIsNone(row["probability"])
                self.assertEqual(row["cost_usd"], .00001)

    def test_wrong_model_or_provider_is_invalid(self):
        for field in ("model", "provider"):
            data = good_response(model())
            data[field] = "unexpected"
            with self.subTest(field=field):
                row = self.call(data)
                self.assertFalse(row["valid"])
                self.assertIsNone(row["probability"])

    def test_http_failure_preserves_unknown_cost_without_retry(self):
        row = self.call({"error": {"message": "upstream unavailable"}, "usage": {"cost": None}}, status=520)
        self.assertFalse(row["valid"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["http_status"], 520)


if __name__ == "__main__":
    unittest.main()
