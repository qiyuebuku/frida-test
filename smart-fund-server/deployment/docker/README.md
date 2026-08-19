# Smart Fund Docker Compose 生产运行时

该目录只管理 `smart-fund-server`、Milvus 和 etcd。THS Android Emulator、ADB、Hook、Bridge、
Load Balancer 与 Watchdog 继续由宿主机 systemd 管理，不进入 Compose。

所有 Python 服务使用同一个 `smart-fund-server:<git-commit>` 镜像，通过不同 CLI command 区分
API、Persist、Scheduler、三个采集 Worker、实时流和 KG Worker。PostgreSQL 使用现有外部实例，
Redis 暂时使用宿主机实例；容器统一采用 host network，直接访问宿主机 49350/49500。

不要手工运行本目录的 Compose 文件。正式入口始终是工作区根目录：

```bash
./deploy.sh production
./deploy.sh production --component server-api
./deploy.sh production --component server-workers
```

首次迁移由部署器停止旧 Smart Fund systemd 单元，复用原 Milvus/etcd 数据目录启动 Compose，
完成 API、实时流和容器状态验收后才写入迁移标记。迁移失败会执行 `docker compose down` 并恢复
旧 `smart-fund-collector.target`。数据库、Redis、Milvus 数据和 THS Android 数据不会被删除。

`kg-graph` 位于 `manual` profile，不随默认栈启动。需要时由受控运维命令单独启动。
