"""Offline regression tests for the administrative cache-key repair."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import adapter
import cache_protocol as original
import cache_protocol_v2 as revised
import verify_cache
from test_verify_cache import FakeAdapter, SPECS


class CacheProtocolV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.primes, _, cls.hashes = original.load_source()
        cls.identifiers = [f'{i:032x}' for i in range(4)]

    def test_original_frozen_and_every_new_key_within_limit(self):
        self.assertEqual(hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest(), revised.ORIGINAL_PROTOCOL_SHA256)
        planner = adapter.Adapter.__new__(adapter.Adapter)
        old_plan, old_prepared = original.create_plan(planner, SPECS, self.cases, self.primes, self.hashes, 17, self.identifiers)
        new_plan, new_prepared = revised.create_plan(planner, SPECS, self.cases, self.primes, self.hashes, 17, self.identifiers)
        self.assertEqual(len(old_plan['attempts']), len(new_plan['attempts']))
        for index, entry in enumerate(new_plan['attempts']):
            self.assertEqual(len(entry['cache_key']), 46)
            self.assertEqual(len(old_plan['attempts'][index]['cache_key']), 66)
            old_case, old_spec, old_request = deepcopy(old_prepared[index])
            new_case, new_spec, new_request = deepcopy(new_prepared[index])
            self.assertEqual(old_case, new_case)
            self.assertEqual(old_spec, new_spec)
            old_request['payload'].pop('prompt_cache_key', None)
            new_request['payload'].pop('prompt_cache_key', None)
            self.assertEqual(old_request, new_request)
            omit = {'cache_key', 'request_sha256', 'reserve_usd'}
            self.assertEqual({k: v for k, v in old_plan['attempts'][index].items() if k not in omit},
                             {k: v for k, v in entry.items() if k not in omit})

    def test_all_keys_preflight_before_first_request_preparation(self):
        class Spy(FakeAdapter):
            prepared = 0
            def build_request(self, *args):
                self.prepared += 1
                return super().build_request(*args)
        spy = Spy()
        identifiers = self.identifiers[:3] + ['x' * 51]
        with self.assertRaisesRegex(ValueError, '1 to 64'):
            revised.create_plan(spy, SPECS, self.cases, self.primes, self.hashes, 17, identifiers)
        self.assertEqual(spy.prepared, 0)
        self.assertEqual(len(revised.cache_key_for('Luna', 'x' * 50)), 64)

    def test_adapter_cannot_replace_the_validated_key(self):
        class Broken(FakeAdapter):
            def build_request(self, *args):
                result = super().build_request(*args)
                result['payload']['prompt_cache_key'] = 'x' * 65
                return result
        with self.assertRaisesRegex(ValueError, '1 to 64'):
            revised.create_plan(Broken(), SPECS, self.cases, self.primes, self.hashes, 17, self.identifiers)

    def test_complete_v2_fixture_independently_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / 'complete'
            result = revised.run_cache_protocol(FakeAdapter(), run, SPECS, 2)
            meta = result['metadata']
            self.assertEqual(meta['status'], 'complete')
            self.assertEqual(meta['protocol_version'], 2)
            self.assertEqual(meta['protocol_file'], 'cache_protocol_v2.py')
            self.assertEqual(meta['protocol_amendment'], revised.PROTOCOL_AMENDMENT)
            verified = verify_cache.verify_cache(run)
            self.assertTrue(verified['passed'], verified)
            self.assertEqual(verified['attempts'], 388)
            self.assertEqual(verified['hashes']['protocol_sha256'], hashlib.sha256(Path(revised.__file__).read_bytes()).hexdigest())

    def test_invalid_http_error_is_retained(self):
        class FailedPrime(FakeAdapter):
            def run(self, **kwargs):
                result = super().run(**kwargs)
                if kwargs['sequence'] == 0:
                    result.update(valid=False, probability=None, provider=None, error='HTTP 400: original validation error')
                return result
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / 'error'
            result = revised.run_cache_protocol(FailedPrime(), run, SPECS, 2)
            saved = (run / 'responses.jsonl').read_text().splitlines()
            self.assertEqual(len(saved), 1)
            self.assertEqual(result['metadata']['status'], 'stopped')
            self.assertEqual(result['metadata']['calls_dispatched'], 1)
            self.assertEqual(result['metadata']['unresolved_dispatch_n'], 0)
            self.assertIn('priming failed', result['metadata']['error'])
            first = json.loads(saved[0])
            self.assertEqual(first['error'], 'HTTP 400: original validation error')
            self.assertFalse(first['valid'])
            self.assertIsNone(first['probability'])

    def test_verifier_rejects_unlisted_protocol_and_wrong_version(self):
        for metadata in ({'protocol_file': '../cache_protocol_v2.py', 'protocol_version': 2},
                         {'protocol_file': 'cache_protocol_v2.py', 'protocol_version': 1},
                         {'protocol_file': 'cache_protocol.py', 'protocol_version': 2}):
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                verify_cache.select_protocol(metadata)


if __name__ == '__main__':
    unittest.main()
