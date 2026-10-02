"""Pinned OpenRouter adapters preserving the frozen Jev probability contract."""
from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
import math
from pathlib import Path
import ssl
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import runner


def model_specs(include_mandatory=False):
    catalog = json.loads((ROOT / 'sources/catalog.json').read_text())['data']
    by_id = {x['id']: x for x in catalog}
    definitions = [
        ('Jev', 'typesafe/jev-1.13', 'TypeSafe', None, 'unsupported'),
        ('Luna', 'openai/gpt-6-luna', 'OpenAI', {'effort': 'none'}, 'explicit'),
        ('Qwen3.8 Flash', 'qwen/qwen3.8-flash', 'alibaba', {'effort': 'none'}, 'unsupported'),
    ]
    if include_mandatory:
        definitions += [
            ('Gemini 3.8 Flash', 'google/gemini-3.8-flash', 'google-ai-studio', {'effort': 'low'}, 'unsupported'),
            ('GLM 5.3 Flash', 'z-ai/glm-5.3-flash', 'deepinfra/fp4', {'effort': 'low'}, 'unsupported'),
        ]
    result = []
    for label, mid, provider, reasoning, cache in definitions:
        # The alpha Decisions endpoint is absent from the public chat catalog.
        # Its conservative reservation is inherited from the frozen runner;
        # every result still uses the actual response charge.
        model = by_id.get(mid) or {'pricing': {'prompt': '0.0000002', 'completion': '0.00000075'}}
        result.append({'label': label, 'id': mid, 'provider': provider,
                       'provider_name': {'Jev': 'TypeSafe', 'Luna': 'OpenAI', 'Qwen3.8 Flash': 'Alibaba', 'Gemini 3.8 Flash': 'Google AI Studio', 'GLM 5.3 Flash': 'DeepInfra'}[label],
                       'prices': model['pricing'], 'reasoning': reasoning,
                       'cache_control': cache, 'max_tokens': None if label == 'Jev' else 128,
                       'reasoning_mandatory': bool((model.get('reasoning') or {}).get('mandatory')),
                       'protocol_difference': 'Mandatory reasoning at minimum supported effort' if reasoning == {'effort': 'low'} else None})
    return result


class Adapter:
    def __init__(self, key, timeout=60):
        if not key.startswith('sk-or-'):
            raise ValueError('Expected OpenRouter credential')
        self.key, self.timeout, self.connection = key, timeout, None

    def close(self):
        if self.connection:
            self.connection.close()
        self.connection = None

    def build_request(self, case, spec, cache_mode=None, cache_key=None):
        shared = runner.shared_input(case)
        if spec['label'] == 'Jev':
            return {'endpoint': '/api/alpha/decisions', 'payload': {'model': spec['id'], **shared}}
        content = json.dumps(shared, ensure_ascii=False, sort_keys=True)
        if cache_mode is not None:
            # The identical prefix/fact split is used in all long-input arms.
            from cache_protocol import text_blocks
            content = text_blocks(case)
            if cache_mode == 'cached':
                if spec['cache_control'] != 'explicit':
                    raise ValueError('Unverified cache treatment')
                content[0]['prompt_cache_breakpoint'] = {'mode': 'explicit'}
        payload = {
            'model': spec['id'],
            'messages': [{'role': 'system', 'content': runner.SYSTEM}, {'role': 'user', 'content': content}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'noul_answer', 'strict': True, 'schema': deepcopy(runner.SCHEMA)}},
            'reasoning': deepcopy(spec['reasoning']), 'max_tokens': 128,
            'provider': {'only': [spec['provider']], 'allow_fallbacks': False, 'require_parameters': True},
            'stream': False,
        }
        if spec['cache_control'] == 'explicit':
            payload['prompt_cache_options'] = {'mode': 'explicit'}
            if cache_mode in ('cached', 'uncached'):
                payload['prompt_cache_options']['ttl'] = '30m'
            if cache_key is not None:
                payload['prompt_cache_key'] = cache_key
        return {'endpoint': '/api/v1/chat/completions', 'payload': payload}

    def reserve_usd(self, request, spec):
        prices = spec['prices']
        input_rate = max(float(prices.get(k) or 0) for k in ('prompt', 'input_cache_write', 'input_cache_read'))
        output_rate = max(float(prices.get(k) or 0) for k in ('completion', 'internal_reasoning'))
        return max(.000001, (len(json.dumps(request, ensure_ascii=False).encode()) + 4096) * input_rate + 128 * output_rate)

    def run(self, case, spec, arm_label, phase, sequence, endpoint, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        row = {'case_id': case['id'], 'experiment': case['experiment'], 'target': case['target'],
               'target_kind': case['target_kind'], 'model_label': arm_label, 'base_model_label': spec['label'],
               'requested_model': spec['id'], 'phase': phase, 'sequence': sequence,
               'started_at': datetime.now(timezone.utc).isoformat(), 'endpoint': endpoint, 'request': payload,
               'probability': None, 'valid': False, 'error': None, 'cost_usd': None, 'usage': {},
               'response_model': None, 'provider': None, 'reasoning_verified_disabled': None}
        started = time.perf_counter()
        try:
            if self.connection is None:
                self.connection = http.client.HTTPSConnection('openrouter.ai', timeout=self.timeout, context=ssl.create_default_context())
            self.connection.request('POST', endpoint, body=encoded, headers={
                'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                'X-Title': 'Constrained-output comparative study'})
            response = self.connection.getresponse()
            body = response.read().decode('utf-8')
            row['http_status'] = response.status
            data = json.loads(body)
            row['response'] = data
            usage = data.get('usage') or {}
            row.update(usage=usage, response_model=data.get('model'), provider=data.get('provider'), generation_id=data.get('id'))
            cost = usage.get('cost')
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                row['cost_usd'] = float(cost)
            if response.status != 200 or 'error' in data:
                raise ValueError('HTTP/API error: ' + json.dumps(data)[:1200])
            served = data.get('model')
            if not isinstance(served, str) or not (served == spec['id'] or served.startswith(spec['id'] + '-20')):
                raise ValueError('Unexpected or missing served model: ' + str(served))
            if spec.get('provider_name') and data.get('provider') != spec['provider_name']:
                raise ValueError('Unexpected or missing served provider: ' + str(data.get('provider')))
            if spec['label'] == 'Jev':
                answer = data['answers']['answer']
            else:
                choice = data['choices'][0]
                row['finish_reason'] = choice.get('finish_reason')
                message = choice['message']
                reasoning_n = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
                row['reported_reasoning_tokens'] = reasoning_n
                row['visible_reasoning'] = bool(message.get('reasoning') or message.get('reasoning_details'))
                if spec['reasoning'].get('effort') == 'none':
                    row['reasoning_verified_disabled'] = reasoning_n == 0 and not row['visible_reasoning']
                    if isinstance(reasoning_n, (int, float)) and reasoning_n > 0 or row['visible_reasoning']:
                        raise ValueError('Reasoning output violates disabled-reasoning protocol')
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('Completion did not finish normally')
                answer = json.loads(message['content'])
            row['answer'] = answer
            row['probability'] = runner.validate_answer(answer)
            row['valid'] = True
        except Exception as exc:
            row['error'] = (type(exc).__name__ + ': ' + str(exc)).replace(self.key, '[REDACTED]')
            self.close()
        row['latency_s'] = time.perf_counter() - started
        return json.loads(json.dumps(row).replace(self.key, '[REDACTED]'))
