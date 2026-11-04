#!/bin/sh
# ---------------------------------------------------------------------------
# wf-router.sh —— 插件版模型路由适配器（不修改任何现有 workflow 代码）
#
# 工作流通过 local-agent 的 provider=command 调用本脚本：
#   命令行参数： {prompt_file} {response_file}
# 本脚本读取同目录的 model-router.json，按提示词里的 Stage/ROUND 标记选择模型，
# 为所选模型动态生成一份临时 DSH profile（复制基础 profile，仅改 agent-default-model），
# 注入对应 provider 的凭证后调用 `dsh --profile <临时> -`，把结果写入 response_file。
#
# 环境变量（由宿主插件 index.js 注入或自动发现）：
#   WF_PLUGIN_DIR     插件版目录（默认本脚本上两级）
#   DSH_BIN           dsh 可执行路径（默认 $HOME/.local/bin/dsh 或 PATH 中的 dsh）
#   DSH_HOME          dsh 家目录（默认 $HOME/.dsh）
#
# 退出码：0 成功；其他按工作流约定（限流在下文标注）。
# ---------------------------------------------------------------------------
set -eu

prompt="$1"
response="$2"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_DIR="${WF_PLUGIN_DIR:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}"
ROUTER="$PLUGIN_DIR/config/model-router.json"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
DSH_BIN="${DSH_BIN:-}"
if [ -z "$DSH_BIN" ]; then
  if [ -x "$HOME/.local/bin/dsh" ]; then DSH_BIN="$HOME/.local/bin/dsh"; else DSH_BIN="dsh"; fi
fi
CREDS="$DSH_HOME/.credentials.yaml"

log() { echo "wf-router: $*" >&2; }

# 读取凭证文件里某个 KEY 的值（仅用于注入环境变量，不落盘、不打印）。
cred_key() {
  [ -f "$CREDS" ] || return 0
  sed -n "s/^[[:space:]]*$1:[[:space:]]*//p" "$CREDS" | head -1 | tr -d '\r'
}

# 从提示词头部判断 Stage（工作流提示词带 "Stage: <name>"），用于选择基础阶段模型。
detect_stage() {
  head -c 4000 "$prompt" | sed -n 's/^Stage:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' | head -1
}

# 从提示词判断审核轮次（review 提示词带 "ROUND: labels|knowledge|sample"）。
detect_round() {
  head -c 20000 "$prompt" | sed -n 's/^ROUND:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' | head -1
}

# 用 python3 解析 model-router.json，输出一行三个字段（用 \t 分隔）：
#   provider    model_id    基础profile
# 选择规则：
#   stage=review 且能识别 round → 用 review.rounds[round]（为空则落回 review.default）
#   其他（或 review 未配置）     → stages[stage]（为空则 default）
# 每个条目形如 {"provider":"deepseek-official","model":"deepseek-v4-pro","profile":"wf-deepseek"}
read_route() {
  stage="$1"; round="$2"
  python3 - "$ROUTER" "$stage" "$round" <<'PY'
import json, sys
router_path, stage, rnd = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    data = json.load(open(router_path, encoding="utf-8"))
except Exception as exc:
    sys.stderr.write("wf-router: cannot read model-router.json: %s\n" % exc)
    # 兜底：默认 deepseek 官方 pro
    sys.stdout.write("deepseek-official\tdeepseek-v4-pro\twf-deepseek\n")
    sys.exit(0)

def pick(entry, fallback):
    if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
        return entry
    return fallback

default = data.get("default") or {}
stages = data.get("stages") or {}
review = data.get("review") or {}

chosen = None
if stage == "review":
    rounds = review.get("rounds") or {}
    entry = rounds.get(rnd) if rnd else None
    if not (isinstance(entry, dict) and entry.get("provider") and entry.get("model")):
        entry = review.get("default")
    chosen = pick(entry, default)
else:
    chosen = pick(stages.get(stage), default)

provider = chosen.get("provider") or "deepseek-official"
model = chosen.get("model") or "deepseek-v4-pro"
profile = chosen.get("profile") or "wf-deepseek"
sys.stdout.write("%s\t%s\t%s\n" % (provider, model, profile))
PY
}

STAGE="$(detect_stage || true)"
ROUND="$(detect_round || true)"
ROUTE="$(read_route "$STAGE" "$ROUND")"
PROVIDER="$(printf '%s' "$ROUTE" | cut -f1)"
MODEL="$(printf '%s' "$ROUTE" | cut -f2)"
BASE_PROFILE="$(printf '%s' "$ROUTE" | cut -f3)"
log "stage=${STAGE:-?} round=${ROUND:-?} -> provider=$PROVIDER model=$MODEL (base=$BASE_PROFILE)"

BASE_DIR="$DSH_HOME/profiles/$BASE_PROFILE"
if [ ! -d "$BASE_DIR" ]; then
  # 基础 profile 缺失时退回到存在的 wf-deepseek-fast / wf-deepseek / desktop
  for cand in wf-deepseek-fast wf-deepseek desktop; do
    if [ -d "$DSH_HOME/profiles/$cand" ]; then BASE_DIR="$DSH_HOME/profiles/$cand"; break; fi
  done
fi
[ -d "$BASE_DIR" ] || { log "no base profile found under $DSH_HOME/profiles"; exit 1; }

# 生成一次性临时 profile（复制基础 profile，仅改 agent-default-model 的 provider/model）。
TMP_PROFILE="wf-plugin-run-$$-$(date +%s)"
TMP_DIR="$DSH_HOME/profiles/$TMP_PROFILE"
cleanup() { rm -rf "$TMP_DIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cp -R "$BASE_DIR" "$TMP_DIR"
PATCH="$TMP_DIR/cordis.patch.yml"

# 用 python 精确替换/追加 agent-default-model 行里的 provider 与 model。
python3 - "$PATCH" "$PROVIDER" "$MODEL" <<'PY'
import sys, re
path, provider, model = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
lines = text.splitlines()
out = []
i = 0
in_block = False
set_provider = False
set_model = False

def find_block(lines):
    for idx, ln in enumerate(lines):
        m = re.match(r'^- id:\s*agent-default-model\b', ln)
        if m:
            return idx
    return -1

start = find_block(lines)
if start == -1:
    # 追加一个新行
    lines.append("- id: agent-default-model")
    lines.append("  config:")
    lines.append("    provider: %s" % provider)
    lines.append("    model: %s" % model)
else:
    # 找到该块内的 config: 之后，替换 provider/model（缩进保留）
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r'^- ', lines[j]) or (lines[j].strip() and not lines[j].startswith(' ')):
            end = j
            break
    block = lines[start:end]
    has_provider = any(re.match(r'^\s*provider:', b) for b in block)
    has_model = any(re.match(r'^\s*model:', b) for b in block)
    nb = []
    for b in block:
        if re.match(r'^\s*provider:', b):
            indent = b[:len(b) - len(b.lstrip())]
            nb.append("%sprovider: %s" % (indent, provider))
        elif re.match(r'^\s*model:', b):
            indent = b[:len(b) - len(b.lstrip())]
            nb.append("%smodel: %s" % (indent, model))
        else:
            nb.append(b)
    if not has_provider or not has_model:
        # 在 config: 后插入缺失项
        for k, b in enumerate(nb):
            if re.match(r'^\s*config:\s*$', b):
                indent = b[:len(b) - len(b.lstrip())] + "  "
                ins = []
                if not has_provider: ins.append("%sprovider: %s" % (indent, provider))
                if not has_model: ins.append("%smodel: %s" % (indent, model))
                nb = nb[:k+1] + ins + nb[k+1:]
                break
    lines = lines[:start] + nb + lines[end:]

open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("patched agent-default-model -> %s / %s" % (provider, model), file=sys.stderr)
PY

# 注入该 provider 对应的凭证环境变量。
case "$PROVIDER" in
  deepseek-official|deepseek*)
    KEY="$(cred_key DEEPSEEK_API_KEY || true)"
    ;;
  magpie*|imported*|openai*)
    KEY="$(cred_key MAGPIE_GATEWAY_KEY || true)"
    ;;
  *)
    KEY=""
    ;;
esac

# 现成的 wf-agent.sh 里 DEEPSEEK 官方时用 DEEPSEEK_API_KEY；magpie 用 MAGPIE_GATEWAY_KEY。
# 同时把两个都注入一份（存在的），避免 provider 切换时缺 key。
DEEPSEEK_API_KEY_V="$(cred_key DEEPSEEK_API_KEY || true)"
MAGPIE_GATEWAY_KEY_V="$(cred_key MAGPIE_GATEWAY_KEY || true)"

run_once() {
  DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY_V" \
  MAGPIE_GATEWAY_KEY="$MAGPIE_GATEWAY_KEY_V" \
  "$DSH_BIN" --profile "$TMP_PROFILE" - < "$prompt"
}

TMP_OUT="$(mktemp)"
TMP_ERR="$(mktemp)"
RC=0
run_once > "$TMP_OUT" 2> "$TMP_ERR" || RC=$?

if [ "$RC" -eq 0 ] && [ -s "$TMP_OUT" ]; then
  cat "$TMP_OUT" > "$response"
  rm -f "$TMP_OUT" "$TMP_ERR"
  exit 0
fi

# 失败：把 stderr 写出去，供工作流 classify_failure 判断限流/额度。
cat "$TMP_ERR" >&2
if grep -Eiq 'DAILY_LIMIT_EXCEEDED|daily usage limit|usage limit has been reached' "$TMP_ERR"; then
  log "RATE_LIMIT: daily/quota exhausted"
elif grep -Eiq '429|RATE_LIMIT|rate[_ ]limit|QUOTA|too many requests' "$TMP_ERR"; then
  log "RATE_LIMIT: throttled"
fi
rm -f "$TMP_OUT" "$TMP_ERR"
log "dsh call failed (profile=$TMP_PROFILE model=$MODEL exit=$RC)"
exit "${RC:-1}"
