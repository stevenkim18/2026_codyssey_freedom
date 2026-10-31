import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from model.evaluation import cli
from model.tests.test_evaluation_records_compare import make


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_score_with_policy_on_synthetic_files(self):
        text = '김가상 연락처 010-1234-5678 입니다'
        gold = [{'document_id': 'a', 'text': text, 'entities': [{'type': 'PERSON', 'start': 0, 'end': 3},
                                                              {'type': 'KR_PHONE_NUMBER', 'start': 9, 'end': 22}]},
                {'document_id': 'b', 'text': '정상 문장', 'entities': []}]
        pred = [{'document_id': 'a', 'findings': [{'type': 'PERSON', 'start': 0, 'end': 3, 'score': .9}]},
                {'document_id': 'b', 'findings': [{'type': 'KR_RRN', 'start': 0, 'end': 2, 'score': .9}]}]
        for row in pred:
            row['status']='complete'
        with tempfile.TemporaryDirectory() as tmp:
            g, p = Path(tmp) / 'g.jsonl', Path(tmp) / 'p.jsonl'
            g.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in gold), encoding='utf-8')
            p.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in pred), encoding='utf-8')
            code, out = run('score', '--gold', str(g), '--pred', str(p), '--with-policy', '--rules')
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result['metric_id'], 'char_span_exact_v1')
        self.assertEqual((result['overall_micro']['tp'], result['overall_micro']['fp'], result['overall_micro']['fn']), (1, 1, 1))
        self.assertEqual(result['policy']['model_only']['false_block_count'], 1)
        self.assertEqual(result['policy']['model_plus_same_rules']['mode'], 'model_plus_same_rules')
        self.assertEqual(result['critical_micro']['gold'], 1)

    def test_incomplete_or_missing_normal_prediction_is_not_scored(self):
        gold=[{'document_id':'normal','text':'normal fixture','entities':[]}]
        for predictions in [[],[{'document_id':'normal','status':'incomplete','findings':[]}],
                            [{'document_id':'normal','findings':[]}]]:
            with self.subTest(predictions=predictions),tempfile.TemporaryDirectory() as tmp:
                g,p=Path(tmp)/'g.jsonl',Path(tmp)/'p.jsonl'
                g.write_text(''.join(json.dumps(row)+'\n' for row in gold))
                p.write_text(''.join(json.dumps(row)+'\n' for row in predictions))
                code,out=run('score','--gold',str(g),'--pred',str(p),'--with-policy')
                self.assertEqual(code,2)
                self.assertNotIn('overall_micro',json.loads(out))

    def test_check_compare_decide(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.json', Path(tmp) / 'b.json'
            a.write_text(json.dumps(make('m0', hits=24)))
            b.write_text(json.dumps(make('m1', hits=27)))
            self.assertEqual(run('check-record', str(a))[0], 0)
            code, out = run('compare', str(b), str(a))
            self.assertTrue(json.loads(out)['comparable'])
            code, out = run('decide', str(b), str(a))
            self.assertEqual((code, json.loads(out)['verdict']), (3, 'pending_runtime_acceptance'))
            code, out = run('decide', str(b), str(a), '--runtime-accepted')
            self.assertEqual((code, json.loads(out)['verdict']), (0, 'replace'))
            a.write_text(json.dumps({'status': 'executed'}))
            code, out = run('check-record', str(a))
            self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
