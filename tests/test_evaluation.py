import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from rouge_score.rouge_scorer import RougeScorer
from rag.evaluate import (token_scores, standard_scores, load_inputs, digest, score_run)
from rag.judge import parse_judgment, RUBRIC


class EvaluationTests(unittest.TestCase):
    def test_token_examples(self):
        self.assertEqual(token_scores('William Pitt', 'William Pitt'), (1, 1))
        self.assertEqual(token_scores('Pitt', 'William Pitt'), (.5, 2/3))
        self.assertEqual(token_scores('William Pitt was British', 'William Pitt'), (1, 2/3))

    def test_multiset_and_empty(self):
        self.assertEqual(token_scores('pitt pitt', 'pitt'), (1, 2/3))
        self.assertEqual(token_scores('', 'pitt'), (0, 0))
        self.assertEqual(token_scores('the', 'the'), (0, 0))

    def test_normalization(self):
        self.assertEqual(token_scores('The WILLIAM Pitt!', 'William Pitt'), (1, 1))

    def test_rouge_multi_reference(self):
        s = RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=False)
        result = standard_scores('December 10', ['December 10, 2026', 'December 10'], s)
        self.assertEqual(result['f1'], 1)
        self.assertEqual(result['rouge_avg'], 1)
        result = standard_scores('hockey', ['hockey'], s)
        self.assertEqual(result['rouge2'], 0)
        self.assertAlmostEqual(result['rouge_avg'], 2/3)

    def test_judge_validation(self):
        good = dict(score=4, reason='Minor omission.', error_types=[], needs_review=False)
        self.assertEqual(parse_judgment(json.dumps(good))['score'], 4)
        for bad in [True, 3.5, 0, 6, '5']:
            with self.assertRaises(ValueError):
                parse_judgment(json.dumps({**good, 'score': bad}))
        with self.assertRaises(ValueError):
            parse_judgment('score: 5')

    def fixture(self, folder):
        gold = folder/'gold.json'
        pred = folder/'predictions.json'
        gold.write_text(json.dumps([{'id':'1','question':'Who?', 'answers':['William Pitt']},
                                    {'id':'2','question':'When?', 'answers':['December 10']}]))
        pred.write_text(json.dumps({'2':'December 10 [D2]', '1':'William Pitt'}))
        return gold, pred

    def test_input_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            gold, pred = self.fixture(Path(d))
            rows = load_inputs(gold, pred)
            self.assertEqual(rows[1]['answer'], 'December 10')
            pred.write_text(json.dumps({'1':'Pitt'}))
            with self.assertRaisesRegex(ValueError, 'ID mismatch'):
                load_inputs(gold, pred)
            pred.write_text(json.dumps([{'id':'1','answer':'x'}, {'id':'1','answer':'x'}]))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                load_inputs(gold, pred)
            pred.write_text(json.dumps({'1':None, '2':'x'}))
            with self.assertRaisesRegex(ValueError, 'string'):
                load_inputs(gold, pred)

    def test_full_and_partial_judge_aggregation_and_stale_cache(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            gold, pred = self.fixture(folder)
            rows = load_inputs(gold, pred)
            judges = [{'id':r['id'], 'input_hash':r['input_hash'], 'rubric_hash':digest(RUBRIC),
                       'judge_config_hash':'test-only', 'score':4, 'reason':'Test fixture',
                       'error_types':[], 'needs_review':False} for r in rows]
            jp = folder/'judge.json'
            labels = folder/'labels.json'
            labels.write_text(json.dumps([{'id':'1','category':'entity'}, {'id':'2','category':'date'}]))
            def run(name, records):
                jp.write_text(json.dumps(records))
                out = folder/name
                score_run(SimpleNamespace(references=str(gold), predictions=str(pred), judge=str(jp),
                          labels=str(labels), system='synthetic-test', output=str(out)))
                return json.loads((out/'summary.json').read_text())
            result = run('full', judges)
            self.assertAlmostEqual(result['total'], .9375)
            self.assertEqual(result['n'], 2)
            result = run('partial', judges[:1])
            self.assertIsNone(result['total'])
            self.assertIsNone(result['judge_raw'])
            self.assertEqual(result['standard_only'], 1)
            judges[0]['input_hash'] = 'stale'
            with self.assertRaisesRegex(ValueError, 'Stale'):
                run('stale', judges)


if __name__ == '__main__':
    unittest.main()
