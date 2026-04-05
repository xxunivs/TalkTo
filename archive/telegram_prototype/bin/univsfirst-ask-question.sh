#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="/home/commit/.openclaw/workspaces/UnivsFirst_agent/state"
STATE_FILE="${STATE_DIR}/ask-question.json"
LOCK_FILE="${STATE_DIR}/ask-question.lock"
CONFIG_FILE="/home/commit/.openclaw/openclaw.json"
CHAT_ID="8786432628"
ACCOUNT_ID="UnivsFirst_bot"

mkdir -p "${STATE_DIR}"
exec 9>"${LOCK_FILE}"
flock -n 9 || exit 0

# Read current question index
current="$(sed -n 's/.*"current":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "${STATE_FILE}" 2>/dev/null | head -n 1 || true)"
case "${current:-}" in
  ''|*[!0-9]*)
    current=0
    ;;
esac

next=$((current + 1))
updated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Questions pool about the user
questions=(
  "오늘 하루 기분은 어때요? 1~10점으로 표현한다면?"
  "요즘 가장 관심 있는 분야나 주제가 뭐예요?"
  "오늘 가장 기억에 남는 순간이 있었나요?"
  "최근에 새로 배운 것이 있다면 뭐예요?"
  "지금 가장 하고 싶은 것은 뭐예요?"
  "요즘 즐겨 듣는 음악이나 좋아하는 아티스트가 있어요?"
  "가장 좋아하는 음식은 뭐예요? 오늘 뭐 먹었어요?"
  "최근에 본 영화나 드라마 중 추천할 만한 게 있어요?"
  "스트레스 받을 때 주로 어떻게 풀어요?"
  "인생에서 가장 중요하게 생각하는 가치가 뭐예요?"
  "어릴 때 꿈이 뭐였어요? 지금은 달라졌나요?"
  "가장 가보고 싶은 여행지가 있다면 어디예요?"
  "요즘 고민이 있다면 하나만 말해줄 수 있어요?"
  "자신의 장점을 세 가지만 꼽는다면?"
  "가장 최근에 웃었던 이유가 뭐예요?"
  "주말에 주로 뭘 하면서 시간을 보내요?"
  "좋아하는 계절은 언제예요? 이유도 알려줘요!"
  "지금 당장 초능력 하나를 가질 수 있다면 뭘 고르겠어요?"
  "가장 소중한 사람에게 하고 싶은 말이 있다면?"
  "5년 후 자신의 모습을 상상해본다면 어떤 모습이에요?"
  "요즘 읽고 있는 책이 있어요?"
  "자기 전에 주로 뭘 해요?"
  "가장 행복했던 기억 하나만 공유해줄 수 있어요?"
  "오늘 감사한 것 하나만 떠올려본다면?"
  "나를 한 단어로 표현한다면 뭐라고 할 거예요?"
  "가장 좋아하는 색깔은 뭐예요? 이유가 있어요?"
  "요즘 건강 관리는 잘 하고 있어요?"
  "인생 좌우명이나 자주 떠올리는 문구가 있어요?"
  "반복되는 일상에서 소소한 즐거움은 뭐예요?"
  "지금 이 순간 가장 감사한 것은 뭐예요?"
)

total=${#questions[@]}
idx=$(( (next - 1) % total ))
question="${questions[$idx]}"

# Save state
tmp_file="$(mktemp)"
printf '{\n  "current": %s,\n  "updatedAt": "%s"\n}\n' "${next}" "${updated_at}" > "${tmp_file}"
mv "${tmp_file}" "${STATE_FILE}"

# Get bot token
bot_token="$(
  sed -n "/\"${ACCOUNT_ID}\"[[:space:]]*:/,/^[[:space:]]*}/ s/.*\"botToken\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" "${CONFIG_FILE}" \
    | head -n 1
)"

if [ -z "${bot_token}" ]; then
  echo "Missing Telegram bot token for account ${ACCOUNT_ID}" >&2
  exit 1
fi

curl -fsS --max-time 30 \
  -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=💬 질문 #${next}: ${question}" \
  >/dev/null
