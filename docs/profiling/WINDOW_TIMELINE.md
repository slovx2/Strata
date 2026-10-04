本轮固定 `pcie_frac=0.25`，不再扫描其他取值。目标是测量真实 decode 验证窗口的并行关系，不能从聚合耗时倒推时间线。

## 2026-10-04 实测结果

通过全部 11 个独立请求，6 个正式请求共抽取 18 个窗口。数据位于 `profile-results/window-timeline-20261004/`；原始服务日志与配置未发布。首次诊断发现默认采用 kernel 搬运，随后补充分界时间戳重新完整采集；本报告只使用第二轮数据。

| 输入 | 窗口总耗时中位 ms | GPU 专家搬运中位 ms | GPU 等待中位 ms | 搬运逻辑量中位 MiB |
| --- | ---: | ---: | ---: | ---: |
| 网络 | 37.36 | 4.89 | 5.52 | 65.71 |
| 数学 | 42.63 | 10.30 | 4.96 | 118.66 |
| 中文写作 | 35.02 | 7.28 | 4.08 | 91.78 |

各列分别取中位数，不可相加；不同窗口 T 与产出不同，不能据此直接排名生成吞吐。正式请求的 prefill 约 1658–1661 tok/s、decode 约 52.0–62.9 tok/s，均带诊断插桩，不替代此前无插桩基线。

页面默认展示数学请求 7 的第 6 个窗口（T=4，产出 2 token）：

- 总计 43.195 ms；verify 主机区间 41.042 ms，draft 2.006 ms。
- GPU 非等待计算阶段 17.824 ms，专家搬运 16.826 ms（184.35 MiB 逻辑量），等待合计 5.969 ms。
- 等待细分：waitA 1.868 ms，waitB 标志 0.125 ms，waitCPU 3.976 ms。
- CPU expert_pool 合计 22.893 ms，与 GPU 工作重叠，不能加到 GPU 总耗时上。

旧 waitB 口径在这个窗口应约 16.951 ms，其中绝大部分是实际搬运内核，而不是单独等待信号。这是本次最关键的归因修正。

另一个热点是 prepare：18 个样本中 **6 个**耗时 12.97–14.64 ms。源码里这一段包含 `apply_pending(true)` 的自适应专家缓存换入等待 / 提交 / 驻留表更新，优先考虑下一步细分定位。当前没有 prepare 内部逐项时间戳，不能直接断言全部由该函数造成。某些窗口的 adapt_join 还会等待约 0.75–5.31 ms；缓存维护与重叠时机值得关注，但这里没有通过关闭缓存或改变调度进行因果对照。

六个正式请求的专家文件读取均为 0 MB，因此本轮暖机测试未观察到专家 SSD 读取瓶颈；不代表冷启动、swap、内存压力或其他上下文长度下也没有 I/O 问题。CPU/GPU 对齐误差半宽为 17.16–32.81 µs。

后续优化优先检查缓存换入同步、减少关键路径上的专家搬运；保持 `pcie_frac=0.25`。更快 CPU 只能消除未被其他工作覆盖的等待部分，不能把所有 expert_pool 时间都当作可节省的总延迟。

## 采集设计

- 独立诊断可执行文件：只重编译 `verify.cpp`、`expert_source.cpp`、`generate.cpp`，复用部署版本的 CUDA 库；不覆盖正式源代码、build 或 binary。
- 部署基底：`7ebaacf1a0674cf31046be441beeb8397bb6c22e`，RTX 4080 16GB，i5-13600KF，WSL 内存上限 56GB。
- SC117 IQ3_S，MTP 开启，32K 容量上限，9 个专家工作线程。正式配置仍为 256K，保持停止、手动启动。
- 两次暖机，网络 / 数学 / 中文写作各一次条件化请求和两次 192-token 独立请求，输入均为 4096 token；前缀缓存关闭。
- 每个请求仅抽样窗口下标 5、20、40；最多 16 个请求。抽样位置确定，不是全请求无偏采样；T 与实际产出随 MTP 接受情况变化。
- 启用条件：`STRATA_WINDOW_TIMELINE_PATH`。未启用时不保存窗口数据、不增加 GPU 分界时间戳。当前仅支持单 GPU、48 层、unsplit verifier。
- GPU 原有 `%globaltimer` 阶段时间戳；额外一个每层 waitB 分界点，把完成信号等待与专家搬运 kernel 拆开。保留原 CUDA Graph。
- CPU 使用 steady_clock。每个抽样窗口由一次设备 stamp + 同步校准两种时钟；校准区间半宽作为对齐误差上界，显示在页面中。窗口内不另行修正时钟漂移。
- DMA 模式另用拷贝流起止 stamp；当前实际为默认 `auto → kernel` 模式，所以专家搬运在 compute stream 内。未改变模式来制造更漂亮的并行图。

## 关键统计口径

旧 `waitB` 统计覆盖：等待完成标志 → `fetch_blobs` → `rebase_ptrs`。在 kernel 模式下，它包含了实际专家搬运，**不能全部叫作 GPU 空等**。本次新增 slot 25 将其拆为：

1. stamp 20 → 25：waitB 完成标志等待。
2. stamp 25 → 21：GPU 专家搬运 + 指针重设。

当前简化图将第二段并入 GPU 的绿色工作块，悬停可查看搬运细节；原始数值数据仍保留展开记录，但合并时只使用 GPU 原始轨道，不重复累计。即使某层专家搬运计数为 0，CUDA Graph 的空操作 kernel 仍有开销。字节数为计划搬运的专家 blob 逻辑量，不是 PCIe 硬件计数器，也不包含所有其他 PCIe 流量。

`expert_pool` 是主机等待一组专家任务完成的墙钟区间，包含计算、内存访问与工作线程同步；不是 CPU 所有核心用时之和。GPU 非等待阶段同样包含访存和阶段开销，不是纯 FLOPs 时间。两者处理的专家数量可能不同，不能用该图推算“CPU 比 GPU 慢多少倍”。

原始数据给出 prepare、verify、commit_emit、draft、adapt_join 的主机调用包络；简化图将其保留在悬浮说明中。GPU 细分仅覆盖 verify，MTP draft、commit 没有逐 kernel GPU 测量；GPU 轨道的空白不能判为 GPU 空闲。各层 gap 与其他未归因时间保留，不强行凑成某个已知阶段。

页面显示真实单次同步窗口，汇总表只对各指标分别统计中位/最小/最大值，不把中位数拼成同步执行图。时钟校准、额外 GPU stamp、CPU trace 和文件写入有扰动；这些数据用于定位热点，不用于替代无插桩吞吐基准。trace 文件写入发生在所显示窗口结束之后，也可能影响下一窗口的调度。

## 隐私

只保存固定阶段标签、相对时间、层号、数量、字节数、验证宽度及产出数量。没有提示词、生成文本、token ID、专家 ID、激活值、聊天记录、API key 或内容 hash。测试输入由固定合成用例生成。HTML 导出使用字段白名单，不发布服务配置、engine 日志或整个运行目录。

## 重现

将上述三个源文件和 `include/strata/core/window_timeline.hpp` 放到 `/mnt/data` 上的独立诊断构建目录，保持原相对路径；随后在目标机器运行：

```sh
python tools/build_window_timeline.py --base /mnt/data/strata-deploy/Strata --out /mnt/data/strata-deploy/window-timeline-build
python tools/capture_window_timeline.py --source-root /mnt/data/strata-deploy/Strata --config /mnt/data/strata-deploy/strata-sc117-iq3_s.json --exe /mnt/data/strata-deploy/window-timeline-build/strata-timeline --out /mnt/data/strata-deploy/window-timeline-capture-new
```

通过现有 `direct-env.sh` 运行，避免系统代理。capture 要求输出目录未存在、正式服务停止、没有另一份引擎；退出时关闭诊断引擎，确认正式配置未改变。一次加载完成全部请求。

```sh
python3 tools/test_window_timeline.py
python3 tools/render_window_timeline.py profile-results/window-timeline-20261004 window-timeline-site/index.html
```

生成单文件 HTML，无 CDN 或外部脚本依赖。支持窗口选择、缩放、平移、悬停、点击定位层、耗时排序、下载数值数据；表格提供精确值。

## 查看服务

`strata-window-timeline.service` 只发布 `/mnt/data/strata-deploy/window-timeline-site`，绑定 `100.64.141.92:8766`，由 systemd 管理，日志位于 `/mnt/data/strata-deploy/window-timeline-web.log`。服务不设开机启动；主机重启后需手动启动。

```sh
ssh song-pc-linux 'systemctl stop strata-window-timeline'
ssh song-pc-linux 'systemctl start strata-window-timeline'
```

仅启动静态报告服务，不启动模型服务。

## 验证记录

独立 native 编译 / 链接成功，11 个合成请求完成且输出非空、前缀复用为 0，退出后配置字节未变。18 个正式窗口通过时间戳单调性、48 层 / 96 个 DMA 槽结构、窗口边界与搬运计数校验；5 个归因单元测试通过。

Worker 浏览器直接通过 Tailscale 打开报告，验证 18 个选项、48 层表格、切换、逐层点击缩放、平移、排序、重置与画布显示，未发现页面脚本错误（浏览器自身翻译扩展有联网报错）。HTTP 200；systemd 静态服务 active、仅 linked，模型服务 inactive。未做输出质量等价性评测，也未对正式 256K 配置重新测速。

## 简化显示（2026-10-04）

主图只保留 GPU / CPU 两条轨道。同一设备相邻且同为工作状态的阶段合并；GPU 计算与搬运统一为绿色，CPU 工作统一为蓝色。已记录等待完全不填色，仅用细虚线边框保留悬停区域；未知 / 未细分区间用灰色斜纹，不能把它们当作已知等待。CPU 轨道包含主机协调等待和专家线程池工作，不表示整颗 CPU 的利用率。

合并严格保留实际间隙，不将间隙插补为工作或等待。主图不显示细阶段标签；悬浮提示给出等待原因、当前步骤、所属层、合并区间细分与已有搬运计数。逐层表格、样本分布与性能数据默认折叠。

本次只改变报告展示，未重新运行模型或修改推理参数。浏览器校验全部 18 个窗口：合并后两行完整覆盖窗口且无重叠，工作 / 等待总时长与原始记录一致；透明等待提示、缩放、平移和窗口切换可用。
