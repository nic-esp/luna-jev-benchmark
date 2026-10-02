import json
import unittest
from unittest.mock import Mock, patch
import runner

class RunnerTests(unittest.TestCase):
    def test_labels_do_not_reach_models(self):
        case = runner.warmup_cases()[0]
        case['source'] = {'secret_ground_truth': 1}
        for label in runner.MODELS:
            _, payload = runner.payload_for(case, label)
            text = json.dumps(payload)
            self.assertNotIn('target', text)
            self.assertNotIn('secret_ground_truth', text)
        jev = runner.payload_for(case, 'Jev')[1]
        luna = runner.payload_for(case, 'Luna')[1]
        self.assertEqual(json.loads(luna['messages'][1]['content']),
                         {k:jev[k] for k in ['state','questions']})

    def test_strict_invalid_values(self):
        for answer in [{'type':'noul','noul':True},{'type':'noul','noul':float('nan')},
                       {'type':'noul','noul':1.1},{'type':'noul','noul':'0.5'},
                       {'type':'noul','noul':.5,'explanation':'extra'}]:
            with self.assertRaises(ValueError):
                runner.validate_answer(answer)

    def test_other_models_rejected(self):
        for model in ['openai/gpt-6-luna-pro','openai/gpt-5.6-luna',None]:
            with self.assertRaises(ValueError):
                runner.validate_model('Luna',model)
        runner.validate_model('Jev','typesafe/jev-1.13-20260917')

    def _client(self, response):
        client = runner.Client('sk-or-test-placeholder')
        connection = Mock()
        http = Mock(status=200)
        http.read.return_value = json.dumps(response).encode()
        connection.getresponse.return_value = http
        client.connection = connection
        return client, connection

    def test_truncated_output_is_failure_but_charged(self):
        client,conn = self._client({'model':'openai/gpt-6-luna', 'usage':{'cost':.01},
            'choices':[{'finish_reason':'length','message':{'content':'{"type":"noul"'}}]})
        row = client.run(runner.warmup_cases()[0],'Luna','measured',0)
        self.assertFalse(row['valid'])
        self.assertEqual(row['cost_usd'],.01)
        self.assertEqual(conn.request.call_count,1)
        self.assertIsNone(row['probability'])

    def test_missing_cost_is_unknown_not_zero(self):
        client,_ = self._client({'model':'typesafe/jev-1.13','answers':{
            'answer':{'type':'noul','noul':.7}}})
        row = client.run(runner.warmup_cases()[0],'Jev','measured',0)
        self.assertTrue(row['valid'])
        self.assertIsNone(row['cost_usd'])

    def test_exception_cannot_echo_key(self):
        client = runner.Client('sk-or-test-placeholder')
        client.connection = Mock()
        client.connection.request.side_effect = RuntimeError('bad sk-or-test-placeholder')
        row = client.run(runner.warmup_cases()[0],'Jev','measured',0)
        self.assertNotIn('sk-or-test-placeholder', json.dumps(row))

    def test_cache_arm_retains_model_identity(self):
        client,_ = self._client({'model':'openai/gpt-6-luna','usage':{'cost':.01},
             'choices':[{'finish_reason':'stop','message':{'content':'{"type":"noul","noul":0.5}'}}]})
        _,request = runner.payload_for(runner.warmup_cases()[0],'Luna')
        row = client.run(runner.warmup_cases()[0],'Luna cached','measured',0,
                         request_override=request, base_label='Luna')
        self.assertTrue(row['valid'])
        self.assertEqual(row['model_label'],'Luna cached')
        self.assertEqual(row['requested_model'],'openai/gpt-6-luna')

if __name__ == '__main__':
    unittest.main()
