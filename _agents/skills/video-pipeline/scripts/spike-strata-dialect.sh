#!/usr/bin/env bash
# spike #83: 关思维链方言 + 字段容忍度测试矩阵（WSL 内经主机 IP POST）。
set -u
GW=$(ip route show default | awk '/default/ {print $3; exit}')
BASE="http://$GW:8080"
Q='What is 2+2? Answer with just the number.'

probe() {
  desc="$1"; body="$2"; mt="$3"
  tf=$(mktemp)
  code=$(curl -s -m 180 -o "$tf" -w '%{http_code}' -X POST "$BASE/v1/chat/completions" \
    -H 'Content-Type: application/json' -d "$body")
  python3 - "$desc" "$code" "$tf" <<'EOF'
import json, sys
desc, code, path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    r = json.load(open(path))
except Exception as e:
    print(f"== {desc} => HTTP {code}, body-unparseable: {open(path).read()[:200]}")
    sys.exit()
if 'choices' in r:
    m = r['choices'][0].get('message', {})
    rc = (m.get('reasoning_content') or '')
    c = (m.get('content') or '')
    print(f"== {desc} => HTTP {code}, reasoning={len(rc)}ch, content={c[:80]!r}, finish={r['choices'][0].get('finish_reason')}")
else:
    print(f"== {desc} => HTTP {code}, body={json.dumps(r)[:220]}")
EOF
  rm -f "$tf"
}

probe "T0-baseline" "{\"model\":\"strata\",\"messages\":[{\"role\":\"user\",\"content\":\"$Q\"}],\"max_tokens\":256,\"temperature\":0}" 256
probe "T1-ctk-enable_thinking-false" "{\"model\":\"strata\",\"messages\":[{\"role\":\"user\",\"content\":\"$Q\"}],\"max_tokens\":64,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}" 64
probe "T2-unknown-top-field" '{"model":"strata","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"totally_unknown_field":123}' 16
probe "T3-ctk-garbage-value" '{"model":"strata","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":"banana"}}' 16
probe "T4-missing-model" '{"messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0}' 16
probe "T5-9router-thinking-field" '{"model":"strata","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"thinking":{"type":"disabled"}}' 16
probe "T6-nothink-in-prompt" "{\"model\":\"strata\",\"messages\":[{\"role\":\"user\",\"content\":\"/no_think $Q\"}],\"max_tokens\":64,\"temperature\":0}" 64
probe "T7-empty-model" '{"model":"","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0}' 16
