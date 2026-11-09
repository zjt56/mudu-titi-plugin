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


def harness_default():
    """The model currently chosen in this device's Harness profile.

    The repository must not pin one model: every device picks its own Harness
    model in the DSH model menu or in the plugin settings.
    """
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    names = []
    if os.environ.get("DSH_PROFILE"):
        names.append(os.environ["DSH_PROFILE"])
    names += ["desktop", "wf-deepseek-fast", "wf-deepseek"]
    for name in names:
        patch = home / "profiles" / name / "cordis.patch.yml"
        found = _default_model_block(patch)
        if not found:
            continue
        # 只接受该 profile 自己声明、会被路由 overlay 复制的 provider。
        if found["provider"] not in _overlay_providers(patch):
            continue
        return dict(found, profile=name)
    return None
try:
    data = json.load(open(router_path, encoding="utf-8"))
except Exception as exc:
    sys.stderr.write("wf-router: cannot read model-router.json: %s\n" % exc)
    sys.exit(1)

def pick(entry, fallback):
    if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
        return entry
    return fallback

raw_default = data.get("default")
default = raw_default if isinstance(raw_default, dict) else {}
if raw_default is None or raw_default == {}:
    # 仓库不固定模型：未显式配置时跟随本机 Harness 当前模型。
    default = harness_default() or {}
elif not (default.get("provider") and default.get("model")):
    # present but corrupt is an error, never a silent fallback
    default = {}
stages = data.get("stages") or {}
roles = data.get("roles") or {}
review = data.get("review") or {}

if not (isinstance(default, dict) and default.get("provider") and default.get("model")):
    sys.stderr.write("wf-router: model-router.json has no valid default provider/model and no "
                     "Harness agent-default-model was found; select a model in the plugin settings\n")
    sys.exit(1)

chosen = None
if stage == "review":
    rounds = review.get("rounds") or {}
    entry = rounds.get(rnd) if rnd else None
    if not (isinstance(entry, dict) and entry.get("provider") and entry.get("model")):
        entry = roles.get(agent) if agent else None
    if not (isinstance(entry, dict) and entry.get("provider") and entry.get("model")):
        entry = review.get("default")
    chosen = pick(entry, default)
else:
    # 子 Agent 角色优先于阶段，便于同一阶段的多个角色独立选模型。
    entry = roles.get(agent) if agent else None
    if not (isinstance(entry, dict) and entry.get("provider") and entry.get("model")):
        entry = stages.get(stage)
    chosen = pick(entry, default)

provider = chosen.get("provider")
model = chosen.get("model")
profile = chosen.get("profile") or "wf-deepseek-fast"
sys.stdout.write("%s\t%s\t%s\n" % (provider, model, profile))
