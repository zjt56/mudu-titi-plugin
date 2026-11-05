#!/bin/sh
# 工作流代理：机械阶段用 flash（快），语义审核用 pro（稳）。
# 撞到限流/额度（429/quota）时，自动切换到备用通道重试一次，避免整段任务因一次限额空转失败。
set -eu
prompt="$1"
response="$2"
creds="${DSH_HOME:-$HOME/.dsh}/.credentials.yaml"
key="$(sed -n 's/^[[:space:]]*DEEPSEEK_API_KEY:[[:space:]]*//p' "$creds" | head -1 | tr -d '\r')"
[ -n "$key" ] || { echo "wf-agent: DEEPSEEK_API_KEY not found" >&2; exit 1; }
profile=wf-deepseek-fast
if head -c 2000 "$prompt" | grep -q "Stage: review"; then profile=wf-deepseek; fi
# 备用通道：机械阶段退到 pro、审核退到 fast（另一条模型路由），仍走直连 DeepSeek 官方。
if [ "$profile" = "wf-deepseek" ]; then fallback=wf-deepseek-fast; else fallback=wf-deepseek; fi

# 判定 stderr 是否属于"可换通道重试"的限流/额度错误。
is_rate_limited() {
  grep -Eiq '429|RATE_LIMIT|rate[_ ]limit|DAILY_LIMIT_EXCEEDED|usage limit has been reached|QUOTA|too many requests' "$1"
}

# 子进程 dsh 启动时可能因并发写 profile 配置瞬时 EPERM；这类失败重试一次即可。
is_profile_race() {
  grep -Eiq 'EPERM|operation not permitted' "$1"
}

run_once() {
  # $1 profile, $2 stdout target, $3 stderr target
  DEEPSEEK_API_KEY="$key" "$HOME/.local/bin/dsh" --profile "$1" - <"$prompt" >"$2" 2>"$3"
}

tmp_out="$(mktemp)"
tmp_err="$(mktemp)"
trap 'rm -f "$tmp_out" "$tmp_err"' EXIT

rc=0
# 注意：`if run_once; then ... fi` 在条件为假时整体返回 0，不能用 $? 取失败码，
# 必须在 else 分支里取，否则失败会被吞成 0 且不写响应文件。
attempt() {
  if run_once "$1" "$tmp_out" "$tmp_err"; then
    cat "$tmp_out" >"$response"
    exit 0
  else
    rc=$?
  fi
}

attempt "$profile"

if [ "$rc" -ne 0 ] && is_profile_race "$tmp_err"; then
  echo "wf-agent: profile 写入冲突（EPERM），2 秒后重试一次" >&2
  sleep 2
  attempt "$profile"
fi

# 仅在限流/额度错误时换备用通道；其它失败（解析、崩溃）不盲目重跑。
if [ "$rc" -ne 0 ] && is_rate_limited "$tmp_err"; then
  echo "wf-agent: 通道 $profile 受限，切换到备用通道 $fallback 重试" >&2
  attempt "$fallback"
fi

# 两条通道都失败：保留原始错误信息，交回工作流按 rate_limit/failed 分类处理。
cat "$tmp_err" >&2
echo "wf-agent: 代理调用失败（profile=${profile}, exit=${rc}）" >&2
# 若失败根因是限流/额度，把标记放在最后一行，便于工作流识别并进入冷却而非判死。
if is_rate_limited "$tmp_err"; then
  echo "wf-agent: RATE_LIMIT 两条通道均受限（429/quota）" >&2
fi
exit "$rc"
