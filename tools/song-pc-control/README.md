# Strata 控制入口

位置：Song PC 的 WSL（`song-pc-linux`）`/root/strata`。模型、二进制、日志和测试数据仍在 `/mnt/data/strata-deploy`；这里不复制大模型。

```bash
cd /root/strata
./start.sh          # 手动启动，并等待模型加载完成（通常几分钟）
./status.sh         # 服务状态、模型是否就绪、上下文容量
./stop.sh          # 停止服务，释放模型占用
./restart.sh       # 修改配置后重启，等待就绪
./logs.sh          # 最近 80 行引擎运行日志
./logs.sh -f       # 持续查看；Ctrl-C 只退出查看
```

服务为 `strata-sc117`，保持手动启动，不启用开机自启。`systemctl is-enabled` 若显示 `linked`，表示服务文件通过链接注册，不是已配置开机启动。停止状态下 `status.sh` 显示 inactive。

正式配置：SC117 IQ3_S、模型名 `qwen3.8-fn`、256K（262144）容量、INT8 KV、MTP 开启、CPU workers=9、`pcie_frac=0.25`。保留弹性 KV 的显存按需增长、RAM 副本保护及图片兼容；未启用混合交换、动态 RAM/VRAM 控制等研究候选。

API 地址：`http://100.64.141.92:8080`；Tailscale 短域名可用时也可使用 `http://song-pc-linux:8080`。监听 `0.0.0.0:8080`，请求 API 需要现有密钥，模型加载完成后再调用。健康状态可用 `curl --noproxy '*' http://100.64.141.92:8080/health` 查看。

`config.json` 是正在使用的配置，权限 600。它含 API 密钥，请勿提交仓库或外发。修改后执行 `restart.sh`。README 和脚本不包含密钥。不要把容量 256K 与每次输入长度混为一谈：短请求只使用少量 KV 显存，余量交给专家缓存，长请求再收回。

```bash
./rollback.sh      # 恢复原生 256K 基线；原来在运行则自动重启，否则保持停止
./use-elastic.sh   # 恢复本次保留的弹性 KV 配置；同样保留原运行/停止状态
```

两项切换使用 `configs/` 内保存的配置快照，会覆盖当前 `config.json` 的手工修改。需要保留改动时先自行备份。

`runtime` 指向已部署的源码/二进制版本；`logs.sh` 根据当前配置自动选择日志，切回基线也不需要改脚本。运行日志用于容量、恢复和计时诊断，关闭 API 全文监控；测试报告只保留合成输入的计数和时间，不记录真实聊天内容。

交付状态：服务保持停止。此前原生测试71/71通过，256K对照及满KV归还图片显存已有记录；不同图片重新借用的补测已取消。按要求不再执行生产API及启停脚本运行验收。
