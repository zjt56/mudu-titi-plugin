"""硬规则 7 的代码级约束：主流程图节点 ≤10，且覆盖全部阶段契约。

规则来自根 `AGENTS.md` 硬规则 7：「流程图 ≤10 节点，与真实阶段顺序同步；须能
区分听力/音频、知识点等分项」。

`lib/client.js::compactFlowStages` 把 18 个契约阶段归并成固定几组（当前 9 组），
是唯一决定主图节点数的代码。这里直接读源码校验，避免以后新增阶段时把主图撑爆
或漏掉某个阶段而没有任何回归拦住。
"""
import re
import unittest
from pathlib import Path

from workflow import contracts

CLIENT = Path(__file__).resolve().parents[2] / "lib" / "client.js"


def _groups() -> list[str]:
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"function compactFlowStages\(sourceItems\)\s*\{(.*?)\n    \}", source, re.S)
    if not match:
        raise AssertionError("client.js 缺少 compactFlowStages，无法校验流程图节点数")
    return re.findall(r"\{ id: '([a-z_]+)'", match.group(1))


def _member_ids() -> set[str]:
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"function compactFlowStages\(sourceItems\)\s*\{(.*?)\n    \}", source, re.S)
    body = match.group(1)
    ids: set[str] = set()
    for group in re.finditer(r"ids: \[(.*?)\]", body, re.S):
        ids.update(re.findall(r"'([a-z_]+)'", group.group(1)))
    return ids


class FlowChartNodeBudgetTest(unittest.TestCase):
    def test_flow_chart_has_at_most_ten_nodes(self):
        groups = _groups()
        self.assertTrue(groups, "主流程图必须至少有一个节点组")
        self.assertLessEqual(len(groups), 10,
                             f"主流程图 {len(groups)} 个节点，超过硬规则 7 的 10 节点上限：{groups}")

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
