"""Regression: 文末参考答案以罗马序号+行内答案列出时必须切为附录。"""
import unittest

from workflow import extract


class RomanAnswerAppendixTest(unittest.TestCase):
    def test_roman_numbered_inline_answer_key_is_separated(self):
        text = (
            '七上 · 人教版 · 课时A计划\n'
            'Starter Unit 1　Hello!\n'
            '第一课时　Section A (1a-2d)\n'
            'Ⅳ.阅读理解\n'
            'Teacher:Good afternoon,class!\n'
            '(　)1.When is this class?\n'
            'A.In the morning.\tB.In the afternoon.\n'
            'C.In the evening.\tD.At night.\n'
            '**参考答案**\n'
            '[IMG image158.jpeg]基础巩固[IMG image159.jpeg]\n'
            'Ⅰ.1.Bb;Dd;Ff;Jj;Ll;Nn;Pp;Qq;Ss;Uu;Ww;Yy\n'
            'Ⅱ.1.Mike,Peter,Teng Fei,Yaming　2.Ella,Emma,Fu Xing,Helen\n'
            'Ⅲ.1.nice　2.Unit　3.everyone/everybody　4.greets\n'
            '5.class　6.people\n'
            'Ⅳ.1.How do;greet　2.Good morning　3.nice to meet you\n'
            'Ⅴ.1~5 BACDD\n'
        )
        structure, main, appendix = extract.infer_structure(text)
        self.assertNotIn('参考答案', main)
        self.assertNotIn('Bb;Dd', main)
        self.assertIn('Bb;Dd', appendix)
        self.assertEqual(structure['sections'][-1]['questions'], [1])
        self.assertEqual(structure['sections'][-1]['source_end_line'], 8)


if __name__ == '__main__':
    unittest.main()
