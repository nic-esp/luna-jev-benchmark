"""Offline prospective pacing checks; fake adapters never make HTTP requests."""
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('paced_test_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('continuation_paced')
F = load('test_multimodel_continuation')


class InstrumentedAdapter(F.FakeAdapter):
    lock = threading.Lock()
    active = peak = 0
    delay = 0
    quality_valid = True
    starts = []

    def run(self, case, spec, arm_label, phase, sequence, endpoint, payload):
        if phase != 'quality':
            return super().run(case, spec, arm_label, phase, sequence, endpoint, payload)
        with self.lock:
            type(self).active += 1
            type(self).peak = max(type(self).peak, type(self).active)
            self.starts.append((sequence, time.monotonic()))
        try:
            time.sleep(self.delay)
            row = super().run(case, spec, arm_label, phase, sequence, endpoint, payload)
            if not self.quality_valid:
                row.update(valid=False, probability=None, cost_usd=None, error='Synthetic HTTP 429')
            return row
        finally:
            with self.lock:
                type(self).active -= 1


class PacedContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = F.ContinuationTests()
        self.fixture.setUp()
        self.f = self.fixture
        self.protocol = P._load_protocol()  # Test the real executor/run_once wrappers.
        self.protocol.PHASE_LOCK = self.f.p / 'phase.lock'
        self.protocol.load_frozen_sources = lambda: (self.f.cases, [], self.f.hashes)
        self.protocol.adapter_class = lambda: InstrumentedAdapter
        InstrumentedAdapter.active = InstrumentedAdapter.peak = 0
        InstrumentedAdapter.delay = 0
        InstrumentedAdapter.quality_valid = True
        InstrumentedAdapter.starts = []

    def tearDown(self):
        self.fixture.tearDown()

    def resume(self):
        with patch.object(P, '_load_protocol', return_value=self.protocol), patch.object(P, 'ROOT', self.f.p):
            return P.resume_qwen('unrelated-synthetic-credential', self.f.run, budget=1)

    def remaining(self, count):
        """Rebuild an entirely synthetic frozen fixture, not a real study run."""
        f = self.f
        f.cases = [dict(f.cases[0], id=f'case-{i}') for i in range(200 + count)]
        f.plan, f.prepared = self.protocol.create_plan(InstrumentedAdapter('fake'), f.cases, [f.spec], 'quality', 42)
        self.protocol.save(f.run / 'plan.json', f.plan)
        requests = [{'sequence': e['sequence'], 'request_sha256': e['request_sha256'], **f.prepared[e['sequence']][2]} for e in f.plan]
        (f.run / 'requests.jsonl').write_text(''.join(self.protocol.canonical(r) + '\n' for r in requests))
        records = []
        for e in f.plan[:200]:
            c, _, request = f.prepared[e['sequence']]
            valid = e['sequence'] < 195
            records.append({**e, 'experiment': c['experiment'], 'target': 1, 'target_kind': 'label',
                            'valid': valid, 'probability': 1 if valid else None,
                            'cost_usd': .001 if valid else None, 'request': request['payload'], 'endpoint': request['endpoint']})
        journal = [{'sequence': e['sequence'], 'request_sha256': e['request_sha256'], 'dispatched_at': '2026-10-02T00:00:00Z'} for e in f.plan[:200]]
        for name, records_to_write in [('responses.jsonl', records), ('attempts.jsonl', journal)]:
            (f.run / name).write_text(''.join(self.protocol.canonical(r) + '\n' for r in records_to_write))
        f.meta['planned_calls'] = len(f.plan)
        for name, field in [('plan.json', 'plan_sha256'), ('requests.jsonl', 'requests_sha256'), ('responses.jsonl', 'responses_sha256'), ('attempts.jsonl', 'attempts_sha256')]:
            f.meta[field] = P.sha(f.run / name)
        self.protocol.save(f.run / 'metadata.json', f.meta)

    def session(self):
        cls = P._armed_session_class(self.protocol, {'id': 'synthetic-amendment'})
        return cls(self.f.run, {'phase': 'quality', 'concurrency_pairs': 16},
                   P.rows(self.f.run / 'responses.jsonl'), set(range(200)), self.f.plan, 1, None)

    def test_success_retains_prefix_hashes_accounting_and_declares_schedule(self):
        f = self.f
        before = {name: (f.run / name).read_bytes() for name in ('responses.jsonl', 'attempts.jsonl', 'plan.json', 'requests.jsonl')}
        result = self.resume()
        self.assertEqual(result['metadata']['status'], 'complete')
        self.assertEqual([x[0] for x in F.FakeAdapter.calls], ['recovery_probe', 'recovery_probe', 'quality'])
        for name in ('responses.jsonl', 'attempts.jsonl'):
            self.assertTrue((f.run / name).read_bytes().startswith(before[name]))
        for name in ('plan.json', 'requests.jsonl'):
            self.assertEqual((f.run / name).read_bytes(), before[name])
        meta = result['metadata']
        self.assertEqual(meta['concurrency_pairs'], 16)
        self.assertEqual(meta['effective_worker_ceiling'], 2)
        self.assertEqual(meta['min_dispatch_spacing_s'], .25)
        self.assertEqual(meta['invalid'], 5)
        self.assertEqual(meta['unknown_billing'], 5)
        self.assertAlmostEqual(meta['unknown_billing_guard_usd'], .05)
        self.assertAlmostEqual(meta['budget_usd'], .998)
        self.assertIsNone(meta['charged_usd'])
        self.assertEqual(result['amendment']['first_unattempted_sequence'], 200)
        row = P.rows(f.run / 'responses.jsonl')[-1]
        self.assertEqual(row['request_sha256'], f.plan[200]['request_sha256'])
        self.assertEqual(row['request'], f.prepared[200][2]['payload'])
        self.assertEqual(row['effective_max_active_requests'], 2)
        self.assertEqual(row['latency_s'], .01)  # Pacing never replaces adapter timing.
        probes = P.rows(Path(result['amendment']['recovery_run_dir']) / 'responses.jsonl')
        self.assertTrue(all('min_dispatch_spacing_s' not in r for r in probes))

    def test_real_executor_never_exceeds_two_active_requests(self):
        self.remaining(8)
        InstrumentedAdapter.delay = .03
        with patch.object(P, 'MIN_DISPATCH_SPACING_S', .005):
            result = self.resume()
        self.assertEqual(result['metadata']['status'], 'complete')
        self.assertEqual(InstrumentedAdapter.peak, 2)
        self.assertEqual(len(InstrumentedAdapter.starts), 8)
        self.assertEqual({seq for seq, _ in InstrumentedAdapter.starts}, set(range(200, 208)))
        with self.protocol.ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(pool._max_workers, 2)
        with self.assertRaises(ValueError):
            self.protocol.ThreadPoolExecutor(max_workers=3)

    def test_queued_work_never_dispatches_after_live_circuit_stops(self):
        self.remaining(30)
        InstrumentedAdapter.quality_valid = False
        with patch.object(P, 'MIN_DISPATCH_SPACING_S', .005):
            result = self.resume()
        self.assertEqual(result['metadata']['status'], 'stopped')
        self.assertIn('Five consecutive', result['metadata']['stop_reason'])
        new_rows = P.rows(self.f.run / 'responses.jsonl')[200:]
        new_claims = P.rows(self.f.run / 'attempts.jsonl')[200:]
        self.assertGreaterEqual(len(new_rows), 5)
        self.assertLessEqual(len(new_rows), 6)  # At most one already-active call.
        self.assertEqual(len(new_claims), len(new_rows))
        self.assertEqual({x['sequence'] for x in new_claims}, {x[0] for x in InstrumentedAdapter.starts})
        self.assertEqual(result['metadata']['unknown_billing'], 5 + len(new_rows))

    def test_exact_spacing_precedes_original_claim_and_stop_is_rechecked(self):
        self.remaining(3)
        session = self.session()
        clock = SimpleNamespace(value=10., waits=[], before_wait=None)
        def sleep(delay):
            clock.waits.append(delay)
            clock.value += delay
            if clock.before_wait:
                clock.before_wait()
        clock_api = SimpleNamespace(monotonic=lambda: clock.value, sleep=sleep)
        starts = []
        original = self.protocol.Session.claim
        def record_claim(obj, entry):
            starts.append(clock.value)
            return original(obj, entry)
        with patch.object(P, 'time', clock_api), patch.object(self.protocol.Session, 'claim', record_claim):
            self.assertTrue(session.claim(self.f.plan[200]))
            self.assertTrue(session.claim(self.f.plan[201]))
            size_before = (self.f.run / 'attempts.jsonl').stat().st_size
            clock.before_wait = lambda: session.guard.stop('Synthetic stopped during pacing wait')
            self.assertFalse(session.claim(self.f.plan[202]))
        self.assertEqual(starts, [10., 10.25, 10.5])
        self.assertEqual(clock.waits, [.25, .25])
        self.assertEqual((self.f.run / 'attempts.jsonl').stat().st_size, size_before)

    def test_frozen_request_tampering_is_rejected_before_any_probe(self):
        with (self.f.run / 'requests.jsonl').open('a') as stream:
            stream.write('\n')
        with self.assertRaisesRegex(ValueError, 'Frozen evidence hash changed'):
            self.resume()
        self.assertEqual(F.FakeAdapter.calls, [])

    def test_active_phase_refuses_recovery_before_any_call(self):
        with self.protocol.isolated_phase('synthetic_other_run'):
            with self.assertRaisesRegex(RuntimeError, 'active'):
                self.resume()
        self.assertEqual(F.FakeAdapter.calls, [])

    def test_failed_probe_is_preserved_and_previous_probe_charge_remains(self):
        F.FakeAdapter.probe_valid = False
        failed = self.resume()
        self.assertEqual(failed['status'], 'recovery_failed')
        self.assertFalse(InstrumentedAdapter.starts)
        F.FakeAdapter.probe_valid = True
        result = self.resume()
        self.assertAlmostEqual(result['amendment']['historical_recovery_budget_accounted_usd'], .001)
        self.assertAlmostEqual(result['metadata']['budget_usd'], .997)

    def test_pacing_and_amendment_exist_before_first_probe(self):
        adapter = InstrumentedAdapter.run
        checks = []
        def inspect(client, *args, **kwargs):
            meta = json.loads((self.f.run / 'metadata.json').read_text())
            amendment = meta['operational_amendments'][-1]
            checks.append((meta['effective_max_active_requests'], amendment['min_dispatch_spacing_s']))
            return adapter(client, *args, **kwargs)
        with patch.object(InstrumentedAdapter, 'run', inspect):
            self.resume()
        self.assertTrue(checks)
        self.assertTrue(all(x == (2, .25) for x in checks))


if __name__ == '__main__':
    unittest.main()
