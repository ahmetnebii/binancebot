#!/bin/sh
# Veri klasörünü (veritabanı + şifreleme ana anahtarı) bot kullanıcısına verir ve
# uygulamayı root olmayan kullanıcıyla başlatır (ek paket gerektirmez).
set -e
DATA="${BB_DATA_DIR:-/data}"
mkdir -p "$DATA"
if [ "$(id -u)" = "0" ]; then
  chown -R bot:bot "$DATA"
  chmod 700 "$DATA"
  export HOME=/home/bot
  exec python -c 'import os, pwd, sys; u = pwd.getpwnam("bot"); os.setgroups([]); os.setgid(u.pw_gid); os.setuid(u.pw_uid); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
fi
exec "$@"
