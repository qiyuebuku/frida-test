ARG BASE_IMAGE=redroid/redroid@sha256:60b0810684be4578733a847be3314c50b70f73bc92405b5a627ebe9b633ebb5e
FROM ${BASE_IMAGE}

COPY native-bridge-overlay/ /
COPY magisk-bootstrap-overlay/ /
COPY --chmod=0644 riru-lsposed-redroid-bootstrap.tar.gz /opt/ths/riru-lsposed-redroid-bootstrap.tar.gz
COPY --chmod=0644 libriruloader.so /system/lib64/libriruloader.so
COPY --chmod=0644 ths.apk /opt/ths/ths.apk
COPY --chmod=0644 ths-hook.apk /opt/ths/ths-hook.apk
COPY --chmod=0755 docker-entrypoint.sh /opt/ths/docker-entrypoint.sh
COPY --chmod=0755 ths-runtime-manager.sh /system/bin/ths-runtime-manager.sh
COPY --chmod=0755 ths-healthcheck.sh /system/bin/ths-healthcheck.sh
COPY --chmod=0755 start-rirud.sh /system/bin/start-rirud.sh
COPY --chmod=0644 init.ths-runtime.rc /system/etc/init/init.ths-runtime.rc
COPY --chmod=0644 bootanim.riru.rc /system/etc/init/bootanim.rc

ENTRYPOINT ["/system/etc/init/magisk/busybox", "sh", "/opt/ths/docker-entrypoint.sh"]
ARG BUILD_REVISION
LABEL org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.source="https://github.com/qiyuebuku/frida-test"
HEALTHCHECK --interval=2s --timeout=2s --start-period=2m --retries=4 \
    CMD ["/system/bin/ths-healthcheck.sh"]
