"""用户确认的十二类主图必须完整覆盖18个后台阶段，且保持独立分支可见。"""
import re
import unittest
from pathlib import Path

from workflow import contracts

CLIENT = Path(__file__).resolve().parents[2] / "lib" / "client.js"


def _groups() -> list[str]:
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"function compactFlowStages\(sourceItems(?:,[^)]*)?\)\s*\{(.*?)\n    \}", source, re.S)
    if not match:
        raise AssertionError("client.js 缺少 compactFlowStages，无法校验流程图节点数")
    return re.findall(r"\{ id: '([a-z_]+)'", match.group(1))


def _member_ids() -> set[str]:
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"function compactFlowStages\(sourceItems(?:,[^)]*)?\)\s*\{(.*?)\n    \}", source, re.S)
    body = match.group(1)
    ids: set[str] = set()
    for group in re.finditer(r"ids: \[(.*?)\]", body, re.S):
        ids.update(re.findall(r"'([a-z_]+)'", group.group(1)))
    return ids


class FlowChartNodeBudgetTest(unittest.TestCase):
    def test_flow_chart_has_the_twelve_approved_categories(self):
        self.assertEqual(_groups(), [
            'extract', 'structure_gate', 'preassemble', 'audio', 'answers',
            'explanations', 'knowledge', 'assemble', 'validate', 'review',
            'format_gate', 'upload',
        ])

    def test_flow_chart_covers_every_contract_stage(self):
        covered = _member_ids()
        missing = sorted({stage.id for stage in contracts.STAGES} - covered)
        self.assertEqual(missing, [], f"主流程图漏掉阶段（会显示不出来）：{missing}")

    def test_listening_and_knowledge_stay_separable(self):
        # 硬规则 7 要求能区分听力/音频与知识点等分项。
        groups = _groups()
        self.assertIn("audio", groups)
        self.assertIn("knowledge", groups)


if __name__ == "__main__":
    unittest.main()
