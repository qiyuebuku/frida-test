#!/system/bin/sh
# Historical unsafe owner-data experiment; never called by deployment.
set -eu
PACKAGE=com.hexin.plat.android
OWNER_CE=/data/user/0/$PACKAGE
OWNER_DE=/data/user_de/0/$PACKAGE
TARGET_CE=/data/user/10/$PACKAGE
TARGET_DE=/data/user_de/10/$PACKAGE
TMP=/data/local/tmp

am force-stop --user 0 "$PACKAGE"
am force-stop --user 10 "$PACKAGE"
rm -f "$TMP/ths_user10_before_sync_ce.tar" \
  "$TMP/ths_user10_before_sync_de.tar" \
  "$TMP/ths_owner_snapshot_ce.tar" \
  "$TMP/ths_owner_snapshot_de.tar"
tar -cf "$TMP/ths_user10_before_sync_ce.tar" -C "$TARGET_CE" .
tar -cf "$TMP/ths_user10_before_sync_de.tar" -C "$TARGET_DE" .
tar -cf "$TMP/ths_owner_snapshot_ce.tar" -C "$OWNER_CE" .
tar -cf "$TMP/ths_owner_snapshot_de.tar" -C "$OWNER_DE" .
find "$TARGET_CE" -mindepth 1 -delete
find "$TARGET_DE" -mindepth 1 -delete
tar -xf "$TMP/ths_owner_snapshot_ce.tar" -C "$TARGET_CE"
tar -xf "$TMP/ths_owner_snapshot_de.tar" -C "$TARGET_DE"
chown -R 1010167:1010167 "$TARGET_CE" "$TARGET_DE"
restorecon -RF "$TARGET_CE" "$TARGET_DE"
sync
du -sh "$TARGET_CE"
echo canary-sync-complete
