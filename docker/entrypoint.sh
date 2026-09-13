#!/usr/bin/env sh
# Migrations run here, on every container start, and nowhere else.
# Creating or altering tables by hand in the Supabase console is not a supported
# path on this project -- see the alltech-redesign-design skill, section 2.3.
set -eu

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

if [ "${COLLECTSTATIC:-1}" = "1" ]; then
    echo "[entrypoint] collecting static files"
    python manage.py collectstatic --noinput
fi

echo "[entrypoint] starting: $*"
exec "$@"
