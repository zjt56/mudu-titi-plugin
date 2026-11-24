#!/usr/bin/env python3
"""按 model-router.json 选择本次调用使用哪个模型（子 Agent 角色优先）。

用法: route_pick.py <router.json> <stage> <round> <agent>
输出: provider\tmodel\tprofile；无有效默认模型时以 1 退出，绝不静默换模型。
"""
import json, os, re, sys
from pathlib import Path

router_path, stage, rnd, agent = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def _default_model_block(path):
    """Read `- id: agent-default-model` provider/model from a DSH profile patch."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    inside, provider, model = False, None, None
    for line in lines:
        if re.match(r"^-\s*id:\s*['\"]?agent-default-model['\"]?\s*(?:#.*)?$", line):
            inside = True
            continue
        if inside:
            if re.match(r"^-\s", line):
                break
            found = re.match(r"^\s+provider:\s*['\"]?([^'\"#\s]+)", line)
            if found:
                provider = found.group(1)
            found = re.match(r"^\s+model:\s*['\"]?([^'\"#]+?)['\"]?\s*(?:#.*)?$", line)
            if found:
                model = found.group(1).strip()
    return {"provider": provider, "model": model} if provider and model else None


def _overlay_providers(path):
    """Providers the generated route profile can serve (llm-pi-ai / llm-deepseek)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    ids, block = set(), ""
    for line in lines:
        found = re.match(r"^-\s*id:\s*['\"]?([A-Za-z0-9_-]+)", line)
        if found:
            block = found.group(1)
            continue
        if block in ("llm-deepseek", "llm-deepseek-account"):
            ids.add("deepseek-official")
        found = re.match(r"^ {6}([A-Za-z0-9._-]+):\s*$", line) if block == "llm-pi-ai" else None
        if found:
            ids.add(found.group(1))
    return ids


def _first_model(path):
    """First declared servable model in a profile patch (bootstrap fallback)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    block, provider = "", ""
    for line in lines:
        found = re.match(r"^-\s*id:\s*['\"]?([A-Za-z0-9_-]+)", line)
        if found:
            block, provider = found.group(1), ""
            continue
        if block == "llm-pi-ai":
            found = re.match(r"^ {6}([A-Za-z0-9._-]+):\s*$", line)
            if found:
                provider = found.group(1)
                continue
            found = re.match(r"^ {10}-\s*id:\s*(.+?)\s*$", line)
            if found and provider:
                return {"provider": provider, "model": found.group(1).strip().strip('"\'')}
        elif block in ("llm-deepseek", "llm-deepseek-account"):
            found = re.match(r"^ {6}-\s*id:\s*(.+?)\s*$", line)
            if found:
                return {"provider": "deepseek-official", "model": found.group(1).strip().strip('"\'')}
    return None


def harness_default():
    """The model currently chosen in this device's Harness profile.

    The repository must not pin one model: every device picks its own Harness
    model in the DSH model menu or in the plugin settings.
    """
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    names = []
    if os.environ.get("DSH_PROFILE"):
        names.append(os.environ["DSH_PROFILE"])
    names.append("desktop")
    for name in names:
        patch = home / "profiles" / name / "cordis.patch.yml"
        found = _default_model_block(patch)
        if not found:
            continue
        # 只接受该 profile 自己声明、会被路由 overlay 复制的 provider。
        if found["provider"] not in _overlay_providers(patch):
            continue
        return dict(found, profile=name)
    # 没有显式 Harness 默认模型时，用宿主 profile 里第一个可服务模型兜底。
    for name in ("desktop", "wf-deepseek-fast", "wf-deepseek"):
        patch = home / "profiles" / name / "cordis.patch.yml"
        if patch.is_file():
            first = _first_model(patch)
            if first:
                return dict(first, profile=name)
    return None
try:
    data = json.load(open(router_path, encoding="utf-8"))
except Exception as exc:
    sys.stderr.write("wf-router: cannot read model-router.json: %s\n" % exc)
    sys.exit(1)
if not isinstance(data, dict) or isinstance(data, list):
    sys.stderr.write("wf-router: model-router.json root must be an object\n")
    sys.exit(1)

def _section(name, value):
    """Read a router section without turning malformed config into absence."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"model-router.json {name} must be an object")
    return value


def _override(section, key, label):
    """Return an explicit override; reject present-but-incomplete entries."""
    if key is None or key not in section:
        return None
    entry = section[key]
    if (not isinstance(entry, dict) or not isinstance(entry.get("provider"), str)
            or not entry.get("provider") or not isinstance(entry.get("model"), str)
            or not entry.get("model")):
        raise ValueError(f"model-router.json {label}.{key} is incomplete; provider/model are required")
    return entry

raw_default = data.get("default")
default = raw_default if isinstance(raw_default, dict) else {}
if raw_default is None or raw_default == {}:
    # 仓库不固定模型：未显式配置时跟随本机 Harness 当前模型。
    default = harness_default() or {}
elif not (default.get("provider") and default.get("model")):
    # present but corrupt is an error, never a silent fallback
    default = {}
if raw_default is not None and raw_default != {} and (
        not isinstance(raw_default, dict) or not isinstance(raw_default.get("provider"), str)
        or not raw_default.get("provider") or not isinstance(raw_default.get("model"), str)
        or not raw_default.get("model")):
    sys.stderr.write("wf-router: no valid default provider/model; model-router.json default is incomplete and provider/model are required\n")
    sys.exit(1)
try:
    stages = _section("stages", data.get("stages"))
    roles = _section("roles", data.get("roles"))
    review = _section("review", data.get("review"))
    rounds = _section("review.rounds", review.get("rounds"))
except ValueError as exc:
    sys.stderr.write("wf-router: %s\n" % exc)
    sys.exit(1)

if not (isinstance(default, dict) and default.get("provider") and default.get("model")):
    sys.stderr.write("wf-router: model-router.json has no valid default provider/model and no "
                     "Harness agent-default-model was found; select a model in the plugin settings\n")
    sys.exit(1)

chosen = None
effective_stage = stage or ("review" if rnd else "")
if effective_stage == "review":
    entry = (_override(rounds, rnd, "review.rounds") if rnd else None)
    if entry is None:
        entry = _override(roles, agent, "roles") if agent else None
    if entry is None:
        entry = _override(roles, "review", "roles")
    if entry is None:
        entry = _override(stages, "review", "stages")
    if entry is None:
        entry = _override(review, "default", "review")
    chosen = entry or default
else:
    # 子 Agent 角色优先于阶段，便于同一阶段的多个角色独立选模型。
    entry = _override(roles, agent, "roles") if agent else None
    if entry is None and effective_stage:
        entry = _override(stages, effective_stage, "stages")
    if entry is None and (effective_stage == "preassemble" or agent == "preassemble"):
        entry = _override(roles, "types", "roles") or _override(stages, "types", "stages")
    chosen = entry or default

provider = chosen.get("provider")
model = chosen.get("model")
profile = chosen.get("profile") or "wf-deepseek-fast"
sys.stdout.write("%s\t%s\t%s\n" % (provider, model, profile))
