import unittest

from model.evaluation.select_checkpoint import choose
from model.evaluation.paired_ci import paired_intervals
from model.evaluation.records import CRITICAL_TYPES


def profile(f1,recall,failure,regress=False):
    per={kind:{'recall':recall} for kind in CRITICAL_TYPES}
    if regress:per['KR_BANK_ACCOUNT']['recall']=.1
    return {'threshold':.5,'score':{'overall_micro':{'f1':f1},'critical_micro':{'recall':recall},'per_type':per},
            'gateway':{'model_only':{'normal_false_block':{'rate':failure}}}}


class SelectionTests(unittest.TestCase):
    def test_larger_f1_does_not_hide_critical_type_regression(self):
        base={'dev_threshold_profiles':[profile(.6,.6,.01)]}
        bad={'experiment_id':'bad','dev_threshold_profiles':[profile(.9,.8,.01,regress=True)]}
        good={'experiment_id':'good','dev_threshold_profiles':[profile(.8,.8,.015)]}
        winner,_=choose(base,[bad,good])
        self.assertEqual(winner['candidate_experiment_id'],'good')
        self.assertTrue(winner['eligible'])

    def test_no_eligible_pair_is_explicit_fallback_not_success(self):
        base={'dev_threshold_profiles':[profile(.6,.6,.01)]}
        bad={'experiment_id':'bad','dev_threshold_profiles':[profile(.9,.8,.10)]}
        winner,_=choose(base,[bad])
        self.assertFalse(winner['eligible'])


class PairedCITests(unittest.TestCase):
    def test_identical_predictions_have_zero_delta_and_undefined_unsupported_types(self):
        rows=[{'id':'a','text':'123','entities':[{'type':'KR_RRN','start':0,'end':3}]},
              {'id':'b','text':'normal','entities':[]}]
        pred=[{'document_id':'a','status':'complete','findings':[{'type':'KR_RRN','start':0,'end':3,'score':.9}]},
              {'document_id':'b','status':'complete','findings':[]}]
        result=paired_intervals(rows,pred,pred,iterations=200)
        self.assertEqual(result['metrics']['overall_f1']['estimate_pp'],0)
        self.assertEqual(result['metrics']['overall_f1']['low_pp'],0)
        self.assertEqual(result['metrics']['critical_micro_recall']['high_pp'],0)
        self.assertIsNone(result['metrics']['recall:CREDIT_CARD']['estimate_pp'])
