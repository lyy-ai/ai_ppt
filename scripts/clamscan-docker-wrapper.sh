#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: clamscan-docker-wrapper.sh [clamscan options] FILE" >&2
  exit 2
fi

exec docker exec ppt-master-clamav clamdscan "$@"
