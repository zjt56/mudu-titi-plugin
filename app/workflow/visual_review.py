"""原卷图片的视觉语义复核（候选证据，不生成内容）。

背景：Word/PDF 提取把图片压成 `[IMG name]` 占位，纯文本 + 正则判不出
"这张图是选项图/材料表格/天气图标/曲线图/纯装饰"，导致图片题、图片选项题、
表格材料题的组题与归属靠猜。本模块用本地 Harness 视觉模型
（magpie 网关 OpenAI 兼容接口，input=[text,image]）逐图识别，产出一份
只读的 `01_source/image_semantics.json` 复核清单。

严格边界（遵守项目硬规则 4/22/28）：
- 只产出"图是什么 / 属于哪道题或材料 / 是否承载可读信息"的结构化候选；
- 不生成、改写、补全题干/选项/答案/解析/文章大意；不据此放行任何闸门；
- 网关不可用或未配置时优雅跳过，绝不影响主流程，也不伪造语义；
- 识别结果写独立产物，正式链路仍以原卷原文为准，图片语义只作佐证。

调用方式（deterministic，无 agent 桥依赖）：
    from workflow import visual_review
    visual_review.build(batch_dir)   # 幂等；网关不可用时返回 skipped
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "visual-review-v1"
ARTIFACT = "01_source/image_semantics.json"

# 图片文件后缀；扫描批次内所有原件图片（排除临时 extract-* 工作目录）。
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_TMP_DIR = re.compile(r"(?:^|/)extract-[^/]+/")

# 网关/模型默认值，可被环境变量覆盖；不写死密钥。
DEFAULT_ENDPOINT = "http://127.0.0.1:3425/v1/chat/completions"
DEFAULT_MODEL = "auto/deepseek-v4.1-flash"
MAX_SIDE_PX = 1400  # 大图降采样，控制 token 与延迟

PROMPT = (
    "这是一张英语试卷原件里的图片或页面。请只输出一个 JSON 对象，字段：\n"
    "kind：图片类别，取值之一：page(整页版面) / option_image(选项配图) / "
    "material_table(材料表格) / chart(图表曲线) / icon(天气/装饰小图标) / "
    "illustration(插图) / formula(公式) / other；\n"
    "title：一句话中文说明它是什么；\n"
    "contains_text：图里是否含可读文字（true/false）；\n"
    "readable_text：图里可读的文字/词句，原样，能读出多少写多少，不要编造，没有就空串；"
    "若是整页版面(page)，readable_text 只填页面上 2-3 个关键栏目标题，务必简短，不要抄全文；\n"
    "summary：不超过 40 字的中文内容概述。\n"
    "严格输出合法 JSON：字符串值内不得出现真实换行，需要换行用 \\n 转义，双引号用 \\\" 转义。\n"
    "只依据图里真实可见的内容，不要猜测题目答案，不要补全看不到的文字。"
)


class VisualReviewError(Exception):
    """视觉复核失败；调用方应降级跳过，不阻断主流程。"""


def _safe_path(root: Path, value: str) -> Path:
    root = root.resolve()
    target = (root / value).resolve()
    if not str(target).startswith(str(root)):
        raise VisualReviewError(f"图片路径越界：{value}")
    return target


def _load_key() -> str:
    key = os.environ.get("MAGPIE_GATEWAY_KEY", "").strip()
    if key:
        return key
    for base in (os.environ.get("DSH_HOME"), os.path.expanduser("~/.dsh")):
        if not base:
            continue
        cred = Path(base) / ".credentials.yaml"
        try:
            for line in cred.read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"\s*MAGPIE_GATEWAY_KEY:\s*(.*?)\s*", line)
                if match:
                    return match.group(1).strip("\"'")
        except OSError:
            continue
    return ""


def _downsample(path: Path) -> bytes:
    """大图降采样为 JPEG，控制视觉 token；Pillow 不可用则原样读。"""
    try:
        from PIL import Image
        import io
        image = Image.open(path)
        image = image.convert("RGB")
        w, h = image.size
        if max(w, h) > MAX_SIDE_PX:
            scale = MAX_SIDE_PX / max(w, h)
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=82)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - 降采样失败退回原图，不影响识别
        return path.read_bytes()


def _identify(path: Path, rel: str, endpoint: str, model: str, key: str, timeout: float = 90.0) -> dict:
    data = _downsample(path)
    b64 = base64.b64encode(data).decode()
    payload = {
        "model": model,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]}],
    }
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as exc:
        raise VisualReviewError(f"视觉网关 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VisualReviewError(f"视觉网关不可达：{exc}") from exc
    try:
        text = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisualReviewError("视觉网关返回结构异常") from exc
    return {**(_parse_json(text) or {}), "_raw": text[:4000]}


def _parse_json(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    # 先按标准 JSON 取第一个完整对象；失败再尝试修复字符串内未转义换行。
    for attempt in (text, _escape_inner_newlines(text)):
        for match in re.finditer(r"\{", attempt):
            try:
                obj, _ = decoder.raw_decode(attempt[match.start():])
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def _escape_inner_newlines(text: str) -> str:
    """把字符串字面量里的真实换行替换成 \\n，修复模型输出的非法 JSON。

    只处理双引号字符串内部；遇未转义换行时补 \\n。
    """
    out, in_str, escaped = [], False, False
    for ch in text:
        if in_str:
            if escaped:
                out.append(ch); escaped = False; continue
            if ch == "\\":
                out.append(ch); escaped = True; continue
            if ch == '"':
                in_str = False; out.append(ch); continue
            if ch == "\n":
                out.append("\\n"); continue
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _iter_images(root: Path):
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if _TMP_DIR.search("/" + rel):
            continue
        yield path, rel


def build(batch_dir, *, endpoint: str | None = None, model: str | None = None,
          limit: int = 0, force: bool = False) -> dict:
    """扫描批次原件图片，逐张视觉复核，写 01_source/image_semantics.json。

    幂等：已存在且非 force 时直接返回既有清单。网关未配置/不可用返回
    status=unavailable 且不写产物——主流程照常继续，绝不伪造语义。
    """
    root = Path(batch_dir).resolve()
    out_path = _safe_path(root, ARTIFACT)
    if out_path.is_file() and not force:
        return {"status": "cached", "artifact": ARTIFACT}

    endpoint = (endpoint or os.environ.get("VISUAL_REVIEW_ENDPOINT") or DEFAULT_ENDPOINT).strip()
    model = (model or os.environ.get("VISUAL_REVIEW_MODEL") or DEFAULT_MODEL).strip()
    key = _load_key()
    if not key:
        return {"status": "unavailable", "reason": "未配置视觉网关密钥", "artifact": None}

    items, skipped = [], []
    images = list(_iter_images(root))
    if limit and limit > 0:
        images = images[:limit]
    for path, rel in images:
        try:
            info = _identify(path, rel, endpoint, model, key)
        except VisualReviewError as exc:
            skipped.append({"path": rel, "reason": str(exc)[:200]})
            continue
        items.append({
            "path": rel,
            "kind": str(info.get("kind") or "other"),
            "title": str(info.get("title") or "")[:200],
            "contains_text": bool(info.get("contains_text")),
            "readable_text": str(info.get("readable_text") or "")[:2000],
            "summary": str(info.get("summary") or "")[:200],
        })

    if not items:
        # 一张都没识别成功：视为网关/模型不可用，不写空产物冒充完成。
        return {"status": "unavailable", "reason": "所有图片均识别失败",
                "skipped": skipped[:20], "artifact": None}

    payload = {
        "version": VERSION,
        "model": model,
        "image_count": len(items),
        "skipped": skipped,
        "note": "视觉复核仅作原卷图片语义候选证据；正式题干/答案/解析仍以原卷原文为准，不据此放行闸门。",
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return {"status": "built", "artifact": ARTIFACT,
            "image_count": len(items), "skipped": len(skipped)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="原卷图片视觉语义复核（候选证据）")
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.batch_dir, limit=args.limit, force=args.force), ensure_ascii=False, indent=2))
