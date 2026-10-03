# Song PC 社区优化首轮盘点

核对日期：2026-10-03。按当前 RTX 4080 16GB、i5-13600KF、WSL 56GB、SC117 IQ3_S、32K 单请求的适用性排序。社区性能数字只代表原作者的机器，不是本机收益承诺。本轮只固定基线并盘点，没有替换引擎、合入优化或改运行配置。

## 固定基线

- Git 提交：`251949c0f1daf1d5193156d535ff1b284151d0bf`。
- 标签：[song-pc-32k-baseline-20261003](https://github.com/slovx2/Strata/tree/song-pc-32k-baseline-20261003)。
- 引擎仍为原部署 0.1.36，来源 `636531421747293942cb1e213ccbeda5fcb52dbe`；二进制 SHA256 `0fc365be01c4947b462e2d29839bb756fe62a711ba98ad99780db089a55f060e`。
- 32K、INT8 KV、MTP `--spec 4`、CPU assist 自动、fused prefill 开、视觉按需、前缀缓存关；默认单轮快测约 22 秒。
- 复核中位数：3842 输入 prefill 1451.9 / decode 44.3；15423 输入 prefill 2227.0 / decode 48.7 tokens/s。不同测试流程不要直接混比，详见 [复核记录](SONG_PC_PREFILL_RECHECK.md)。

这里的优先级是验证顺序。首要目标是得到能归因的改进，不把标题里的百分比相加。

## 优先级 0：先处理会干扰验收的同步与布局问题

这是性能实验的前置组，不把它计作提速。

- [#463](https://github.com/Niko1221/Strata/pull/463)：等待自适应专家迁移完成再更新下一轮执行。0.1.38 已手工纳入，并改为默认等待、`STRATA_ADAPT_NOWAIT=1` 才取消。旧 PR 的 opt-in 开关与最终实现不同。我们尚无这份新实现；它会影响输出路径、MTP 接受率和速度比较，需单独建立修正后对照。
- [#550](https://github.com/Niko1221/Strata/pull/550)：residency table 上传与非阻塞流的先后关系；仍 open。它和 #463 针对不同的复制阶段，不能互相替代。作者未测出明确速度收益。
- [#546](https://github.com/Niko1221/Strata/pull/546)：只有所有层都能走 fused 时才缩减中间缓冲。已由 0.1.38 的 `220e0e8` 覆盖。当前旧源码仍按“任一层支持”判定。不过已逐层核对 SC117 的 48 层：gate/up 类型 18/21/22/23，down 20/42，全部在当前 fused 格式覆盖范围内；不能据此说当前模型已经触发该问题。将它作为后续改布局或换量化的保护条件。

不要拿“取消等待获得的吞吐”与有同步保证的实现混作普通加速收益。也不要因为 GitHub 显示 closed / merged=false，就误判这些手工纳入的修复未被上游采用。

## 优先级 1：减少 prefill 多占的显存，协调分块与流式缓冲

**先 #547，后考虑 #583 的剩余部分。** 当前显存有限，这一组最值得先做小范围验证。

- [#547](https://github.com/Niko1221/Strata/pull/547)，open：让 `bytes_needed()` 与实际分配一致，避免为不需要的中间缓冲多借专家缓存显存。作者例子中 8K 分块少借约 0.31 GiB；小卡可能因此跨过自动分块档位，但已经能容纳当前分块时，不保证明显提速。我们当前尚无修正。
- [#583](https://github.com/Niko1221/Strata/pull/583)，open：按字节预算控制专家流式 ring，并联合选择 chunk/ring。它也修改了 #547 的 `xn/grs` 计数，所以不能直接叠加同一修正。还需保留 #547 关于 `src`/fused 布局的一致性处理。
- #583 的大幅数字来自四卡、长输入和不同分块条件；其 IQ3_S fused 示例中 ring 仍为 512 槽，主要变动来自分块扫描。不能把该 PR 的约 51% 套到本机。
- 本 fork 已有按输入均分 chunks 和细化 auto 扫描的修改。#583 会碰到相同选择逻辑，应做移植而非直接覆盖。#547 与本 fork 头文件也有上下文差异。

验收：同一输入检查实际 chunk、ring、借出显存、专家槽数，再看 prefill/TTFT。先按原有默认快测筛选；收益有迹象再测 16K 单轮。保持相同 32K 容量。

## 优先级 2：小范围的 prefill / decode 内核改进

这一组已被 [0.1.38](https://github.com/Niko1221/Strata/releases/tag/v0.1.38) 采用，可挑选小提交试验，不必一次搬入整个新版。

| 项目 | 作用 | 当前状态与适用性 |
| --- | --- | --- |
| [#413](https://github.com/Niko1221/Strata/pull/413) | DeltaNet 共享同一 key head 的三个 value head，减少重复加载与同步 | 我们缺少新内核。作者 4080 SUPER 的内核约 1.44 倍，整段 prefill 约 +2%，不能混淆。4080 是接近的适用硬件。 |
| [#363](https://github.com/Niko1221/Strata/pull/363) | 减少验证窗口 PCIe 专家调用的空 block 与 kernel launch | 我们缺少。作者 4080 SUPER 的端到端 decode 约 +2%，是否生效取决于本机 GPU/CPU 哪侧限速。 |
| [#415](https://github.com/Niko1221/Strata/pull/415) | IQ4_XS gate/up 的 AVX2 多 token 计算 | 13600KF 属于适用 CPU。已核实 SC117 仅 1/48 层 gate/up 为 IQ4_XS，不能套用整套 IQ4_XS 模型的收益。 |
| [#372](https://github.com/Niko1221/Strata/pull/372) / [#439](https://github.com/Niko1221/Strata/pull/439) | 将专家 gather 与同步批量执行 | 我们已有 #439；#372 是上游选中的替代方案，额外合并 wait/event。必须替换同一代码块，不能两份叠加计收益。当前 fused 路径会降低 MMQ 改进的覆盖面。 |
| [#374](https://github.com/Niko1221/Strata/pull/374) | 首块 PLE 表读取与 layer 0 重叠，增加在途读取 | 我们缺少。对 SSD 首块等待可能有帮助，但社区数据主要来自 Windows + NVMe。本机 WSL + SATA 应先确认 PLE 读取等待；专家常驻 RAM 不等于 PLE 表也全部驻留。 |

建议组内次序：#413 → #363 → #415 → #374；#372 单独处理现有 #439 的替换。在这组内一次只改变一个逻辑，避免多个百分点的小收益被噪声覆盖。

## 优先级 3：专家缓存命中、CPU / PCIe / MTP 配比

**先不换算法，调已有参数；再测试 profile 工具修正。**

- 当前已有 worker 数、`--pool-affinity`、`--pcie-frac`、MTP spec/阈值及 `--expert-profile-save`。当前为 9 workers + 主线程，PCIe probe 约 21.3–21.4 GB/s，PCIe share 0.55；尚未做配比扫描。候选是 worker 数/亲和性、CPU 与 PCIe 分工、MTP 窗口的独立 A/B。WSL 中不能假定显示出的核拓扑等于 Windows 的 P/E 核，应先确认可识别信息。
- [#589](https://github.com/Niko1221/Strata/pull/589)，open：`make_profile --reorder` 让实际路由 trace 排在完整默认 profile 之前。当前只有追加逻辑，完整默认 profile 会让这种 trace 排序失效。我们尚无该修正；改动局限在 Python 工具，容易回退。
- 同方向：[ #407 ](https://github.com/Niko1221/Strata/pull/407) 的 `--adapt-decay` 已进入 0.1.38，`--adapt-tuned` 没被接受，作者在主要配置上没测出明确的每轮时间收益。其自动条件要求缓存覆盖 20–60%；当前 4242 / 24576 约 17%，也不在该范围。
- #589 是离线初始排序，已有 profile-save 是在线学习后的保存，adapt 是运行中迁移；它们互补但会共同影响缓存热度。必须冻结 profile 文件哈希，不能每次边学习边改基准。

先保留同一个 profile 调 CPU/PCIe/MTP；再单独验证 profile。用独立的代码/中文/英文提示做复核，避免只为三条快测输入排序，形成“基准快了，实际请求没变”。

## 优先级 4：收益不确定的小实验，降低预期

- [#500](https://github.com/Niko1221/Strata/pull/500)，open：把中间激活量化分派给 CPU workers。作者在[最新澄清](https://github.com/Niko1221/Strata/pull/500#issuecomment-5966512698)中承认最初 +78% 被缓存和 MTP 波动放大，常规生成收益上限可能不足 1%；低任务数唤醒 worker 还可能更慢。新版本带 `STRATA_POOL_QUANT_THRESH` 阈值。应放在 CPU 配比稳定之后，查看量化耗时，再决定是否试。
- [#443](https://github.com/Niko1221/Strata/pull/443)：两个不同实验不能一起算。`STRATA_GR_DOWN_MAX4` 已被 0.1.38 采用，保留原数值顺序，但 4090 的单次样本仅 +0.30%；另一项 one-warp MMVQ 未被采用，会改变浮点累加和输出，不放进当前首批。
- [#366](https://github.com/Niko1221/Strata/pull/366) DFlash2：仍是可行性 RFC，没有确认适配 Flash-Next 的现成 drafter，也不是现有 MTP 权重换个开关。涉及训练依赖和验证回滚，暂列研究备选，不在这台机器上强行启动训练。

## 优先级 5：长上下文专用，当前 32K 目标后置

| 同方向候选 | 为什么后置 | 依赖、冲突或已具备内容 |
| --- | --- | --- |
| [#378](https://github.com/Niko1221/Strata/pull/378) 弹性 KV | 256K 预留 KV 时更有价值；32K 可回收空间小得多 | 需要单 GPU、专家在 RAM；不能与 `--kv-resident` 流式 KV 路线同时启用。它和按需视觉都调整专家缓存显存，属于集成验证重点，并非已证实不兼容。 |
| [#575](https://github.com/Niko1221/Strata/pull/575) top-k 分段 | 主要解决约 135K cells 以上的寄存器范围问题；32K 不触发核心分支 | “约 +9%”来自超长输入，不作为当前快测收益候选。 |
| [#452](https://github.com/Niko1221/Strata/pull/452) Q4 KV / [#453](https://github.com/Niko1221/Strata/pull/453) 流式 KV 草稿预填充 | 当前固定 INT8 KV、没有 KV streaming | 两者代码已由 architectds 带进当前版本；不能当作新的未合并 PR。启用需改变 KV 精度或存储策略，另开质量/资源对照。 |

## 优先级 6：会话/API、多卡、其他量化分开排

- [#567](https://github.com/Niko1221/Strata/pull/567) 增量 tokenizer：减少相同前缀的重复 BPE 编码。与引擎 KV 缓存互补，但主要作用于重复前缀/多轮；当前单轮引擎 prefill 基准不是它的主要目标。以后单独看 HTTP TTFT，避免把 tokenizer 缓存归为 GPU prefill 加速。
- [#345](https://github.com/Niko1221/Strata/pull/345) / [#571](https://github.com/Niko1221/Strata/pull/571) 都实现 Responses API，属于重叠方案，应比较协议完整性后择一；功能兼容，不是吞吐优化。
- 多卡组：[ #390 ](https://github.com/Niko1221/Strata/pull/390) 是较大组合方案；[ #580 ](https://github.com/Niko1221/Strata/pull/580) 将按层裁剪权重拆出，[ #584 ](https://github.com/Niko1221/Strata/pull/584) 调放置搜索，另有 #578/#598 的 helper/refill、#492 草稿头分卡、#559 并发流水线。不能把组合分支与拆分 PR 重复合入。本机单 4080 暂不排。
- [#478](https://github.com/Niko1221/Strata/pull/478) Q6_K MMQ：当前 SC117 没有 Q6_K 专家，且新 kernels 会占显存，不为当前模型编入。
- [#586](https://github.com/Niko1221/Strata/pull/586) BF16 PLE：偏数值/精度研究，不是提速。表文件约 102GB，对照 FP8 约 51GB；作者也不宣称质量测试证明了更好，不纳入当前速度改造。
- AMD、Intel、Volta/Turing 专项及新硬件支持不适用于这张 sm_89 卡；SM90+ cluster decode 代码虽然已有，也不能靠开关让 RTX 4080 获得对应硬件能力。

## 合并关系速查

| 关系 | 处理原则 |
| --- | --- |
| #547 ↔ #583 | 部分相同计数修正；先小修复，再移植 ring/auto 的剩余差异。 |
| #546 ↔ #547/#583 | 不重复，但都改 prefill 布局与计数；先统一全层 fused 的判定。 |
| 已有 #439 ↔ #372 | 替代/演进关系；保留一种 gather 与事件生命周期实现。 |
| #589 + profile-save + #407 | 目标相同、不同阶段；分开实验、固定 profile，不把各自收益直接相加。 |
| #500 + worker/亲和性调整 | 没有逻辑互斥，但线程唤醒开销和阈值强耦合；先确定线程配置。 |
| #378 ↔ KV streaming | PR 明确限制：`--kv-resident` 模式不适用弹性 KV。 |
| #378 + 按需视觉 | 共用显存借出/回收机制，需验证交互；不能仅因补丁无文本冲突就认定正确。 |
| 整体升级 architectds best/0.1.38 ↔ 逐项移植 | 两种推进路径。整体升级后需重新盘点，不能把其中已含的 PR 再重复加一次。 |

## 执行建议

先核对并单独验证优先级 0；第一项纯性能候选选择 **#547**。然后 #413/#363，小步验证，最后再考虑 #583 与 CPU/profile 参数组合。每个候选单独提交，默认 22 秒单轮快测筛选；值得保留的候选用同一 32K 服务做三次重复，若差异只有 1–3%，再做交替 A/B，而不是拿一次最高值报收益。输出改变的候选需检查数值或任务正确性，不能只看速度。

优先级是对本机的判断，不是上游排名。本轮读取了当前 open PR 清单、最近更新的 100 条 PR，并重点核对上述 PR 的正文、最新评论和部分源码；不是对所有 fork 的穷尽搜索。PR 头提交与状态见同目录 `community-optimization-audit-20261003.json`。
