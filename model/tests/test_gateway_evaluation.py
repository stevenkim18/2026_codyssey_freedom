import unittest
from types import SimpleNamespace

from model.evaluation.gateway_metrics import gateway_metrics


class Backend:
    manifest={'model_version':'fixture','threshold_version':'fixture'}
    def __init__(self,repeat=False):self.repeat=repeat
    def inspect(self,segments):
        findings=[SimpleNamespace(type='PERSON',start=0,end=3,score=.9)] if self.repeat else []
        return SimpleNamespace(status='complete',findings=findings)


class GatewayEvaluationTests(unittest.TestCase):
    def test_rule_mode_can_reuse_identical_successful_model_reinspection(self):
        rows=[{'id':'normal','text':'가나다 테스트','entities':[]}]
        pred=[{'document_id':'normal','status':'complete','findings':[{'type':'PERSON','start':0,'end':3,'score':.9}]}]
        cache={};backend=Backend()
        first=gateway_metrics(rows,pred,backend,inference_cache=cache)
        second=gateway_metrics(rows,pred,backend,mode='model_plus_same_rules',inference_cache=cache)
        self.assertEqual(first['cached_reinspection_calls'],0)
        self.assertEqual(second['cached_reinspection_calls'],1)
        self.assertEqual(first['normal_false_block'],second['normal_false_block'])

    def test_reinspection_error_is_counted_as_normal_transmission_failure(self):
        rows=[{'id':'normal','text':'가나다 테스트','entities':[]}]
        pred=[{'document_id':'normal','status':'complete','findings':[{'type':'PERSON','start':0,'end':3,'score':.9}]}]
        result=gateway_metrics(rows,pred,Backend(repeat=True))
        self.assertEqual(result['normal_false_block']['false_blocks'],1)
        self.assertEqual(result['normal_false_block']['reinspection_failures'],1)

    def test_clean_reinspection_can_forward_masked_normal_document(self):
        rows=[{'id':'normal','text':'가나다 테스트','entities':[]}]
        pred=[{'document_id':'normal','status':'complete','findings':[{'type':'PERSON','start':0,'end':3,'score':.9}]}]
        result=gateway_metrics(rows,pred,Backend())
        self.assertEqual(result['normal_false_block']['false_blocks'],0)
        self.assertEqual(result['normal_wrong_masks'],1)

    def test_missed_gold_in_allow_documents_is_not_hidden_from_residual(self):
        rows=[{'id':'positive','text':'123456-1234567','entities':[{'type':'KR_RRN','start':0,'end':14}]}]
        pred=[{'document_id':'positive','status':'complete','findings':[]}]
        result=gateway_metrics(rows,pred,Backend())
        self.assertEqual(result['forwarded_protected_gold_entities'],1)
        self.assertEqual(result['residual_protected_entities'],1)
        self.assertEqual(result['residual_rate'],1.0)
