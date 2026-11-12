"""Sync dialogue slots derive from body markers, never the answer appendix."""
import unittest

from workflow.extract import infer_structure


class ExtractSyncDialogueBlanksTest(unittest.TestCase):
    DIALOGUE = (
        'Amy:Hello,Bill.1.<u>　　　　</u> How is it going?\u00a0\n'
        'Bill:Great,thanks.I’m on vacation in Guilin.\n'
        'Amy:Sounds like you’re having a good time.2.[IMG image3.jpeg]\n'
        'Bill:I’m sitting by the pool and drinking orange juice.\n'
        'Amy:Oh,how nice!3.<u>　　　\u00a0</u>\n'
        'Bill:Yes,I did.I’ll show you the photos later.\n'
        'Amy:That’s great!\n'
        'Bill:By the way,I called Dave.But there was no answer.4.<u>　　　　</u>\n'
        'Amy:Sure,no problem.What’s it?\n'
        'Bill:Just tell him to feed my dog on time every day.\n'
        'Amy:OK,I will.5.<u>　　　\u00a0</u>\n'
        'Bill:You too.Thank you.See you.\n'
        'Amy:See you.\n'
        'A.This is Amy.\nB.Can I help you?\nC.Have a good day!\n'
        'D.Did you take any photos?\nE.What are you doing now?\n'
        'F.Could you take a message?\nG.I visited many places of interest.\n'
    )

    def test_real_five_body_slots_include_numbered_image_and_preserve_text(self):
        text = 'Unit 1\n第一课时 Section A\n补全对话\n' + self.DIALOGUE
        structure, body, appendix = infer_structure(text)
        section = structure['sections'][0]
        self.assertEqual(section['questions'], [1, 2, 3, 4, 5])
        self.assertEqual(section['question_locations'], [
            {'number': n, 'line': line, 'kind': 'blank'}
            for n, line in [(1, 4), (2, 6), (3, 8), (4, 11), (5, 14)]])
        self.assertEqual(body, text)
        self.assertEqual(appendix, '')
        self.assertFalse(any(r['code'] == 'QUESTION_BOUNDARY_MISSING' for r in structure['unresolved']))
        self.assertNotIn('numbers_inferred', section)
        self.assertEqual(structure['groups'][0]['questions'], [1, 2, 3, 4, 5])

    def test_shared_preview_gate_identity_anchors_see_same_five_slots(self):
        from workflow.preview import _weak_numbers
        from workflow.structure_gate import _scope_anchors
        lines = self.DIALOGUE.splitlines()
        self.assertEqual(_weak_numbers(lines[2]), [2])
        self.assertEqual([n for _, n in _scope_anchors(lines)], [1, 2, 3, 4, 5])
        for text in ['2.[IMG image.png]', 'A.2.[IMG image.png]',
                     'Amy:Picture [IMG image.png]', 'Amy:No.2 is shown [IMG image.png]']:
            self.assertEqual(_weak_numbers(text), [], text)

    def test_appendix_does_not_supply_missing_body_slot(self):
        text = 'Unit 1\n第一课时 Section A\n补全对话\n' + self.DIALOGUE.replace(
            '2.[IMG image3.jpeg]', '[IMG image3.jpeg]') + '\n参考答案\n1~5 AEFBC\n'
        structure, _, _ = infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [1, 3, 4, 5])

    def test_normal_sentence_numbers_and_non_dialogue_images_are_not_slots(self):
        text = ('Unit 1\n第一课时 Section A\n补全对话\n'
                'Amy:No.5 is my room.\n1.[IMG option.png]\nA.[IMG option2.png]\n')
        structure, _, _ = infer_structure(text)
        # Existing explicit question 1 remains; No.5 is no blank proof.
        self.assertEqual(structure['sections'][0]['questions'], [1])

    def test_non_sync_exam_does_not_gain_dialogue_inline_fallback(self):
        text = '2025年期末考试试卷\nⅠ.补全对话\n' + self.DIALOGUE
        structure, _, _ = infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [])


if __name__ == '__main__':
    unittest.main()
