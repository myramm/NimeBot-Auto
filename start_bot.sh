#!/usr/bin/env bash
set -e

cd /root
export PYTHONPATH=/root:$PYTHONPATH

# Stop old instance if running
PID=$(pgrep -f "src/master_bot.py" || true)
if [ -n "$PID" ]; then
    echo "Stopping existing Master Bot process (PID $PID)..."
    kill -9 $PID || true
    sleep 1
fi

echo "Starting Master Bot daemon..."
nohup python3 -u /root/src/master_bot.py </dev/null > /root/master_bot.log 2>&1 &
disown
sleep 2

NEW_PID=$(pgrep -f "src/master_bot.py" || true)
if [ -n "$NEW_PID" ]; then
    MEM=$(ps -o rss= -p $NEW_PID | awk '{print int($1/1024) " MB"}')
    echo "✅ Master Bot started successfully! PID: $NEW_PID (Memory: $MEM)"
else
    echo "❌ Failed to start Master Bot! Check /root/master_bot.log"
    cat /root/master_bot.log
    exit 1
fi
