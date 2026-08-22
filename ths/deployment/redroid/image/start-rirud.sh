#!/system/bin/sh
set -eu

export PATH=/sbin:/system/bin:/system/xbin:/vendor/bin
i=0
while [ "$i" -lt 50 ] && [ ! -e /sbin/.magisk ]; do
    /system/bin/sleep 0.2
    i=$((i + 1))
done

mkdir -p /sbin/.magisk/modules/riru-core/lib64
cp /data/adb/modules/riru-core/lib64/libriru.so \
    /sbin/.magisk/modules/riru-core/lib64/libriru.so

MOD=/data/adb/modules/riru_lsposed
mkdir -p /sbin/.magisk/modules/riru_lsposed/riru/lib64
cp "$MOD/system/lib64/liblspd.so" \
    /sbin/.magisk/modules/riru_lsposed/riru/lib64/liblspd.so
if [ -x "$MOD/daemon" ]; then
    (cd "$MOD" && "$MOD/daemon" --from-service >/dev/null 2>&1 &)
fi

cd /data/adb/modules/riru-core
exec /system/bin/app_process \
    -Djava.class.path=/data/adb/modules/riru-core/rirud.apk \
    /system/bin --nice-name=rirud riru.Daemon 0 /sbin libndk_translation.so
