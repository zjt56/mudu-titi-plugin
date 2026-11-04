"""Paper-section type mapping uses only source headings, never model labels."""
import unittest

from workflow.review_paper_scope import heading_type, question_sections


class PaperScopeTest(unittest.TestCase):
    def test_heading_type_mapping(self):
        cases = {
            '关键词语选择': '听力关键词语选择',
            '短对话理解': '听力短对话理解',
            '长对话理解': '听力长对话理解',
            '短文理解': '听力短文理解',
            '信息转换': '听力信息转换',
            '单项选择': '单项填空',
            '完形填空': '完形填空',
            '补全对话': '补全对话',
            '任务型阅读': '任务型阅读',
            '阅读理解': '阅读理解',
            '单词拼写': '单词拼写',
            '书面表达': '书面表达',
            '其他说明': None,
        }
        for text, expected in cases.items():
            self.assertEqual(heading_type(text), expected, text)

    def test_numbers_follow_declared_sections(self):
        source = ('第一部分 听力\nⅠ.关键词语选择(共2小题)\n(　　)1.A.a B.b C.c\n(　　)2.A.a B.b C.c\n'
                  'Ⅱ.短对话理解(共1小题)\n(　　)3.Where?\nA.a B.b C.c\n'
                  '第二部分 笔试\nⅥ.单项填空(共1小题)\n(　　)4.Choose.\nA.a B.b C.c\n')
        result = question_sections(source)
        self.assertEqual(result['types'], {'1': '听力关键词语选择', '2': '听力关键词语选择',
                                           '3': '听力短对话理解', '4': '单项填空'})
        self.assertEqual(result['unassigned'], [])
        self.assertEqual(result['declared_types'],
                         ['单项填空', '听力关键词语选择', '听力短对话理解'])

    def test_numbered_blanks_are_assigned_to_the_section(self):
        source = ('Ⅴ.信息转换(共2小题)\n'
                  'Who | The students of Class Eight,Grade<u>\u300026\u3000 </u>\n'
                  'When | Last<u>\u300027\u3000 </u>\n')
        result = question_sections(source)
        self.assertEqual(result['types'], {'26': '听力信息转换', '27': '听力信息转换'})

    def test_numbers_before_any_heading_stay_unassigned(self):
        source = '1. Early question?\nA.a B.b C.c\nⅠ.关键词语选择\n2. Later?\nA.a B.b C.c\n'
        result = question_sections(source)
        self.assertEqual(result['unassigned'], ['1'])
        self.assertEqual(result['types'], {'2': '听力关键词语选择'})

    def test_option_lines_and_prose_are_not_headings(self):
        source = ('Ⅰ.阅读理解(共1小题)\nA. book\nB. pen\nShe reads a book.\n'
                  '3. What happens?\nA.a B.b C.c\n')
        self.assertEqual(question_sections(source)['types'], {'3': '阅读理解'})

    def test_non_text_source_is_rejected(self):
        with self.assertRaises(ValueError):
            question_sections(None)


if __name__ == '__main__':
    unittest.main()
