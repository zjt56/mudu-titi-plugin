# -*- coding: utf-8 -*-
"""按题型过滤知识点目录树与搜索接口的单元测试与 HTTP 回归测试。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import quote
from urllib.request import urlopen

from workbench import server, taxonomy


def _walk_nodes(items):
    for node in items:
        yield node
        yield from _walk_nodes(node.get("children") or [])


class TestKnowledgeTreeScoping(unittest.TestCase):
    def test_unscoped_tree_backward_compatible(self):
        tree = taxonomy.knowledge_tree()
        self.assertTrue(tree["complete"])
        self.assertFalse(tree["scoped"])
        self.assertEqual(tree["type"], "")
        self.assertGreater(tree["total"], 0)
        self.assertEqual(tree["selectable_total"], tree["leaf_total"])
        all_nodes = list(_walk_nodes(tree["items"]))
        self.assertEqual(len(all_nodes), tree["total"])
        self.assertEqual(sum(1 for n in all_nodes if n.get("selectable")), tree["selectable_total"])
        # 含斜线的目录名不被拆成伪层级
        paths = {n["path"] for n in all_nodes}
        self.assertTrue(any("固定搭配/句型" in p or "some/any" in p for p in paths))

    def test_unknown_type_falls_back_to_unscoped(self):
        tree = taxonomy.knowledge_tree("不存在的题型")
        self.assertTrue(tree["complete"])
        self.assertFalse(tree["scoped"])
        self.assertEqual(tree["type"], "")

    def test_all_27_types_have_valid_scoped_trees(self):
        all_types = taxonomy.types()
        self.assertEqual(len(all_types), 27)
        for qtype in all_types:
            with self.subTest(qtype=qtype):
                tree = taxonomy.knowledge_tree(qtype)
                self.assertTrue(tree["scoped"])
                self.assertFalse(tree["complete"])
                self.assertEqual(tree["type"], qtype)
                self.assertGreater(tree["selectable_total"], 0)
                nodes = list(_walk_nodes(tree["items"]))
                self.assertEqual(len(nodes), tree["total"])
                selectable = [n for n in nodes if n.get("selectable")]
                self.assertEqual(len(selectable), tree["selectable_total"])
                for node in nodes:
                    if not node.get("selectable"):
                        self.assertTrue(node.get("children"), f"{qtype} 不应存在空分支 {node['path']}")
                for node in selectable:
                    path = node["path"]
                    problems = taxonomy.check_labels({"type": qtype, "knowledge": [path]})
                    self.assertEqual(problems, [], f"{qtype} 可选节点 {path} 未通过校验: {problems}")
                    self.assertEqual(taxonomy.require_knowledge([path], qtype), [path])

    def test_cloze_only_shows_genre_and_theme(self):
        tree = taxonomy.knowledge_tree("完形填空")
        roots = [n["name"] for n in tree["items"]]
        self.assertEqual(set(roots), {"语篇体裁", "主题"})
        paths = [n["path"] for n in _walk_nodes(tree["items"])]
        for forbidden in ("词汇", "语法", "语音", "设题角度", "语用"):
            self.assertFalse(any(p == forbidden or p.startswith(forbidden + "/") for p in paths))

    def test_reading_scopes_question_angle_to_reading(self):
        tree = taxonomy.knowledge_tree("阅读理解")
        roots = {n["name"]: n for n in tree["items"]}
        self.assertEqual(set(roots.keys()), {"设题角度", "语篇体裁", "主题"})
        angle_children = [c["name"] for c in roots["设题角度"]["children"]]
        self.assertEqual(angle_children, ["阅读"])

    def test_listening_keyword_exact_path(self):
        tree = taxonomy.knowledge_tree("听力关键词语选择")
        self.assertEqual(tree["selectable_total"], 1)
        selectable = [n for n in _walk_nodes(tree["items"]) if n.get("selectable")]
        self.assertEqual(len(selectable), 1)
        self.assertEqual(selectable[0]["path"], "语音/基本读音")
        self.assertEqual(selectable[0]["children"], [])
        self.assertTrue(selectable[0]["leaf"])

    def test_spelling_excludes_vocab_discrimination(self):
        tree = taxonomy.knowledge_tree("单词拼写")
        paths = [n["path"] for n in _walk_nodes(tree["items"])]
        self.assertFalse(any(p.startswith("词汇/词汇辨析") for p in paths))
        self.assertTrue(any(p.startswith("词汇/拼写与词形") for p in paths))

    def test_disallowed_vocab_branches_excluded(self):
        for qtype in ("完成句子", "短文填空", "选词填空", "教材语篇填空"):
            with self.subTest(qtype=qtype):
                tree = taxonomy.knowledge_tree(qtype)
                paths = [n["path"] for n in _walk_nodes(tree["items"])]
                self.assertFalse(any(p.startswith("词汇/考察词汇") for p in paths))
                self.assertFalse(any(p.startswith("词汇/答案考点") for p in paths))

    def test_scoped_knowledge_search(self):
        # 完形填空搜“词汇”或“名词”应无结果
        self.assertEqual(taxonomy.knowledge_search("词汇", 40, main_type="完形填空"), [])
        # 完形填空搜“记叙文”应命中语篇体裁
        hits = taxonomy.knowledge_search("记叙文", 40, main_type="完形填空")
        self.assertTrue(any("记叙文" in h["path"] for h in hits))
        # 听力关键词语选择精确路径可搜出
        exact_hits = taxonomy.knowledge_search("基本读音", 40, main_type="听力关键词语选择")
        self.assertEqual([h["path"] for h in exact_hits], ["语音/基本读音"])

    def test_http_endpoints(self):
        import io

        def get_json(path):
            handler = server.Handler.__new__(server.Handler)
            handler.path = path
            handler.headers = {}
            buf = io.BytesIO()
            status_box = []
            handler.wfile = buf
            handler.send_response = lambda code, msg=None: status_box.append(code)
            handler.send_header = lambda k, v: None
            handler.end_headers = lambda: None
            handler.do_GET()
            self.assertEqual(status_box, [200])
            return json.loads(buf.getvalue().decode("utf-8"))

        full = get_json("/api/knowledge/tree")
        self.assertTrue(full["complete"])
        self.assertFalse(full["scoped"])

        cloze = get_json("/api/knowledge/tree?type=" + quote("完形填空"))
        self.assertTrue(cloze["scoped"])
        self.assertEqual(cloze["type"], "完形填空")
        self.assertEqual({n["name"] for n in cloze["items"]}, {"语篇体裁", "主题"})

        exact = get_json("/api/knowledge/tree?type=" + quote("听力关键词语选择"))
        self.assertEqual(exact["selectable_total"], 1)

        search_empty = get_json("/api/knowledge?q=" + quote("词汇") + "&type=" + quote("完形填空"))
        self.assertEqual(search_empty["items"], [])

        search_exact = get_json("/api/knowledge?q=" + quote("基本读音") + "&type=" + quote("听力关键词语选择"))
        self.assertEqual([i["path"] for i in search_exact["items"]], ["语音/基本读音"])


if __name__ == "__main__":
    unittest.main()
