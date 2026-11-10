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
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
# 设备本地模型选择优先；仓库 config 只是默认模板。
ROUTER="${WF_ROUTER_FILE:-$DSH_HOME/plugin-state/md-upload-tiku/model-router.json}"
[ -f "$ROUTER" ] || ROUTER="$PLUGIN_DIR/config/model-router.json"
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
  head -c 4000 "$prompt" | LC_ALL=C sed -n 's/^Stage:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' | head -1
}

# 从提示词头部判断子 Agent 角色（工作流提示词带 "Agent: <role>"）。
detect_agent() {
  head -c 4000 "$prompt" | LC_ALL=C sed -n 's/^Agent:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' | head -1
}

# 从提示词判断审核轮次（review 提示词带 "ROUND: labels|sample"）。
detect_round() {
  head -c 20000 "$prompt" | LC_ALL=C sed -n 's/^ROUND:[[:space:]]*\([A-Za-z0-9_-]*\).*/\1/p' | head -1
}

# 用 python3 解析 model-router.json，输出一行三个字段（用 \t 分隔）：
#   provider    model_id    基础profile
# 选择规则：
#   优先级：review 时 rounds[round] → roles[agent] → review.default；其余 roles[agent] → stages[stage] → default
#   选择逻辑在 backend/route_pick.py，便于单测。
# 每个条目形如 {"provider":"deepseek-official","model":"deepseek-flash","profile":"wf-deepseek-fast"}
# 不提供任何“换一个模型”的兜底：配置缺失/损坏/没有默认模型时直接失败，
# 由用户重新选择模型，绝不静默改用其他模型。
read_route() {
  stage="$1"; round="$2"; agent="$3"
  python3 "$SCRIPT_DIR/route_pick.py" "$ROUTER" "$stage" "$round" "$agent"
}

STAGE="$(detect_stage || true)"
ROUND="$(detect_round || true)"
AGENT="$(detect_agent || true)"
# 路由失败必须终止本次调用；不能继续用其他模型。
ROUTE="$(read_route "$STAGE" "$ROUND" "$AGENT")" || { log "无法确定模型路由，已停止（不切换到其他模型）"; exit 1; }
PROVIDER="$(printf '%s' "$ROUTE" | cut -f1)"
MODEL="$(printf '%s' "$ROUTE" | cut -f2)"
BASE_PROFILE="$(printf '%s' "$ROUTE" | cut -f3)"
log "stage=${STAGE:-?} agent=${AGENT:-?} round=${ROUND:-?} -> provider=$PROVIDER model=$MODEL (base=$BASE_PROFILE)"

BASE_DIR="$DSH_HOME/profiles/$BASE_PROFILE"
if [ ! -d "$BASE_DIR" ]; then
  # 基础 profile 缺失时退回到存在的 wf-deepseek-fast / wf-deepseek / desktop
  for cand in wf-deepseek-fast wf-deepseek desktop; do
    if [ -d "$DSH_HOME/profiles/$cand" ]; then BASE_DIR="$DSH_HOME/profiles/$cand"; break; fi
  done
fi
[ -d "$BASE_DIR" ] || { log "no base profile found under $DSH_HOME/profiles"; exit 1; }

# 复用按路由生成的 profile。每个分片仍是独立 dsh 进程，但不再重复复制
# 整个基础 profile、改写 YAML；路由或基础 patch 变化时通过 key 自动换缓存。
# 用户 desktop profile 里额外配置的 provider（如 opencode-go）也会被合入路由
# profile，所以缓存键同时包含它的状态，避免新增 provider 后继续复用旧缓存。
ROUTE_KEY="$(python3 - "$BASE_DIR" "$PROVIDER" "$MODEL" "$DSH_HOME/profiles/desktop/cordis.patch.yml" <<'PY'
import hashlib, pathlib, sys
base, provider, model, overlay = sys.argv[1:]
patch = pathlib.Path(base) / "cordis.patch.yml"
stat = patch.stat()
parts = [str(patch.resolve()), provider, model, str(stat.st_mtime_ns), str(stat.st_size)]
overlay = pathlib.Path(overlay)
if overlay.is_file():
    ostat = overlay.stat()
    parts += [str(overlay.resolve()), str(ostat.st_mtime_ns), str(ostat.st_size)]
print(hashlib.sha256("\\0".join(parts).encode()).hexdigest()[:24])
PY
)"
TMP_PROFILE="wf-plugin-route-$ROUTE_KEY"
TMP_DIR="$DSH_HOME/profiles/$TMP_PROFILE"
PATCH="$TMP_DIR/cordis.patch.yml"
READY="$TMP_DIR/.wf-route-ready"
INIT_LOCK="$TMP_DIR.init"
NEEDS_PATCH=0
if [ ! -f "$READY" ]; then
  if mkdir "$INIT_LOCK" 2>/dev/null; then
    trap 'rm -rf "$INIT_LOCK" 2>/dev/null || true' EXIT INT TERM
    mkdir -p "$TMP_DIR"
    cp -R "$BASE_DIR"/. "$TMP_DIR"/
    NEEDS_PATCH=1
  else
    # 另一分片正在初始化同一路由；等待完整 patch（不是只等目录出现）。
    for _ in $(seq 1 100); do
      [ -f "$READY" ] && break
      sleep 0.02
    done
  fi
fi
[ -f "$PATCH" ] || { log "route profile initialization timed out"; exit 1; }

# 合入基础 profile 缺少、但用户 desktop profile 已配置的 provider（如 opencode-go）。
# 只复制整段 provider 定义，不改动基础 profile 自己的模型配置；脚本幂等。
if [ "$NEEDS_PATCH" -eq 1 ] && [ -f "$DSH_HOME/profiles/desktop/cordis.patch.yml" ]; then
  python3 "$SCRIPT_DIR/merge-route-providers.py" "$PATCH" \
      "$DSH_HOME/profiles/desktop/cordis.patch.yml" \
    || log "provider merge skipped (route profile keeps base providers)"
fi

# 用 python 精确替换/追加 agent-default-model 行里的 provider 与 model。
# 只有初始化 owner 执行一次；后续分片直接复用已经就绪的 profile。
if [ "$NEEDS_PATCH" -eq 1 ]; then
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
  # 标记必须最后写入，读者看到它时 patch 已完整落盘。
  : > "$READY"
  rmdir "$INIT_LOCK" 2>/dev/null || true
fi

# 自愈加固：READY 文件不能证明 agent-default-model 已被正确改写（历史上曾出现
# 临时 profile 已就绪但 provider/model 仍是 desktop 基线的 magpie/auto/...，
# 导致部分分片误走 magpie 中转并报 UNKNOWN_MODEL）。使用前强校验实际值，不符
# 则就地重写为 route_pick 选定的 provider/model，避免坏 profile 被复用。
if ! python3 - "$PATCH" "$PROVIDER" "$MODEL" <<'PY'
import sys, re
path, provider, model = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    lines = open(path, encoding="utf-8").read().splitlines()
except OSError:
    sys.exit(1)
start = next((i for i, ln in enumerate(lines)
              if re.match(r'^- id:\s*agent-default-model\b', ln)), -1)
if start == -1:
    sys.exit(1)
end = len(lines)
for j in range(start + 1, len(lines)):
    if re.match(r'^- ', lines[j]) or (lines[j].strip() and not lines[j].startswith(' ')):
        end = j; break
block = lines[start:end]
def get(k):
    for b in block:
        m = re.match(r'^\s*%s:\s*(.*)$' % k, b)
        if m: return m.group(1).strip()
    return None
ok = get('provider') == provider and get('model') == model
if ok:
    sys.exit(0)
# 就地重写 provider/model
out = []
for b in block:
    if re.match(r'^\s*provider:', b):
        out.append(re.match(r'^\s*', b).group() + "provider: %s" % provider)
    elif re.match(r'^\s*model:', b):
        out.append(re.match(r'^\s*', b).group() + "model: %s" % model)
    else:
        out.append(b)
lines[start:end] = out
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wf-router: repaired agent-default-model -> %s / %s" % (provider, model), file=sys.stderr)
sys.exit(0)
PY
then
  log "route profile agent-default-model 仍与目标不符，且无法修复，已停止（不切换其他模型）"
  exit 1
fi

# 注入该 provider 对应的凭证环境变量。
case "$PROVIDER" in
  deepseek-official|deepseek*)
    KEY="$(cred_key DEEPSEEK_API_KEY || true)"
    ;;
  opencode-go|opencode*)
    KEY="$(cred_key OPENCODE_GO_API_KEY || true)"
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
OPENCODE_GO_API_KEY_V="$(cred_key OPENCODE_GO_API_KEY || true)"

run_once() {
  DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY_V" \
  MAGPIE_GATEWAY_KEY="$MAGPIE_GATEWAY_KEY_V" \
  OPENCODE_GO_API_KEY="$OPENCODE_GO_API_KEY_V" \
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
