"""准备、冻结并提交上传暂存；从不确认上传或发布题目。"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os

from . import manifest as mf
from . import upload_client, upload_payload
from .local_agent import _agent_lock

ROOT = Path(__file__).resolve().parents[1]
FOLDER = '07_upload'
MAX_BYTES = 2_000_000


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(batch, name):
    from .agent_tasks import _safe_path
    return _safe_path(Path(batch).resolve(), f'{FOLDER}/{name}')


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.upload-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _read(path):
    # 通用协议的严格 JSON 加载器拒绝重复键和非有限数值。
    from .agent_protocol import _load
    return _load(Path(path).read_bytes())


def _source(batch):
    from .agent_tasks import _safe_path
    path = _safe_path(Path(batch).resolve(), '06_batch/batch_meta.json')
    if not path.is_file() or path.is_symlink():
        raise ValueError('缺少正式批次 06_batch/batch_meta.json，或该文件是软链接')
    raw = path.read_bytes()
    return path, raw, _read(path)


def _config(config=None):
    return upload_client.load_config() if config is None else config


def _mapping(source, catalog, options=None):
    return upload_payload.build_payload(source, catalog, **(options or {}))


# 本地装配产物的题目 ID 只有单卷内唯一（q-1、group-16-17）。题库的题目 ID 是
# 全局主键，多卷或重传会撞上 question_id_exists；上传时为这些 ID 加批次前缀。
_LOCAL_WIRE_ID = re.compile(r"^(?:q-\d+|group-\d+(?:-\d+)*)$")


def _auto_id_prefix(source, batch_id):
    """Derive a stable prefix when any local q-N/group-N id is present.

    The payload mapper applies it only to local ids; explicit ids in a mixed
    batch (AH-2014-001, UUIDs, …) remain unchanged.
    """
    items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(items, list) or not items:
        return None
    ids = [item.get("id") for item in items if isinstance(item, dict)]
    if not any(
            isinstance(value, str) and _LOCAL_WIRE_ID.fullmatch(value) for value in ids):
        return None
    years = set()
    for item in items:
        value = item.get("year")
        if isinstance(value, bool):
            continue
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if 1900 <= year <= 2100:
            years.add(year)
    year = next(iter(years)) if len(years) == 1 else None
    digest = hashlib.sha256(str(batch_id).encode("utf-8")).hexdigest()[:10]
    return f"{year}-{digest}" if year else digest


def _media_map(batch):
    """Deployed-media map (local filename -> /source-media/... URL), if present."""
    import json as _json
    for relative in ("06_batch/media-map.json", "07_upload/media-map.json"):
        path = Path(batch) / relative
        if not path.is_file():
            continue
        try:
            data = _json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        result = {}
        for key, value in data.items():
            if isinstance(value, str) and value.startswith("/source-media/"):
                result[key] = value
            elif isinstance(value, dict):
                url = value.get("url")
                if isinstance(url, str) and url.startswith("/source-media/"):
                    result[str(value.get("name") or key)] = url
        if result:
            break
    else:
        result = {}
    # 未提供映射文件时，按已约定的部署目录生成候选映射；
    # 是否真的可访问仍由提交前的 HEAD 校验决定（不会因为生成了 URL 就放行）。
    from urllib.parse import quote
    base = Path(batch).name
    generated = {}
    for relative in ("01_source/media", "01_source_ans/media", "02_audio"):
        folder = Path(batch) / relative
        if not folder.is_dir():
            continue
        for item in sorted(folder.iterdir()):
            if not item.is_file():
                continue
            if item.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".mp3", ".wav", ".m4a", ".ogg"}:
                continue
            generated[item.name] = f"/source-media/{quote(base)}/{quote(item.name)}"
    for name, url in generated.items():
        result.setdefault(name, url)
    return result or None


def _media(payload):
    urls = {}
    def visit(value):
        if isinstance(value, dict):
            if 'attachments' in value:
                for attachment in value['attachments']:
                    url, kind = attachment['url'], attachment['kind']
                    if url in urls and urls[url] != kind:
                        raise ValueError('同一媒体路径被声明为不同类型')
                    urls[url] = kind
            for key, child in value.items():
                if key != 'attachments': visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(payload)
    return urls


def local_preflight(batch_dir) -> dict:
    """离线生成上传前摘要，不访问远端、不冻结、不改变阶段状态。

    目的是把最终批次的本地硬错误提前暴露在 validate 之后，而不是等用户点击上传。
    远端目录、媒体 HEAD 和冻结一致性仍由 prepare/verify 负责。
    """
    batch = Path(batch_dir).resolve()
    report = {"passed": True, "network_checked": False, "errors": [], "warnings": []}
    final = batch / "06_batch" / "batch_meta.json"
    if not final.is_file() or final.is_symlink():
        report["passed"] = False
        report["errors"].append({"code": "FINAL_BATCH_MISSING", "message": "缺少正式批次 batch_meta.json"})
    else:
        try:
            from .label_audit import inspect_file
            labels = inspect_file(final)
            report["label_audit"] = labels
            if not labels.get("passed"):
                report["passed"] = False
                report["errors"].extend(labels.get("errors", []))
            from .upload_content import inspect_content
            content = inspect_content(batch, json.loads(final.read_text(encoding="utf-8-sig")), strict=True)
            report["content"] = content
            if not content.get("passed"):
                report["passed"] = False
                report["errors"].extend(content.get("errors", []))
            raw = final.read_bytes()
            report["payload_bytes_before_mapping"] = len(raw)
            if len(raw) > MAX_BYTES:
                report["passed"] = False
                report["errors"].append({"code": "PAYLOAD_TOO_LARGE", "message": "正式批次超过 2 MB，上传前需拆分"})
            report["unresolved_media"] = bool(report.get("content", {}).get("errors"))
        except (OSError, ValueError, TypeError) as exc:
            report["passed"] = False
            report["errors"].append({"code": "LOCAL_PREFLIGHT_ERROR", "message": str(exc)})
    target = batch / FOLDER / "local-preflight.json"
    _write(target, _bytes(report))
    return report


def prepare(batch_dir, *, catalog=None, config=None, check_media=True, **options):
    """读取实时目录后冻结确定性映射。离线目录预检不等同已验证远端媒体。"""
    batch = Path(batch_dir).resolve()
    settings = _config(config)
    with _agent_lock(10):
        source_path, source_raw, source = _source(batch)
        client = upload_client.ApiClient(settings)
        try:
            snapshot = client.fetch_catalog() if catalog is None else catalog
        except upload_client.APIError as exc:
            _write(_path(batch, 'preflight.json'), _bytes({'passed': False,
                'errors': [{'code': exc.code, 'message': str(exc)}], 'details': exc.details}))
            raise
        manifest = mf.load(batch)
        options = {key: value for key, value in options.items() if value is not None}
        if "media_map" not in options:
            media_map = _media_map(batch)
            if media_map:
                options["media_map"] = media_map
        if not source.get('batch_id') and not options.get('batch_id'):
            options['batch_id'] = manifest['workflow_id']
        if 'id_prefix' not in options:
            prefix = _auto_id_prefix(source, options.get('batch_id') or manifest.get('workflow_id'))
            if prefix:
                options['id_prefix'] = prefix
        # 标题可从已绑定当前正文的元信息确定性继承，不使用目录名或上传年份猜测。
        from .agent_tasks import _safe_path
        metadata_path = _safe_path(batch, '01_source/metadata.json')
        if not (source.get('name') or source.get('title') or options.get('name')) and metadata_path.is_file():
            metadata = _read(metadata_path)
            title = metadata.get('paper_title')
            if (isinstance(title, str) and title.strip() and metadata.get('source_sha256')
                    and metadata['source_sha256'] == mf.sha256_file(batch / '01_source' / '正文.txt')):
                options['name'] = title
        report = _mapping(source, snapshot, options)
        from .upload_content import inspect_content
        content = inspect_content(batch, source, strict=True)
        report['content_source'] = content
        report['errors'].extend(content['errors'])
        report['passed'] = report['passed'] and content['passed']
        report['source_sha256'] = _sha(source_raw)
        report['api_base'] = settings['base_url']
        report['catalog_origin'] = 'live_api' if catalog is None else 'supplied_snapshot'
        _write(_path(batch, 'preflight.json'), _bytes({k: v for k, v in report.items() if k != 'payload'}))
        if not report['passed']:
            return {k: v for k, v in report.items() if k != 'payload'}
        payload = report['payload']
        raw = _bytes(payload)
        if len(raw) > MAX_BYTES:
            report['passed'] = False
            report['errors'].append({'code': 'PAYLOAD_TOO_LARGE', 'message': '请求超过 2 MB，需拆分批次后重新审核，禁止自动分段提交'})
        urls = _media(payload)
        media_reports = []
        if urls and not check_media:
            report['passed'] = False
            report['errors'].append({'code': 'MEDIA_UNVERIFIED', 'message': '含媒体但尚未经目标站点核验，不生成可批准冻结文件'})
        if report['passed']:
            for url, kind in sorted(urls.items()):
                try:
                    media_reports.append(client.check_media(url, kind=kind))
                except upload_client.APIError as exc:
                    report['passed'] = False
                    report['errors'].append({'code': 'MEDIA_UNAVAILABLE', 'message': str(exc), 'url': url})
        if not report['passed']:
            _write(_path(batch, 'preflight.json'), _bytes({k: v for k, v in report.items() if k != 'payload'}))
            return {k: v for k, v in report.items() if k != 'payload'}
        # 冻结件按内容摘要保存，不覆盖已审核的另一版本。
        digest = _sha(raw)
        stem = f'frozen/{digest}'
        payload_path = _path(batch, stem + '/payload.json')
        catalog_path = _path(batch, stem + '/catalog.json')
        catalog_raw = _bytes(snapshot)
        plan = {'schema_version': 1, 'state': 'prepared', 'payload_sha256': digest,
                'source_sha256': _sha(source_raw), 'catalog_sha256': _sha(catalog_raw),
                'payload_file': stem + '/payload.json', 'catalog_file': stem + '/catalog.json',
                'api_base': settings['base_url'], 'mapping_options': options, 'bytes': len(raw),
                'summary': report.get('summary', {}), 'warnings': report.get('warnings', []),
                'media': media_reports, 'content_source_sha256': content['source_sha256'],
                'catalog_origin': report['catalog_origin']}
        if payload_path.is_file() and payload_path.read_bytes() != raw:
            raise ValueError('既有内容寻址冻结文件发生变化')
        _write(payload_path, raw)
        _write(catalog_path, catalog_raw)
        # plan 对规则和实时目录一并绑定；再次准备产生的新 plan 必须重新批准。
        _write(_path(batch, 'plan.json'), _bytes(plan))
        report = {k: v for k, v in report.items() if k != 'payload'}
        report.update(payload_sha256=digest, payload_file=str(payload_path.relative_to(batch)),
                      plan_sha256=_sha(_bytes(plan)), bytes=len(raw), media=media_reports)
        _write(_path(batch, 'preflight.json'), _bytes(report))
        return report


def verify(batch_dir, *, config=None):
    """只读验证冻结件仍与本地最终批次、目录快照和当前映射器一致。"""
    batch = Path(batch_dir).resolve()
    settings = _config(config)
    plan_path = _path(batch, 'plan.json')
    plan_raw = plan_path.read_bytes(); plan = _read(plan_path)
    if plan.get('schema_version') != 1 or plan.get('state') != 'prepared':
        raise ValueError('没有有效的上传冻结计划，请先 prepare-upload')
    digest = plan.get('payload_sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('无效冻结摘要')
    expected = f'frozen/{digest}'
    if plan.get('payload_file') != expected + '/payload.json' or plan.get('catalog_file') != expected + '/catalog.json':
        raise ValueError('冻结路径与摘要不一致')
    raw = _path(batch, plan['payload_file']).read_bytes()
    catalog_raw = _path(batch, plan['catalog_file']).read_bytes()
    if _sha(raw) != digest or _sha(catalog_raw) != plan['catalog_sha256']:
        raise ValueError('冻结文件或目录快照已变化，必须重新准备并批准')
    _, source_raw, source = _source(batch)
    if _sha(source_raw) != plan['source_sha256']:
        raise ValueError('最终批次已变化，必须重新准备、审核和批准')
    if settings['base_url'] != plan['api_base']:
        raise ValueError('目标 API 与冻结计划不一致，必须重新批准')
    preflight = _read(_path(batch, 'preflight.json'))
    if (preflight.get('passed') is not True or preflight.get('payload_sha256') != digest
            or preflight.get('plan_sha256') != _sha(plan_raw)):
        raise ValueError('最近一次上传预检失败或与冻结计划不一致，须重新准备')
    mapped = _mapping(source, _read(_path(batch, plan['catalog_file'])), plan.get('mapping_options'))
    if not mapped['passed'] or _bytes(mapped['payload']) != raw:
        raise ValueError('冻结件不再满足当前映射校验，必须重新准备、审核和批准')
    from .upload_content import inspect_content
    content = inspect_content(batch, source, strict=True)
    if not content['passed'] or content['source_sha256'] != plan.get('content_source_sha256', {}):
        raise ValueError('解析或文章大意的来源不符、串材料或来源文件已变化，须重新准备和复核')
    return {'plan': plan, 'plan_sha256': _sha(plan_raw), 'payload': raw}


def approval_binding(batch_dir, *, config=None):
    frozen = verify(batch_dir, config=config)
    return {'approved_payload_sha256': frozen['plan']['payload_sha256'],
            'approved_upload_plan_sha256': frozen['plan_sha256'],
            'approved_api_base': frozen['plan']['api_base']}


def submit_approved(batch_dir, manifest, *, config=None, lock_held=False):
    """只提交已由 runner 确认的冻结字节；远端结果未知时禁止自动重复。"""
    batch = Path(batch_dir).resolve()
    settings = _config(config)
    with (contextlib.nullcontext() if lock_held else _agent_lock(10)):
        frozen = verify(batch, config=settings)
        plan = frozen['plan']; gate = manifest['stages']['upload_gate']
        if (gate.get('status') != mf.APPROVED or gate.get('approved_batch_sha256') != plan['source_sha256']
                or any(gate.get(k) != v for k, v in approval_binding(batch, config=settings).items())):
            raise ValueError('未批准本次冻结文件与目标 API，请人工重新确认')
        digest = plan['payload_sha256']
        attempt_path = _path(batch, f'attempts/{digest}.json')
        for prior in attempt_path.parent.glob('*.json'):
            if prior == attempt_path:
                continue
            previous = _read(_path(batch, 'attempts/' + prior.name))
            if previous.get('state') in ('submitting', 'unknown'):
                raise ValueError('该批另一次提交结果未知，修改载荷也不能重发；先由人工在远端核实')
            if previous.get('state') == 'accepted':
                raise ValueError('该批已有不同载荷的成功暂存记录；如需新增或替换须明确范围并另建批次')
        if attempt_path.is_file():
            previous = _read(attempt_path)
            if previous.get('state') == 'accepted':
                return previous['receipt']
            if previous.get('state') in ('submitting', 'unknown'):
                raise ValueError('前次提交结果未知，禁止重发；请人工在远端工作台核实暂存记录')
        client = upload_client.ApiClient(settings)
        current = client.fetch_catalog()
        catalog = _read(_path(batch, plan['catalog_file']))
        # fetched_at 不属于目录语义；其余目录值有变化时必须重新准备。
        def semantic(value):
            return {k: v for k, v in value.items() if k not in ('fetched_at', 'fetchedAt')}
        if _bytes(semantic(current)) != _bytes(semantic(catalog)):
            raise ValueError('远端目录已变化，请重新准备、审核和批准')
        for url, kind in sorted(_media(json.loads(frozen['payload'])).items()):
            client.check_media(url, kind=kind)
        if frozen['payload'] != verify(batch, config=settings)['payload']:
            raise ValueError('发送前冻结文件发生变化')
        # 持久化后才开始 POST；即使进程在收到响应前中断也不会静默重发。
        _write(attempt_path, _bytes({'state': 'submitting', 'payload_sha256': digest, 'api_base': plan['api_base']}))
        try:
            response = client.submit(frozen['payload'])
        except upload_client.APIError as exc:
            state = 'rejected' if exc.http_status in (400, 401, 403, 409, 413) else 'unknown'
            _write(attempt_path, _bytes({'state': state, 'payload_sha256': digest,
                                        'error_code': exc.code, 'http_status': exc.http_status, 'details': exc.details}))
            raise
        receipt = {'id': response['id'], 'stage': 'pending_confirmation',
                   'state': 'staged', 'payload_sha256': digest, 'source_sha256': plan['source_sha256'],
                   'api_base': plan['api_base'], 'submitted_at': mf.now_iso(),
                   'summary': plan.get('summary', {}), 'remote_confirmation_required': True}
        _write(_path(batch, 'receipt.json'), _bytes(receipt))
        _write(attempt_path, _bytes({'state': 'accepted', 'payload_sha256': digest, 'receipt': receipt}))
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', required=True, type=Path)
    parser.add_argument('--catalog', type=Path, help='已读取的远端目录快照；只替代目录 GET，不代表媒体已部署')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    try:
        report = prepare(args.batch_dir, catalog=_read(args.catalog) if args.catalog else None,
                         config=upload_client.load_config(args.config))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['passed'] else 1
    except (OSError, ValueError, upload_client.APIError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
