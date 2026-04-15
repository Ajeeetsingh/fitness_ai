#!/bin/bash

# Load .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Use ENV variables
APP_NAME="${APP_NAME:-fitness-api}"
APP_MODULE="${APP_MODULE:-app.main:app}"
APP_PORT="${APP_PORT:-9001}"
WORKERS="${WORKERS:-2}"
TIMEOUT="${TIMEOUT:-120}"

PID_FILE="/tmp/${APP_NAME}.pid"
LOG_FILE="logs/${APP_NAME}.log"

start() {
    echo "Starting $APP_NAME ..."

    if [ -f "$PID_FILE" ]; then
        echo "$APP_NAME is already running (PID: $(cat $PID_FILE))"
        exit 1
    fi

    # Activate correct venv
    source venv/bin/activate 2>/dev/null || true

    nohup gunicorn $APP_MODULE \
        -k uvicorn.workers.UvicornWorker \
        --bind 0.0.0.0:$APP_PORT \
        --workers $WORKERS \
        --timeout $TIMEOUT \
        --log-file $LOG_FILE \
        --access-logfile $LOG_FILE \
        > /dev/null 2>&1 &

    echo $! > "$PID_FILE"
    echo "$APP_NAME started (PID: $(cat $PID_FILE))"
}

stop() {
    echo "Stopping $APP_NAME ..."
    if [ ! -f "$PID_FILE" ]; then
        echo "$APP_NAME is not running."
        exit 1
    fi
    kill "$(cat $PID_FILE)"
    rm -f "$PID_FILE"
    echo "$APP_NAME stopped."
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if [ -f "$PID_FILE" ]; then
        echo "$APP_NAME is running (PID: $(cat $PID_FILE))"
    else
        echo "$APP_NAME is NOT running."
    fi
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}" ;;
esac
