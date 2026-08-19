#!/system/bin/sh
# Historical production experiment; never called by deployment.
set -eu
PACKAGE=com.hexin.plat.android
TARGET_CE=/data/user/10/$PACKAGE
TARGET_DE=/data/user_de/10/$PACKAGE
TMP=/data/local/tmp
am force-stop --user 10 "$PACKAGE"
find "$TARGET_CE" -mindepth 1 -delete
find "$TARGET_DE" -mindepth 1 -delete
tar -xf "$TMP/ths_user10_before_sync_ce.tar" -C "$TARGET_CE"
tar -xf "$TMP/ths_user10_before_sync_de.tar" -C "$TARGET_DE"
chown -R 1010167:1010167 "$TARGET_CE" "$TARGET_DE"
restorecon -RF "$TARGET_CE" "$TARGET_DE" >/dev/null
sync
du -sh "$TARGET_CE"
echo canary-restore-complete
