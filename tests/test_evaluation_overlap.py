"""Regression tests for user-specified metric protocol; no models or dataset runs."""
import unittest
from rag.evaluate import f1_calc, overlap_scores, overlap_aggregate
class OverlapTests(unittest.TestCase):
    def test_strict_em_normalized_f1(self):
        s=overlap_scores('The WILLIAM Pitt!', ['William Pitt'])
        self.assertEqual(s['exact_match'],0)
        self.assertEqual((s['f1'],s['precision'],s['recall']),(1,1,1))
        self.assertEqual(overlap_scores('William Pitt ',['William Pitt'])['exact_match'],0)
    def test_repetitions_empty(self):
        self.assertEqual(f1_calc('pitt pitt','pitt'),(2/3,.5,1))
        self.assertEqual(f1_calc('the','the'),(0,0,0))
        self.assertEqual(f1_calc('','Pitt'),(0,0,0))
    def test_multiple_references(self):
        s=overlap_scores('Pitt', ['William Pitt','Pitt'])
        self.assertEqual(s['exact_match'],1)
        self.assertEqual(s['selected_reference_index'],1)
        s=overlap_scores('a b c', ['b','b c d e f'])
        self.assertEqual(s['selected_reference_index'],0)
        self.assertEqual(s['precision'],.5)
        self.assertEqual(s['recall'],1)
    def test_macro(self):
        a=overlap_scores('William Pitt',['William Pitt'])
        b=overlap_scores('Unknown',['William Pitt'])
        result=overlap_aggregate([a,b])
        self.assertEqual(result['n'],2)
        for k in ['exact_match','f1','precision','recall']:self.assertEqual(result[k],.5)
if __name__=='__main__':unittest.main()
