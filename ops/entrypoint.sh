#!/bin/sh
set -e

if [ ! -w /data ]; then
    echo "Le volume /data n'est pas inscriptible." >&2
    exit 1
fi

python manage.py check

exec "$@"
