import unittest
from workflow import content_rules


class PreserveTextTest(unittest.TestCase):
    def test_media_markers_do_not_create_false_assemble_mismatch(self):
        row = {'explanation': '正文 [IMG image4.jpeg]age n.很久'}
        upstream = {'explanation': '正文 age n.很久'}
        content_rules.preserve(row, upstream)

    def test_real_explanation_change_still_fails(self):
        with self.assertRaises(ValueError):
            content_rules.preserve({'explanation': '正文A'}, {'explanation': '正文B'})

    def test_child_media_markers_do_not_create_false_mismatch(self):
        content_rules.preserve(
            {'subquestions': [{'number': 40, 'explanation': '[IMG x]相同'}]},
            {'subquestions': [{'number': 40, 'explanation': '相同'}]},
        )


if __name__ == '__main__':
    unittest.main()
