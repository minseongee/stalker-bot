#!/bin/bash
# macOS에서 matplotlib/mplfinance가 올바른 expat 라이브러리를 찾도록 설정
export DYLD_LIBRARY_PATH="$(brew --prefix expat)/lib:$DYLD_LIBRARY_PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "========================================="
echo "  Stalker Bot 시작"
echo "========================================="

# FastAPI 서버를 백그라운드에서 실행
echo "[서버] FastAPI 시작 중... (http://localhost:8000)"
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000 &
SERVER_PID=$!

# 서버가 뜰 때까지 잠깐 대기
sleep 1
echo "[서버] PID $SERVER_PID — 실행 중"
echo ""

# Discord 봇을 포그라운드에서 실행
echo "[봇] Discord 봇 시작 중..."
python3 main.py

# 봇이 종료되면 서버도 함께 종료
echo ""
echo "[종료] 서버를 종료합니다..."
kill $SERVER_PID 2>/dev/null
