# -*- coding: utf-8 -*-
"""同步练习/课时同步判定口径的回归测试。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import sync_practice as sp

ROOT = Path(__file__).resolve().parents[1]


class TestRulesFile(unittest.TestCase):
    def test_rules_reference_only_catalog_types_and_subtypes(self):
        self.assertEqual(sp.validate(), [])

    def test_every_type_has_default_kind(self):
        for name in sp.type_kind():
            self.assertIn(sp.kind_for(name), ("group", "standalone"), name)

    def test_rules_are_ordered_and_unique(self):
        ids = [rule.get("id") for rule in sp.rules()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(ids))


class TestClassify(unittest.TestCase):
    CASES = [
        ("Ⅰ.根据首字母及汉语提示补全单词", "单词拼写", "汉语及首字母", "standalone"),
        ("Ⅳ.根据汉语意思完成句子,每空一词", "完成句子", "汉语提示", "standalone"),
        ("Ⅲ.按要求完成句子,每空一词", "完成句子", "改写句子", "standalone"),
        ("Ⅱ.单项填空", "单项填空", "四个选项", "standalone"),
        ("Ⅱ.完形填空", "完形填空", None, "group"),
        ("Ⅳ.阅读理解[2025·滁州天长市月考]", "阅读理解", None, "group"),
        ("Ⅱ.任务型阅读", "任务型阅读", None, "group"),
        ("Ⅲ.补全对话", "补全对话", None, "group"),
        ("Ⅱ.填入恰当的特殊疑问词或词组,完成对话", "补全对话", None, "group"),
        ("Ⅲ.根据所给汉语提示完成表格中的填词游戏,并补充第6题所缺的字母", "单词拼写", "汉语提示", "group"),
        ("用方框中所给单词或短语的适当形式填空", "选词填空", "多句", "group"),
        ("Ⅰ.根据短文内容及所给提示填空", "短文填空", None, "group"),
        ("教材语篇回顾", "教材语篇填空", "人教版", "group"),
        ("Ⅱ.写出下列缩写词的完全形式", "单词拼写", "词形转换", "standalone"),
        ("Ⅱ.按照名字的字母顺序为下列人物排序,并按照性别进行分类", "语音题", None, "group"),
        ("选出下列每组单词中画线部分读音不同的一项", "语音题", None, "group"),
        ("根据所给音标写出单词", "单词拼写", "句意及音标", "group"),
        ("根据下图家谱所示信息完成下列句子,每空仅填一词", "单词拼写", "看图填词", "group"),
        ("用can、can’t、could或couldn’t的适当形式填空", "单词拼写", "句意", "group"),
        ("根据句意用介词in、on或under填空", "单词拼写", "句意", "standalone"),
        ("写出下面画线词的汉语意思", "翻译", "英翻汉", "standalone"),
        ("☆佳句仿写", "翻译", "汉翻英", "standalone"),
        ("根据例句及汉语意思仿写句子", "翻译", "汉翻英", "standalone"),
    ]

    def test_representative_titles(self):
        for title, type_name, subtype, kind in self.CASES:
            with self.subTest(title=title):
                hit = sp.classify(title)
                self.assertIsNotNone(hit, title)
                self.assertEqual(hit["type"], type_name)
                self.assertEqual(hit["subtype"], subtype)
                self.assertEqual(hit["kind"], kind)

    def test_group_and_standalone_follow_material_sharing(self):
        # 语音题整大题、看图题、方框题都共用材料 -> group
        for title in ("将下列字母按照其所含音素进行正确归类", "根据地图用合适的方位介词或介词短语填空",
                      "根据音标从方框中选择合适的单词填空", "判断下列各组字母的大小写是否一致(在括号内写T或F)"):
            self.assertEqual(sp.classify(title)["kind"], "group", title)
        # 一句一空、可独立作答 -> standalone
        for title in ("根据句意用所给词的适当形式填空", "根据首字母或汉语提示完成单词",
                      "找出并改正下列句子中的错误", "Ⅳ.单项填空"):
            self.assertEqual(sp.classify(title)["kind"], "standalone", title)

    def test_unknown_title_returns_none(self):
        self.assertIsNone(sp.classify("Ⅴ.听录音,选择正确的图片"))

    def test_normalize_strips_numbers_and_labels(self):
        self.assertEqual(sp.normalize("Ⅰ.阅读理解[情境题]"), "阅读理解")
        self.assertEqual(sp.normalize("【2026·淮南期末】Ⅲ.补全对话"), "补全对话")
        self.assertEqual(sp.normalize(" Ⅰ ． 单项填空 "), "单项填空")


class TestRefineWithBody(unittest.TestCase):
    def test_reading_subtype_from_item_count(self):
        body = "根据短文内容,选择最佳答案。\n(　)1.A.x\n(　)2.A.x\n(　)3.A.x\n(　)4.A.x"
        self.assertEqual(sp.classify_with_body("Ⅳ.阅读理解", body)["subtype"], "选择4题")
        body3 = "(　)1.A.x\n(　)2.A.x\n(　)3.A.x"
        self.assertEqual(sp.classify_with_body("Ⅳ.阅读理解", body3)["subtype"], "选择3题")

    def test_cloze_subtype_counts_blanks_not_extended_prompts(self):
        body = ("Good morning,class.That’s the \u30001\u3000.Let’s have our \u30002\u3000 now.\n" +
                "".join(f"(　){n}.A.x\n" for n in range(1, 11)))
        self.assertEqual(sp.classify_with_body("Ⅱ.完形填空", body)["subtype"], "10空")
        with_ext = body + "\n☆拓展设问 (　)11.Why?\n"
        self.assertEqual(sp.classify_with_body("Ⅱ.完形填空", with_ext)["subtype"], "10空")

    def test_cloze_with_unknown_blank_count(self):
        hit = sp.classify_with_body("Ⅱ.完形填空", "".join(f"(　){n}.A.x\n" for n in range(1, 8)))
        self.assertEqual(hit["subtype"], "其他空数")

    def test_task_reading_defaults_to_qa(self):
        hit = sp.classify_with_body("Ⅱ.任务型阅读", "阅读下面短文,根据短文内容回答问题。\n1.______\n2.______")
        self.assertEqual(hit["subtype"], "阅读问答")
        mixed = "阅读下面短文,根据短文内容完成各题。\n(　)1.A.x\n排序\n"
        self.assertEqual(sp.classify_with_body("Ⅱ.任务型阅读", mixed)["subtype"], "阅读综合")

    def test_dialogue_seven_choose_five(self):
        body = "A.I’m fine.\nB.Me too.\nC.Yes.\nD.No.\nE.Sure.\nF.Hi.\nG.Thanks."
        self.assertEqual(sp.classify_with_body("Ⅲ.补全对话", body)["subtype"], "7选5")

    def test_body_does_not_override_known_subtype(self):
        hit = sp.classify_with_body("Ⅰ.根据短文内容及所给提示填空", "(　)1.A.x\n(　)2.A.x")
        self.assertEqual(hit["type"], "短文填空")
        self.assertIsNone(hit["subtype"])


class TestMaterialScope(unittest.TestCase):
    def test_sync_practice_detected(self):
        sample = "Unit 1 Happy Holiday\n第一课时 Section A (1a-1d)\n基础 Ⅰ.单项填空\n提升 Ⅰ.补全对话"
        self.assertTrue(sp.looks_like_sync_practice(sample))

    def test_exam_paper_not_detected(self):
        sample = "2026年安徽省合肥市中考模拟试题\n一、听力\n二、单项填空"
        self.assertFalse(sp.looks_like_sync_practice(sample))

    def test_prompt_block_is_compact_and_scoped(self):
        block = sp.prompt_block()
        self.assertIn("同步练习", block)
        self.assertIn("group", block)
        self.assertIn("单元话题写作", block)
        self.assertLess(len(block), 4000)

    def test_module_cli_self_check_passes(self):
        self.assertEqual(sp.main([]), 0)


class TestAnswerAppendixBoundary(unittest.TestCase):
    def test_intermediate_answers_never_remove_following_lesson(self):
        from . import extract
        text = ('第一课时 Section A\nⅠ.单项填空\n1.Who?\nA.a B.b C.c D.d\n'
                '参考答案\n1.A\n2.B\n第二课时 Section B\nⅡ.根据汉语意思完成句子\n1.He is ____.')
        self.assertIsNone(sp.answer_key_start(text))
        structure, main, _ = extract.infer_structure(text)
        self.assertIn('第二课时', main)
        self.assertTrue(all(row['source_end_line'] >= row['source_line'] for row in structure['sections']))

    def test_tail_answers_do_not_create_heading_candidates(self):
        from . import extract
        text = ('第一课时 Section A\nⅠ.单项填空\n1.Who?\nA.a B.b C.c D.d\n'
                '参考答案\nⅠ.1.A\nⅡ.1.B')
        self.assertEqual(sp.answer_key_start(text), 5)
        self.assertEqual(len(sp.heading_candidates(text)), 1)
        structure, main, appendix = extract.infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [1])
        self.assertNotIn('参考答案', main)
        self.assertIn('Ⅱ.1.B', appendix)

    def test_non_lesson_numbers_inside_a_stem_are_not_new_items(self):
        from . import extract
        text = ('2026年期末测试卷\nⅠ.单项填空(共1小题)\n'
                '( )1.Which word completes ____: 2.Planning or 3.Action?\nA.a B.b C.c D.d')
        structure, _, _ = extract.infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [1])
        self.assertFalse(any(row['code'] == 'QUESTION_COUNT_MISMATCH' for row in structure['unresolved']))

    def test_declared_image_section_is_not_given_guessed_printed_numbers(self):
        from . import extract
        text = ('第一课时 Section A\nⅠ.单项填空\n1.Who?\nA.a B.b C.c D.d\n'
                'Ⅱ.根据汉语意思完成句子(共1小题)\n[IMG image2.jpeg]')
        structure, _, _ = extract.infer_structure(text)
        section = structure['sections'][-1]
        self.assertTrue(section.get('image_only'))
        self.assertEqual(section['questions'], [])
        self.assertFalse(any(row['code'] in {'QUESTION_COUNT_MISMATCH', 'QUESTION_NUMBER_INFERRED'}
                             and row['line'] == section['source_line'] for row in structure['unresolved']))


class TestLessonHeadingsMatchesRulesDocument(unittest.TestCase):
    """课时同步的真实大题标题 → 题型映射表（与 app/docs/sync-lesson-types.md 第三节同源）。

    这些表述来自 2026-11-11 真实课时批次（七上 Starter Unit 1 第一课时）逐条核过的原卷标题。
    改规则必须同时改文档表格与这张表，避免「文档写一套、代码判另一套」。
    """

    LESSON_HEADINGS = [
        ("Ⅰ.按照字母表顺序,写出空白处所缺字母", "语音题", None, "group"),
        ("Ⅱ.按照名字的字母顺序为下列人物排序,并将句子补充完整", "语音题", None, "group"),
        ("Ⅲ.根据首字母及汉语提示补全单词", "单词拼写", "汉语及首字母", "standalone"),
        ("Ⅳ.根据汉语意思完成句子,每空一词", "完成句子", "汉语提示", "standalone"),
        ("Ⅴ.单项填空", "单项填空", "四个选项", "standalone"),
        ("Ⅰ.判断下列各组字母的大小写是否一致,一致的写T,不一致的写F", "语音题", None, "group"),
        ("Ⅱ.找出下列句子中书写错误的单词并改正", "改错", "句子改错", "standalone"),
        ("Ⅲ.补全对话", "补全对话", None, "group"),
        ("Ⅳ.阅读理解", "阅读理解", None, "group"),
    ]

    def test_every_lesson_heading_maps_to_catalog_type(self):
        for title, type_name, subtype, kind in self.LESSON_HEADINGS:
            with self.subTest(title=title):
                hit = sp.classify(title)
                self.assertIsNotNone(hit, title)
                self.assertEqual(hit["type"], type_name)
                self.assertEqual(hit["subtype"], subtype)
                self.assertEqual(hit["kind"], kind)

    def test_type_is_never_the_heading_itself(self):
        # 审核页展示的是目录题型，不能把大题标题（题干）当题型。
        for title, type_name, _subtype, _kind in self.LESSON_HEADINGS:
            with self.subTest(title=title):
                self.assertNotEqual(sp.classify(title)["type"], title)

    def test_rules_document_covers_every_lesson_heading(self):
        doc = (ROOT / "docs" / "sync-lesson-types.md").read_text(encoding="utf-8")
        self.assertIn("同步课时题型匹配规则", doc)
        self.assertIn("审核页必须直接展示题型", doc)
        for title, _type_name, _subtype, _kind in self.LESSON_HEADINGS:
            with self.subTest(title=title):
                self.assertIn(title, doc)

    def test_prompt_block_states_lesson_numbering_and_type_rule(self):
        block = sp.prompt_block()
        self.assertIn("从 1 重新开始", block)
        self.assertIn("不能把大题标题、题干或答题指令抄进题型字段", block)
        self.assertIn("参考答案", block)



if __name__ == "__main__":
    unittest.main()
