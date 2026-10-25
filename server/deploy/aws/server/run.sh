#!/bin/bash
# EC2 안에서 돈다(deploy.py 가 SSM 으로 보낸다). 소스 묶음을 받아 도커로 빌드하고 컨테이너를 바꾼다.
# 비밀값은 Parameter Store 에서 바로 파일로 받고, 화면에 찍지 않는다.
# 필요한 환경 변수: PROJECT REGION TAG ARTIFACT_BUCKET
set -euo pipefail
: "${PROJECT:?}" "${REGION:?}" "${TAG:?}" "${ARTIFACT_BUCKET:?}"

ROOT=/opt/talkto
SRC="$ROOT/src/$TAG"
NET="$PROJECT-net"
SUBNET=172.30.0.0/24
AI_IP=172.30.0.10   # AI 컨테이너 고정 IP(인스턴스 메타데이터를 막으려고). 나머지는 .128/25 에서 받는다.
mkdir -p "$SRC/be" "$SRC/ai" "$ROOT/env"
chmod 700 "$ROOT/env"
# 성공하든 실패하든 끝나면 설정 파일(비밀값)을 지운다. 컨테이너는 시작할 때 이미 읽었다.
trap 'rm -f "$ROOT/env/"*.env' EXIT
redact() { sed -E 's/((KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*=)[^ ]*/\1***/g'; }

echo "[1/6] 소스 받기 ($TAG)"
aws s3 cp "s3://$ARTIFACT_BUCKET/$TAG/be.tar.gz" "$SRC/be.tar.gz" --region "$REGION" --only-show-errors
aws s3 cp "s3://$ARTIFACT_BUCKET/$TAG/ai.tar.gz" "$SRC/ai.tar.gz" --region "$REGION" --only-show-errors
tar -xzf "$SRC/be.tar.gz" -C "$SRC/be"
tar -xzf "$SRC/ai.tar.gz" -C "$SRC/ai"

echo "[2/6] 설정 받기"
umask 077
for name in backend ai; do
  aws ssm get-parameter --region "$REGION" --name "/$PROJECT/prod/$name-env" --with-decryption \
    --query Parameter.Value --output text > "$ROOT/env/$name.env"
done
umask 022

echo "[3/6] 이미지 빌드"
# 빌드 기록은 파일로 둔다(SSM 은 출력 앞쪽 24KB 만 돌려줘서, 그대로 찍으면 실패 이유가 잘린다)
build() {
  local name=$1 dir=$2 logf="$ROOT/build-$1.log"
  if ! docker build --pull -t "$PROJECT-$name:$TAG" "$dir" > "$logf" 2>&1; then
    echo "$name 이미지 빌드 실패. 마지막 기록:"; tail -n 40 "$logf" | redact; exit 1
  fi
  echo "  $name 빌드 완료"
}

# ── 인스턴스 메타데이터(IAM 역할 자격 증명) 막기 ──────────────────────
# 백엔드만 S3 를 위해 역할이 필요하다. AI 컨테이너(올린 녹음을 다룬다)와 빌드(docker0)는 못 읽게 한다.
command -v iptables >/dev/null || dnf install -y -q iptables-nft
RULES="iptables -C DOCKER-USER -s $AI_IP/32 -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables -I DOCKER-USER -s $AI_IP/32 -d 169.254.169.254/32 -j DROP; iptables -C DOCKER-USER -i docker0 -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables -I DOCKER-USER -i docker0 -d 169.254.169.254/32 -j DROP"
sh -c "$RULES"
# 다시 켜질 때도(재부팅·도커 재시작) 같은 규칙을 넣는다. 앞의 '-' 는 실패해도 도커를 멈추지 않게.
mkdir -p /etc/systemd/system/docker.service.d
printf '[Service]\nExecStartPost=-/bin/sh -c "%s"\n' "$RULES" > /etc/systemd/system/docker.service.d/10-ai-no-imds.conf
systemctl daemon-reload

build ai "$SRC/ai"
build backend "$SRC/be"

echo "[4/6] 컨테이너 바꾸기"
# 지금 돌고 있는 버전. 새 버전이 확인을 통과하지 못하면 이것으로 되돌린다.
PREV_AI=$(docker inspect -f '{{.Config.Image}}' "$PROJECT-ai" 2>/dev/null || true)
PREV_BE=$(docker inspect -f '{{.Config.Image}}' "$PROJECT-backend" 2>/dev/null || true)
# 서브넷이 정해진 네트워크여야 AI 에 고정 IP 를 줄 수 있다. 예전 네트워크면 새로 만든다.
if [ "$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$NET" 2>/dev/null || true)" != "$SUBNET" ]; then
  docker rm -f "$PROJECT-ai" "$PROJECT-backend" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker network create --subnet "$SUBNET" --ip-range 172.30.0.128/25 "$NET" >/dev/null
fi
log() {
  echo --log-driver awslogs --log-opt "awslogs-region=$REGION" --log-opt "awslogs-group=/$PROJECT/$1" \
    --log-opt "awslogs-stream=$1-$TAG" --log-opt mode=non-blocking --log-opt max-buffer-size=4m
}
# 둘 다 루트가 아니고 1024 위 포트를 쓴다: 권한(capability)을 모두 빼고 권한 상승도 막는다.
HARDEN="--cap-drop ALL --security-opt no-new-privileges:true"

start_containers() {
  local ai_image=$1 be_image=$2
  # 백엔드를 먼저 내린다. AI 를 먼저 내리면 백엔드가 돌리던 작업(전사·목소리 만들기)이 그 순간
  # 'AI 오류'로 실패해, 다시 켤 때 끊긴 작업 정리(INTERRUPTED: 목소리 회사에 남은 목소리 지우기,
  # 다시 만들기 실패로 세지 않기)를 거치지 못했다.
  docker rm -f "$PROJECT-backend" >/dev/null 2>&1 || true
  docker rm -f "$PROJECT-ai" >/dev/null 2>&1 || true
  # AI 서버는 밖으로 열지 않는다(백엔드만 같은 도커 네트워크에서 부른다). 4GB 서버: 목소리 묶기에 넉넉히.
  # MALLOC_ARENA_MAX=2: 스레드마다 메모리 풀을 따로 키우지 않게 한다(스레드풀로 도는 파이썬 서버의
  # 흔한 메모리 불어남. 목소리 묶기가 2.5GB 를 넘어 강제 종료된 일이 있었다, 2026-10-18).
  docker run -d --name "$PROJECT-ai" --network "$NET" --ip "$AI_IP" --restart unless-stopped \
    --env-file "$ROOT/env/ai.env" -e PORT=8001 -e MALLOC_ARENA_MAX=2 \
    --memory 2560m --memory-swap 5g --pids-limit 512 $HARDEN $(log ai) \
    "$ai_image" >/dev/null
  docker run -d --name "$PROJECT-backend" --network "$NET" --restart unless-stopped \
    --env-file "$ROOT/env/backend.env" -e PORT=8000 -p 80:8000 \
    --memory 1536m --memory-swap 3g --pids-limit 256 $HARDEN $(log backend) \
    "$be_image" >/dev/null
}

# 백엔드·AI 가 응답하고, 백엔드가 IAM 역할(S3)을 읽을 수 있는지
healthy() {
  local ok=0 ai=0
  for i in $(seq 1 60); do
    if curl -fsS -o /dev/null http://127.0.0.1/health; then ok=1; break; fi
    sleep 2
  done
  if [ "$ok" != 1 ]; then
    echo "  백엔드가 응답하지 않아요. 최근 로그:"; docker logs --tail 40 "$PROJECT-backend" 2>&1 | redact
    return 1
  fi
  for i in $(seq 1 60); do
    if docker exec "$PROJECT-backend" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://$PROJECT-ai:8001/health').status==200 else 1)" 2>/dev/null; then ai=1; break; fi
    sleep 3
  done
  if [ "$ai" != 1 ]; then
    echo "  AI 서버가 응답하지 않아요. 최근 로그:"; docker logs --tail 40 "$PROJECT-ai" 2>&1 | redact
    return 1
  fi
  docker exec "$PROJECT-backend" python -c "import boto3,sys; sys.exit(0 if boto3.Session().get_credentials() else 1)" \
    || { echo "  백엔드가 IAM 역할을 못 읽어요(S3 저장이 안 돼요)."; return 1; }
}

start_containers "$PROJECT-ai:$TAG" "$PROJECT-backend:$TAG"
# 예전 이름의 네트워크가 남아 있으면 치운다
docker network rm "$PROJECT" >/dev/null 2>&1 || true

echo "[5/6] 확인"
if ! healthy; then
  if [ -n "$PREV_AI" ] && [ -n "$PREV_BE" ] && [ "$PREV_BE" != "$PROJECT-backend:$TAG" ]; then
    echo "  새 버전이 확인을 통과하지 못해 이전 버전($PREV_BE)으로 되돌려요."
    start_containers "$PREV_AI" "$PREV_BE"
    if healthy; then echo "  이전 버전으로 되돌렸어요(서비스는 그대로 돌아요)."; else echo "  이전 버전도 응답하지 않아요."; fi
  fi
  exit 1
fi
if docker exec "$PROJECT-ai" python -c "import urllib.request as u; u.urlopen(u.Request('http://169.254.169.254/latest/api/token', method='PUT', headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'}), timeout=3)" >/dev/null 2>&1; then
  echo "  경고: AI 컨테이너가 아직 인스턴스 메타데이터에 닿아요."
else
  echo "  AI 컨테이너 → 인스턴스 메타데이터: 막힘(정상)"
fi

echo "[6/6] 정리"
# 지금 것과 바로 전 것만 남긴다(디스크 30GB)
ls -1dt "$ROOT"/src/* 2>/dev/null | tail -n +3 | xargs -r rm -rf
for repo in "$PROJECT-ai" "$PROJECT-backend"; do
  docker images "$repo" --format '{{.Tag}}' | sort -r | tail -n +3 | sed "s#^#$repo:#" | xargs -r docker rmi >/dev/null 2>&1 || true
done
docker image prune -f >/dev/null 2>&1 || true
docker builder prune -f --filter until=72h >/dev/null 2>&1 || true
df -h / | awk 'NR==2 {print "  디스크: " $3 " 사용 / " $2}'
echo "배포 완료: $TAG"
