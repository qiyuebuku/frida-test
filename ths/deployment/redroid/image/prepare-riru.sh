#!/system/bin/sh
set -eu

export PATH=/sbin:/system/bin:/system/xbin:/vendor/bin
i=0
while [ "$i" -lt 50 ] && [ ! -e /sbin/.magisk ]; do
    /system/bin/sleep 0.2
    i=$((i + 1))
done
[ -e /sbin/.magisk ] || exit 1

# The Redroid Magisk mirror is a tmpfs. Materialize the two native libraries
# there before Magisk runs the modules' standard post-fs-data scripts. Those
# scripts remain the only owners of the Riru and LSPosed daemon lifecycle.
mkdir -p /sbin/.magisk/modules/riru-core/lib64
cp /data/adb/modules/riru-core/lib64/libriru.so \
    /sbin/.magisk/modules/riru-core/lib64/libriru.so

MOD=/data/adb/modules/riru_lsposed
mkdir -p /sbin/.magisk/modules/riru_lsposed/riru/lib64
cp "$MOD/system/lib64/liblspd.so" \
    /sbin/.magisk/modules/riru_lsposed/riru/lib64/liblspd.so
