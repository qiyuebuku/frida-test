 1. 帮忙将本次逆向过程中的经验总结到 /home/yuyang/frida-test/.claude/skills/reverse-app-skill 中的知识库里，需要注意的是 这个       
  skill 他是面向通用app的逆向skill，而不是专门为了特定场景设计的，所以你在总结的时候需要注意，不要写死某种固定的，还有就是看      
  看是否需要更新：/home/yuyang/frida-test/.claude/skills/reverse-app-skill/SKILL.md

# 生产部署

工作区包含 `ths` 和 `smart-fund-server` 两个可独立部署的组件，统一入口为：

```bash
./deploy.sh production
```

默认读取生产端每个组件上次成功 revision，通过 Git diff 只部署发生变化的组件。也可以显式选择：

```bash
./deploy.sh production --component ths-hook
./deploy.sh production --component ths-runtime
./deploy.sh production --component server
./deploy.sh production --component server-api
./deploy.sh production --component server-scheduler,server-workers
./deploy.sh production --component ths-hook,server
./deploy.sh production --dry-run
```

部署 revision 必须已经提交并推送到 `origin/main`。生产机通过只读 deploy key 执行
`git fetch`/`checkout`；密钥、环境变量、THS APK、AVD 和初始化模板不进入 Git。新机和旧机使用
同一个命令：组件部署器检测缺失状态后自动初始化，但不会自动覆盖已有 Android 用户数据。

生产运行采用混合架构：THS Android/Hook/Bridge 保持宿主机 systemd；`smart-fund-server` 使用
单镜像、多服务 Docker Compose。服务端可以按 `server-api`、`server-persist`、
`server-scheduler`、`server-workers`、`server-ths-stream`、`server-kg` 独立更新；共享代码、
依赖、schema 或部署文件变化自动选择全部服务端组件。
