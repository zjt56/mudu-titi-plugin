#!/usr/bin/env python3
"""按 model-router.json 选择本次调用使用哪个模型（子 Agent 角色优先）。

用法: route_pick.py <router.json> <stage> <round> <agent>
输出: provider\tmodel\tprofile；无有效默认模型时以 1 退出，绝不静默换模型。
"""
import json, sys
router_path, stage, rnd, agent = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    data = json.load(open(router_path, encoding="utf-8"))
except Exception as exc:
    sys.stderr.write("wf-router: cannot read model-router.json: %s\n" % exc)
    sys.exit(1)

def pick(entry, fallback):
    if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
        return entry
    return fallback

default = data.get("default") or {}
stages = data.get("stages") or {}
roles = data.get("roles") or {}
review = data.get("review") or {}

if not (isinstance(default, dict) and default.get("provider") and default.get("model")):
    sys.stderr.write("wf-router: model-router.json has no valid default provider/model; "
                     "select a model again in the plugin settings\n")
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
