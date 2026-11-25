'''共享写文件/哈希原语：各模块历史私有实现收拢到 workflow_primitives 后，写出字节与哈希保持不变。'''
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflow import ai_supplement, ingest, structure_gate, workflow_primitives


class SharedWriterCompatTest(unittest.TestCase):
    PAYLOAD = {'题干': '中文 “引号” ______', 'numbers': [1, 2], 'nested': {'b': None, 'a': True}}

    def test_module_json_writers_keep_historical_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, writer, indent in (('structure_gate', structure_gate._write_json, 1), ('ingest', ingest._write_json, 1), ('primitives', workflow_primitives.write_json, 2)):
                target = Path(tmp) / name / 'nested' / 'out.json'
                writer(target, self.PAYLOAD)
                expected = (json.dumps(self.PAYLOAD, ensure_ascii=False, indent=indent) + '\n').encode('utf-8')
                self.assertEqual(target.read_bytes(), expected, name)

    def test_atomic_text_writers_replace_content_and_leave_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, writer in (('ai_supplement', ai_supplement._atomic_write_text), ('primitives', workflow_primitives.write_text_atomic)):
                target = Path(tmp) / name / 'x.txt'
                writer(target, '旧')
                writer(target, '新内容\n')
                self.assertEqual(target.read_text(encoding='utf-8'), '新内容\n', name)
                self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ['x.txt'], name)


if __name__ == '__main__':
    unittest.main()
