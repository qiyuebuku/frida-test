#!/system/bin/sh
set -eu
PACKAGE=com.hexin.plat.android
TMP=/data/local/tmp
OWNER_CE_TAR=$TMP/ths_owner_snapshot_ce.tar
OWNER_DE_TAR=$TMP/ths_owner_snapshot_de.tar

test -s "$OWNER_CE_TAR"
test -s "$OWNER_DE_TAR"

for USER_ID in 11 12 13 14 15 16; do
  TARGET_CE=/data/user/$USER_ID/$PACKAGE
  TARGET_DE=/data/user_de/$USER_ID/$PACKAGE
  APP_UID=$((USER_ID * 100000 + 10167))
  am force-stop --user "$USER_ID" "$PACKAGE"
  CE_BACKUP=$TMP/ths_user${USER_ID}_before_sync_ce.tar
  DE_BACKUP=$TMP/ths_user${USER_ID}_before_sync_de.tar
  if [ ! -s "$CE_BACKUP" ]; then
    tar -cf "$CE_BACKUP" -C "$TARGET_CE" .
    tar -cf "$DE_BACKUP" -C "$TARGET_DE" .
  fi
  find "$TARGET_CE" -mindepth 1 -delete
  find "$TARGET_DE" -mindepth 1 -delete
  tar -xf "$OWNER_CE_TAR" -C "$TARGET_CE"
  tar -xf "$OWNER_DE_TAR" -C "$TARGET_DE"
  chown -R "$APP_UID:$APP_UID" "$TARGET_CE" "$TARGET_DE"
  restorecon -RF "$TARGET_CE" "$TARGET_DE" >/dev/null
  echo "synced user=$USER_ID uid=$APP_UID"
done
sync
echo all-clones-synced
