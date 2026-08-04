#!/usr/bin/env bash
# agentwatch quickstart example
#
# Builds a small fake home directory containing two session logs — one Claude
# Code, one Codex — and runs agentwatch against it with --home.  Nothing here
# touches your real logs, and it cleans up after itself.
#
# Usage:
#   bash examples/quickstart.sh

set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRATCH=$(mktemp -d /tmp/agentwatch-quickstart-XXXXXX)
trap "rm -rf '$SCRATCH'" EXIT

AW=(python3 -m agentwatch --home "$SCRATCH" --no-color)

CLAUDE="$SCRATCH/.claude/projects/-home-you-api-server"
CODEX="$SCRATCH/.codex/sessions/2026/08/04"
mkdir -p "$CLAUDE" "$CODEX"

# A timestamp a few seconds ago, so the logs count as active.
stamp() { python3 -c "
import sys
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(seconds=int(sys.argv[1]))).isoformat())
" "$1"; }

cat > "$CLAUDE/4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl" << EOF
{"type":"user","cwd":"/home/you/api-server","timestamp":"$(stamp 50)","message":{"role":"user","content":[{"type":"text","text":"(never printed — agentwatch does not read message text)"}]}}
{"type":"assistant","timestamp":"$(stamp 45)","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"pytest tests/test_auth.py -x"}}]}}
{"type":"user","timestamp":"$(stamp 33)","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"1 failed"}]}}
{"type":"assistant","timestamp":"$(stamp 28)","message":{"role":"assistant","content":[{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"/home/you/api-server/src/auth/session.py"}}]}}
{"type":"assistant","timestamp":"$(stamp 20)","message":{"role":"assistant","content":[{"type":"tool_use","id":"t3","name":"Bash","input":{"command":"pytest tests/test_auth.py -x"}}]}}
EOF

# Codex sends its shell commands as a snippet of JavaScript, and reports a patch
# in a separate event once it has applied.  These records are the real shapes,
# reduced.
cat > "$CODEX/rollout-2026-08-04T09-40-00-1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809.jsonl" << EOF
{"timestamp":"$(stamp 40)","type":"turn_context","payload":{"type":"turn_context","cwd":"/home/you/web"}}
{"timestamp":"$(stamp 38)","type":"event_msg","payload":{"type":"user_message","message":"(never printed)"}}
{"timestamp":"$(stamp 24)","type":"response_item","payload":{"type":"custom_tool_call","call_id":"c1","name":"exec","input":"const r = await tools.exec_command({\"cmd\":\"npm run build\",\"workdir\":\"/home/you/web\"}); text(r.output);\n"}}
{"timestamp":"$(stamp 22)","type":"response_item","payload":{"type":"custom_tool_call_output","call_id":"c1","output":[{"type":"input_text","text":"Script failed\nWall time 1.4 seconds\nOutput:\n"}]}}
{"timestamp":"$(stamp 12)","type":"response_item","payload":{"type":"custom_tool_call","call_id":"c2","name":"exec","input":"const patch = \"*** Begin Patch\n*** Update File: components/Nav.tsx\n*** End Patch\";\ntext(await tools.apply_patch(patch));\n"}}
{"timestamp":"$(stamp 11)","type":"event_msg","payload":{"type":"patch_apply_end","call_id":"exec-1","success":true,"changes":{"/home/you/web/components/Nav.tsx":{"type":"update"}}}}
EOF

cd "$HERE"

echo "=== 1. Everything that has happened ==="
COLUMNS=88 "${AW[@]}" --since 5m --once
echo ""

echo "=== 2. Only the failures ==="
COLUMNS=88 "${AW[@]}" --since 5m --once --only error
echo ""

echo "=== 3. Only one project ==="
COLUMNS=88 "${AW[@]}" --since 5m --once --project web
echo ""

echo "=== 4. Every file that was written, as JSON ==="
"${AW[@]}" --since 5m --once --only write --json
echo ""

echo "=== 5. Live mode (2 seconds, then a new command is appended) ==="
(
  sleep 1
  echo "{\"type\":\"assistant\",\"timestamp\":\"$(stamp 0)\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"tool_use\",\"id\":\"t9\",\"name\":\"Bash\",\"input\":{\"command\":\"git commit -m 'fix auth session'\"}}]}}" \
    >> "$CLAUDE/4ef1361b-07e4-4bc9-bb29-1783b761d677.jsonl"
) &
COLUMNS=88 timeout 3 "${AW[@]}" || true
echo ""

echo "Done. Scratch home removed: $SCRATCH"
