import copy
import unittest

from workflow import review


class ReviewSourceProjectionTest(unittest.TestCase):
    def test_sample_projection_preserves_response_and_real_knowledge(self):
        units = {'q-22': {'unit_id': 'q-22', 'unit_kind': 'standalone', 'numbers': [22]}}
        original = [{'unit_id': 'q-22', 'number': '22', 'assessment': {
            'subquestions': [{'number': '22', 'knowledge': []}]}}]
        saved = copy.deepcopy(original)
        projected = review._source_comparison_checks(original, 'sample', units)
        self.assertEqual(projected[0]['assessment']['subquestions'], [])
        self.assertEqual(original, saved)
        original[0]['assessment']['subquestions'][0]['knowledge'] = ['real-tag']
        projected = review._source_comparison_checks(original, 'sample', units)
        self.assertEqual(projected[0]['assessment']['subquestions'][0]['knowledge'], ['real-tag'])


if __name__ == '__main__':
    unittest.main()
