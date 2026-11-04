"""本地 Agent 的确定性输入准备；不调用模型，不改变阶段状态。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import extract, manifest as mf


def prepare_answers(batch_dir: Path) -> dict:
    """明确上传答案卷时复用复合提取，供 answers Agent 读取完整原文。"""
    batch = Path(batch_dir)
    manifest = mf.load(batch)
    source_value = manifest.get('inputs', {}).get('answers', {}).get('path')
    if not source_value:
        return {'prepared': False, 'reason': '没有单独答案卷，使用已提取附录（如有）'}
    source = Path(source_value).expanduser().resolve(strict=True)
    if source.suffix.lower() == '.txt':
        return {'prepared': False, 'reason': '答案卷为文本，直接读取'}
    output = batch / '01_source_ans'
    output.mkdir(parents=True, exist_ok=True)
    provenance = output / 'preparation.json'
    text_path = output / '正文.txt'
    digest = mf.sha256_file(source)
    if provenance.is_file() and text_path.is_file():
        try:
            saved = json.loads(provenance.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            saved = {}
        if saved.get('source_sha256') == digest and saved.get('text_sha256') == mf.sha256_file(text_path):
            return {'prepared': True, 'cached': True}
    attempt = Path(tempfile.mkdtemp(prefix='source-', dir=output))
    report = extract.extract_source(source, attempt)
    # 答案卷本身可能重复大题标题。不得使用被分离附录后的正文漏掉后半答案。
    raw = attempt / '原始正文.txt'
    if text_path.is_file():
        shutil.copy2(text_path, text_path.with_name('正文.txt.bak'))
    shutil.copy2(raw, text_path)
    saved = {'source_sha256': digest, 'text_sha256': mf.sha256_file(text_path),
             'source': str(source), 'evidence_dir': str(attempt.relative_to(batch)),
             'extraction_report': report}
    if provenance.is_file():
        shutil.copy2(provenance, provenance.with_name('preparation.json.bak'))
    provenance.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'prepared': True, 'cached': False, 'evidence_dir': saved['evidence_dir']}
