# Song PC 32K 单轮基准

2026-10-03。此分支从实际部署的 architectds/Strata `636531421747293942cb1e213ccbeda5fcb52dbe` 建立，保留本地 HF 镜像和 native-pack 安装修改。未升级引擎、未合入新社区 PR。本次只启用已有能力并固定快速验证流程。

## 固定环境

- RTX 4080 16GB；i5-13600KF；物理内存 64GB，WSL 上限 56GB、swap 16GB。
- SC117 Qwen3.8 Flash Next GSQ-RCO abliterated IQ3_S，API 别名 `qwen3.8-fn`。
- Strata 引擎 0.1.36，二进制 SHA256 `0fc365be01c4947b462e2d29839bb756fe62a711ba98ad99780db089a55f060e`。本轮未重新编译。
- `--max-context 32768 --kv int8 --resident-experts --prefill auto --spec 4 --spec-min-p 0.5 --vram-reserve-mib 700 --vision --prompt-cache 0`。
- 沿用原版 Q2_0 MTP；9 个 expert worker；本轮未调整这些参数。
- 环境 `STRATA_PF_FUSED=1`、`STRATA_PREFILL_CPU_LOG=1`；CPU assist 保持自动策略。
- 服务配置 `vision_on_demand=true`、`vision_idle_s=10`；服务自动传入 `--lendable-cache`。
- 全部项目内容在 `/mnt/data`。下载直连、HF 使用国内镜像；本轮没有下载模型。

32K 是配置的上下文容量；此基准实际输入最多 3842 tokens，**不等于填满 32K 的压力测试**。

## 已确认生效的能力

1. 在 32K 配置下 CPU 辅助预填充正常参与，日志显示约 42–46% 的非驻留专家由 CPU 处理。这里没有在同一 32K 下做 CPU 开关消融，不能把跨上下文差异全归因于 CPU。
2. fused INT8 prefill 内核已从日志确认生效。约 4K 输入相对本轮 base 的预填充中位数提高约 2.2%，属于小幅变化，不能保证所有请求变快。
3. 按需视觉释放显存后，专家缓存从 3643 槽 / 6.97 GiB 增至 4242 槽 / 8.09 GiB。图片请求借出 1442 MiB / 746 槽，空闲 10 秒后已确认回收，746 个专家回填耗时 316ms。
4. 专家权重常驻并锁定约 41.74 GiB 系统内存，本轮运行期间专家 blob 文件读取为零。这不代表模型冷启动不读 SSD。

## 一键复测

在 Song PC Linux 上，服务就绪后运行：

```bash
/mnt/data/strata-deploy/Strata/tools/song_pc_quick_bench.sh
```

默认做三次预热（各输出 32 tokens），然后三条独立文本请求（每条最多输出 128 tokens），约 **22 秒**。需要三次重复和图片检查时：

```bash
/mnt/data/strata-deploy/Strata/tools/song_pc_quick_bench.sh --repeats 3 --image
```

本轮完整配置耗时 68.3 秒。每个请求都是新的 system + user，没有连续对话；前缀缓存必须为零。温度为 0，关闭 thinking。答案检查涵盖算术 391、文本检索标记、图片中标签与数值，属于回归冒烟检查，不是完整智力评估。测量走 `/v1/chat/completions`；不改变客户端 Messages API 配置。

脚本使用私密 key 文件，HTTP 不走环境代理；`flock` 防止多个快测脚本并发。测试期间也应避免其他客户端请求，以免资源争用。结果保存在 `/mnt/data/strata-deploy/bench-32k-20261003/results/quick/`。

仅默认三条文本用于快速筛选；需要确认小幅收益时用 `--repeats 3`，最好交替测多个配置。复测须固定模型、引擎、输入、输出上限、MTP、上下文及预热流程。每次完整重启会重置运行中的专家缓存热度；当前模型冷启动约 3.4–3.9 分钟，未计入请求基准耗时。

固定 prompt SHA256：`3732eaebcbdac45891ae09352ed1d28b7325cbb21b513f921527694477f50244`。
固定图片 SHA256：`a9266a8c519d91cbacbf4e7e9eaf6241cfee610efa6d7521e32462d7ea725043`。

## 最终配置基准

以下为三次测量中位数，所有测量请求实际输出 128 tokens，所有 `cache_n=0`。

| 实际输入 tokens | Prefill tokens/s | Decode tokens/s | 首字等待秒 |
| --- | ---: | ---: | ---: |
| 58 | 47.7 | 38.8 | 1.251 |
| 891 | 599.2 | 42.1 | 1.522 |
| 3842 | 1453.8 | 43.9 | 2.693 |

Prefill、decode 为引擎报告的阶段吞吐；首字等待从发起 HTTP 请求到收到第一段输出，包含服务开销。短输入容易受固定开销影响，不能把 58-token 的吞吐直接与 4K 输入吞吐比较。

随后一键脚本实跑总耗时 **21.832 秒**，三条测量依次为：57.2 / 42.9、647.8 / 43.8、1457.9 / 44.3 tokens/s（prefill / decode），首字等待 1.037、1.403、2.679 秒。全部六次请求通过答案检查。该结果为已运行过完整基准的热服务上的单次快测，不能替代上面的三次中位数。

### 本轮配置对照

| 配置 | 约 1K prefill / decode | 约 4K prefill / decode |
| --- | ---: | ---: |
| 32K base，视觉常驻 | 593.3 / 37.95 | 1393.85 / 39.65 |
| base + fused | 611.25 / 37.6 | 1423.9 / 40.1 |
| fused + 按需视觉（选定） | 599.2 / 42.1 | 1453.8 / 43.9 |

单位 tokens/s。base 与 fused 各测两次，最终配置测三次。base 此前运行过 pilot，预热流程也略有差异；这些是探索性对照，不是严格交替 A/B 因果验证。短文本、专家缓存热度和 MTP 接受率会造成波动，本轮不宣称全面固定比例提速。

### 图片代价

首次图片请求：426 输入 / 128 输出 tokens，prefill 336.7、decode 30.3 tokens/s，首字等待 **4.274 秒**。
同图缓存后，另起独立请求：prefill 351.3、decode 39.0 tokens/s，首字等待 **1.254 秒**。

按需视觉更适合以文本为主的场景。首次图片需要加载视觉模块；同图第二次可能命中图像编码缓存。这不是连续对话或 KV 前缀缓存的收益。要比较首次图片成本，应重启服务再测试。

## 服务与回退

当前运行的是 32K 基准配置，前缀缓存关闭。监听 `0.0.0.0:8080`，API key 保持不变，Tailscale 地址不变；服务未启用开机自启。

```bash
systemctl start strata-sc117
systemctl stop strata-sc117
```

客户端原有 256K metadata 尚未修改，不代表当前服务能接收 256K；当前服务上限以 `/health` 的 32768 为准。保留 256K 原配置和旧二进制；需恢复时在 Song PC Linux 上执行：

```bash
systemctl stop strata-sc117
cp /mnt/data/strata-deploy/bench-32k-20261003/backups/config-256k.json /mnt/data/strata-deploy/strata-sc117-iq3_s.json
chmod 600 /mnt/data/strata-deploy/strata-sc117-iq3_s.json
systemctl start strata-sc117
```

32K 私密配置另存为 `/mnt/data/strata-deploy/strata-sc117-32k-baseline.json`。私密配置和 key 不提交 Git。原有源码修改已提交 fork，同时仍保留远端 stash 与 patch 备份。
