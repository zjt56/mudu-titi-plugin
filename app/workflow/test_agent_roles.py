"""内置子 Agent 角色必须与阶段契约一一对应，且真正进入提示词。"""
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from workflow import agent_roles, agent_tasks, contracts


class AgentRoleContractTest(unittest.TestCase):
    def test_every_agent_stage_ships_a_matching_role(self):
        stages = list(agent_tasks.ARTIFACTS)
        self.assertEqual(sorted(stages), sorted(p.stem for p in agent_roles.ROLES_DIR.glob("*.md")),
                         "每个 agent 阶段必须有且只有一个角色文件")
        for stage in stages:
            role = agent_roles.load(stage, agent_tasks.ARTIFACTS[stage])
            self.assertEqual(role["stage"], stage)
            self.assertEqual(role["artifact"], agent_tasks.ARTIFACTS[stage])
            self.assertTrue(role["title"].strip())
            self.assertGreater(len(role["body"]), 80, stage)

    def test_role_mismatch_is_an_error_not_a_silent_fallback(self):
        with self.assertRaises(agent_roles.RoleError):
            agent_roles.load("answers", "06_batch/batch.json")
        with self.assertRaises(agent_roles.RoleError):
            agent_roles.load("not_a_stage")

    def test_role_section_carries_identity_boundaries_and_self_check(self):
        for stage in agent_tasks.ARTIFACTS:
            text = agent_roles.section(stage, agent_tasks.ARTIFACTS[stage])
            self.assertIn("## 职责", text)
            self.assertIn("## 权限边界", text)
            self.assertIn("脑子检查", text)
            self.assertIn(agent_tasks.ARTIFACTS[stage], text)

    def test_review_role_states_the_duty_of_each_round(self):
        labels = agent_roles.section("review", agent_tasks.ARTIFACTS["review"], "labels")
        sample = agent_roles.section("review", agent_tasks.ARTIFACTS["review"], "sample")
        self.assertIn("一审", labels)
        self.assertIn("二审", sample)
        self.assertNotEqual(labels, sample)

    def test_review_prompts_preserve_differences_without_content_regeneration(self):
        for round_id in ('labels', 'sample'):
            prompt = agent_roles.section('review', agent_tasks.ARTIFACTS['review'], round_id)
            self.assertIn('审核差异是产出，不是执行故障', prompt)
            self.assertIn('不得要求上游知识点重判', prompt)
            self.assertIn('JSON', prompt)
        unit = {'unit_id': 'q-1', 'numbers': [1], 'unit_kind': 'standalone'}
        task = agent_tasks._build_task_template('review', 200000, {}, {}, [], [unit])
        self.assertIn('request upstream regeneration', task['prompt'])
        self.assertIn('审核差异是产出，不是执行故障', task['prompt'])
        self.assertIn('request upstream regeneration', agent_tasks.RULES['review'])
        self.assertIn('not execution failures', agent_tasks.RULES['review'])

    def test_actual_prompt_fields_and_hierarchy_follow_authoritative_contracts(self):
        from workbench import taxonomy
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        for stage in ("answers", "knowledge"):
            task = agent_tasks._build_task_template(stage, 200000, {}, {}, [], [unit])
            role = task["prompt"].split("[子 Agent]", 1)[1].split("Complete valid JSON", 1)[0]
            self.assertNotIn("{{", role)
            for heading in ("## 职责", "## 权限边界", "脑子检查", "## 出问题怎么办"):
                self.assertIn(heading, role)
            if stage == "answers":
                for key in agent_tasks.ANSWERS_ALLOWED:
                    self.assertIn(f"`{key}`", role)
                self.assertIn("Word", role)
                self.assertIn("PDF/OCR", role)
                self.assertIn("逐字保留", role)
            else:
                for kind in taxonomy.WHOLE_UNIT_TYPES:
                    self.assertIn(kind, role)
                self.assertIn("子题 `knowledge=[]`", role)
                self.assertNotIn("每道子题都要有一个", role)
                self.assertIn("教材匹配暂停", role)
        # 常量改变即进入真实 prompt，角色 Markdown 无第二份题型列表。
        with patch.object(taxonomy, "WHOLE_UNIT_TYPES", frozenset({"测试整组题型"})):
            prompt = agent_tasks._build_task_template("knowledge", 200000, {}, {}, [], [unit])["prompt"]
            self.assertIn("整组题型（测试整组题型）", prompt)
        with patch.dict(agent_tasks.EXAMPLE_REQUIRED, answers=("answers", "evidence")):
            role = agent_roles.section("answers")
            self.assertIn("产物必填字段：`answers`、`evidence`；", role)

    def test_task_build_rejects_missing_duplicate_or_unknown_role_slots(self):
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        for stage, token in (("answers", "{{answers_fields}}"), ("knowledge", "{{knowledge_hierarchy}}")):
            original = (agent_roles.ROLES_DIR / f"{stage}.md").read_text(encoding="utf-8")
            for replacement in ("旧字段/层级说明", token + token, token + "{{unknown_contract}}"):
                with self.subTest(stage=stage, replacement=replacement), tempfile.TemporaryDirectory() as tmp:
                    (Path(tmp) / f"{stage}.md").write_text(original.replace(token, replacement), encoding="utf-8")
                    with patch.object(agent_roles, "ROLES_DIR", Path(tmp)):
                        with self.assertRaises(agent_roles.RoleError):
                            agent_tasks._build_task_template(stage, 200000, {}, {}, [], [unit])

    def test_header_is_machine_readable_for_routing(self):
        for stage in agent_tasks.ARTIFACTS:
            self.assertEqual(agent_roles.header(stage), f"Agent: {stage}\n")

    def test_catalog_lists_all_roles(self):
        ids = [role["id"] for role in agent_roles.catalog()]
        self.assertEqual(ids, sorted(agent_tasks.ARTIFACTS))

    def test_task_prompt_contains_role_header_and_section(self):
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        task = agent_tasks._build_task_template("types", 200000, {}, {}, [], [unit])
        prompt = task["prompt"]
        self.assertIn("Agent: types", prompt)
        self.assertIn("[子 Agent] 题型判定员", prompt)
        self.assertIn("Agent: types\n", prompt.split("BEGIN SUPPLIED CONTENT")[0])

    def test_content_source_priority_is_stated_in_prompts(self):
        # 用户规则：Word > PDF/OCR > 空占位，提示词必须显式声明。
        from workflow import agent_tasks, content_rules
        exp = content_rules.RULES
        self.assertIn('Word', exp)
        self.assertIn('PDF', exp)
        self.assertIn('「无」', exp)
        self.assertIn('PDF/OCR', agent_tasks.RULES['answers'])
        self.assertIn('书面表达', agent_tasks.RULES['answers'])
        self.assertIn('书面表达', content_rules.RULES)
        for stage in ('explanations', 'answers'):
            role = agent_roles.load(stage, agent_tasks.ARTIFACTS[stage])
            self.assertIn('Word', role['body'], stage)
            self.assertIn('PDF', role['body'], stage)

    def test_role_text_never_changes_the_json_contract(self):
        unit = {"unit_id": "q-1", "numbers": [1], "unit_kind": "standalone"}
        for stage in agent_tasks.ARTIFACTS:
            task = agent_tasks._build_task_template(stage, 200000, {}, {}, [], [unit])
            head = task["prompt"].split("BEGIN SUPPLIED CONTENT")[0]
            self.assertIn("exactly four sibling keys: status, artifacts, notes, findings", head)
            self.assertIn("Stage: " + stage, head)


if __name__ == "__main__":
    unittest.main()

    def test_every_role_states_that_a_single_failure_must_not_stop_the_flow(self):
        # 用户硬规则：任一子 Agent 出问题都必须自己再看一遍、修一下，不能把流程停下。
        for role in agent_roles.catalog():
            self.assertIn("不得", role["body"], role["id"])
            self.assertTrue(
                any(word in role["body"] for word in ("重跑", "重判", "恢复", "重试", "重查", "不得")),
                role["id"])
        for stage in agent_tasks.ARTIFACTS:
            text = agent_roles.section(stage, agent_tasks.ARTIFACTS[stage])
            self.assertIn("## 出问题怎么办", text, stage)

    def test_role_section_tells_the_worker_not_to_report_a_dead_end(self):
        text = agent_roles.section("assemble", agent_tasks.ARTIFACTS["assemble"])
        self.assertIn("重看一遍证据", text)
        self.assertIn("重跑", text)
        self.assertIn("继续推进", text)

    def test_preassemble_and_types_roles_warn_about_legitimate_blank_or_image_shapes(self):
        # 代码口径（question_content.legitimate_source_shape）必须与提示词同步：
        # 前置组装/题型提示词都要提醒“原卷合法空/图形态不算内容缺失”。
        from workflow.question_content import LEGITIMATE_SHAPES
        self.assertEqual(set(LEGITIMATE_SHAPES), {"image_only", "blank_answer_grid", "unnumbered_no_image"})
        for stage in ("preassemble", "types"):
            text = agent_roles.section(stage, agent_tasks.ARTIFACTS[stage])
            self.assertIn("图片", text, stage)
            self.assertIn("空白作答网格", text, stage)
            self.assertIn("不算内容缺失", text, stage)
            self.assertIn("contentComplete", text, stage)

    def test_preassemble_instruction_warns_about_capability_shapes(self):
        instruction = agent_tasks.RULES["types"]
        self.assertIn("contentComplete", instruction)
        # 内置英文指令也必须点出“合法空/图形态不算缺失”，避免与提示词漂移。
        self.assertIn("pure image", instruction)
        self.assertIn("blank student answer grids", instruction)
        self.assertIn("NOT missing content", instruction)
