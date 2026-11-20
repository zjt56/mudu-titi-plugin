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

    def test_phonetic_picture_columns_keep_all_printed_question_numbers(self):
        text = (
            '七上·人教版·课时A计划\nUnit 2\n第二课时 Pronunciation\n'
            'A 基础巩固\n'
            'Ⅱ.根据图片及所给音标写出下列单词所缺的字母\n'
            '**1.**[IMG image21.jpeg]\n'
            'c______/eɪ/ke\t2.[IMG image22.jpeg]\n'
            'n______/əʊ/se\n'
            '**3.**[IMG image23.jpeg]\n'
            'b______/aɪ/ke\t4.[IMG image24.jpeg]\n'
            'm______/iː/t\n'
            '**5.**[IMG image25.jpeg]\n'
            'comp______/juː/ter\n'
            'Ⅲ.将下面歌谣中画线的单词按所给音标归类\n'
            '1. Wake up, little Kate.\n'
        )
        structure, _, _ = infer_structure(text)
        section = next(row for row in structure['sections'] if row['title'].startswith('Ⅱ.'))
        self.assertEqual(section['questions'], [1, 2, 3, 4, 5])
        self.assertEqual([row['number'] for row in section['question_locations']], [1, 2, 3, 4, 5])
        self.assertEqual([row['kind'] for row in section['question_locations']],
                         ['question'] * 5)
        from workflow.structure_gate import _scope_anchors
        body_lines = text.splitlines()
        start = next(i for i, line in enumerate(body_lines) if line.startswith('Ⅱ.'))
        next_section = next(i for i, line in enumerate(body_lines[start + 1:], start + 1)
                            if line.startswith('Ⅲ.'))
        self.assertEqual([n for _, n in _scope_anchors(body_lines[start:next_section])],
                         [1, 2, 3, 4, 5])

    def test_non_sync_exam_does_not_gain_dialogue_inline_fallback(self):
        text = '2025年期末考试试卷\nⅠ.补全对话\n' + self.DIALOGUE
        structure, _, _ = infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [])

    # 短文填空段落里的图注空：`N.[IMG …](提示)`（图片+紧随括号提示）是合法小题号，
    # 与 `<u></u>` 文本空一起构成完整题号序列；孤立 `N.[IMG …]`（无括号提示）不算。
    CLOZE = ('Unit 1\n第一课时 Section B\nⅣ.根据短文内容或所给提示填空\n'
             'My uncle is a farmer.He has ninety apple 1.[IMG image10.jpeg](tree) on his farm.'
             'My uncle and my 2.[IMG image10.jpeg](/aːnt/) can’t pick all the apples by themselves. '
             '3.<u>　　　　</u> they ask my parents and me. It is 4.<u>　　　　</u>early in the morning.\n'
             '参考答案\nⅣ.1.trees　2.aunt　3.So　4.at\n')

    def test_passage_image_hint_blanks_are_numbered_slots(self):
        from workflow.preview import _weak_numbers
        from workflow.extract import blank_numbers
        from workflow.structure_gate import _scope_anchors
        line = self.CLOZE.splitlines()[3]
        self.assertEqual(blank_numbers(line), [1, 2])
        self.assertEqual(_weak_numbers(line), [1, 2, 3, 4])
        self.assertEqual(sorted({n for _, n in _scope_anchors([line])}), [1, 2, 3, 4])
        structure, _, _ = infer_structure(self.CLOZE)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2, 3, 4])

    def test_bare_number_then_image_without_hint_is_not_a_slot(self):
        from workflow.preview import _weak_numbers
        from workflow.extract import blank_numbers
        for text in ('1.[IMG option.png]', 'A.2.[IMG option.png]',
                     'See picture 3.[IMG photo.jpeg] here'):
            self.assertEqual(_weak_numbers(text), [], text)
            self.assertEqual(blank_numbers(text), [], text)

    # 表格同一行里的 `N.[IMG …]` 与编号空档连续相邻时是只有图片提示的小题
    # （Ⅱ 歌谣填表第 6 空原文只有一张小图，答案给 6.sit）；孤立的图号仍不算小题。
    PICTURE_CELL = (
        'Unit 3\n第二课时 Pronunciation\n'
        'Ⅱ.熟读下面的歌谣并根据表格规律,在歌谣中找出对应的单词完成表格\n'
        '[TABLE 1]\n'
        '| a | /æ/ | 1.<u>　　</u>,and,2.<u>　　</u> |\n'
        '| e | /e/ | 3.<u>　　</u>,4.<u>　　</u>,tell |\n'
        '| i | /ɪ/ | big,5.<u>　　</u>,will,listen,music,pink,6.[IMG image40.jpeg],in |\n'
        '| o | /ɒ/ | 7.<u>　　</u>,8.<u>　　</u>,frog |\n'
        '| u | /ʌ/ | sun,9.<u>　　</u> |\n'
        '[/TABLE 1]\n')

    def test_table_picture_cell_beside_numbered_blanks_is_a_slot(self):
        from workflow.preview import _weak_numbers, picture_cell_numbers
        from workflow.structure_gate import _scope_anchors
        lines = self.PICTURE_CELL.splitlines()
        row = [line for line in lines if 'image40' in line][0]
        self.assertEqual(picture_cell_numbers(row), [6])
        self.assertEqual(_weak_numbers(row), [5, 6])
        for text in ('6.[IMG image40.jpeg]', '6.[IMG image40.jpeg],in',
                     'A.6.[IMG image40.jpeg]'):
            self.assertEqual(_weak_numbers(text), [], text)
        # 表格五行的编号空档加上图片小题 6 构成 1–9 的完整序列。
        self.assertEqual([number for _, number in _scope_anchors(lines)], list(range(1, 10)))


if __name__ == '__main__':
    unittest.main()
