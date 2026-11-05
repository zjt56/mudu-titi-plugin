# 英语听力切段（WhisperX 可选能力）

插件默认**不安装、不下载**任何语音模型。核心流程零第三方依赖，听力切段是可选能力：
适配器代码随插件分发（几十 KB），`torch` / `whisperx` / 模型权重在设置页点击「安装」后
才懒下载到**数据根的 `audio/` 子目录**（与宿主 `resolveDataRoot()` 完全一致：
`WF_DATA_DIR` > 插件目录内已有 `workflows/` 时沿用插件目录 > `~/.dsh/wf-plugin-data`），
升级或重装插件不丢，且宿主新旧版本解析到同一处。
随包分发的 `config/audio.json` 只是只读默认值；设置页的改动写到数据根的
`audio/config.json`，不会写进（可能只读的）npm 包目录。

**转写时间戳绝不直接当切点**；词/段时间只提供定位，最终可信切点必须来自真实长静音。

## 模型、真实时间与切片可信度

默认 **large-v3-turbo 多语种大模型**；没有安装时不下载。模型缓存缺失或加载失败明确报错，不自动降回small。CPU默认4线程、batch=2、最多1份模型；资源充足可显式改并发为2，不能把网络模型并发数照搬到本地大模型。

1. ffprobe读取时长，ffmpeg探测真实静音；≥3秒长静音中点构成物理边界。
2. **在ASR之前按长静音分区域**，同一份大模型依次转写各区域，局部时间按真实采样起点加回原音频偏移，避免不同题被合成一个长段后对齐压缩。
3. worker保留原始`segments`及真实start/end。词级对齐不插值，含中文/越界/重叠/倒序/首尾覆盖不足则拒绝该段词级结果，保留粗段时间及诊断。不得按字符长度伪造词时间。
4. 题号支持线索词、裸题号、简繁体与中文数字；章节序号不能当题号，公交`a number two bus`不能当题号。标题如短文理解、信息转换可提供粗定位，不能声称精确到词。
5. 切点只取真实长静音；无锚点或锚点碰撞等仅保留候选。邻接单元的结束边界同样受影响。`interpolated/coarse/micro`本身不自动verified；只有完整真实region、章节题量、已有题号全部一致，或完整组范围/标题可唯一绑定物理region时，才另附`region_proofs`形成边界证明；`semantic_verified=false`明确未做听音语义核验。
6. 主转写已有全部章节/分组锚点时省掉辅助整卷中文转写；不足才调用zh辅助。切片可并行，模型受跨进程slot预算控制。

## 缓存、恢复与速度

转写键绑定音频内容SHA256、具体模型、VAD、语言、是否对齐、真实regions及worker实现契约。复制同一音频路径变化仍可复用；同大小同mtime但内容变化不会误命中。内容与sidecar摘要必须匹配，老的伪词时间缓存拒绝复用。

输出用临时文件原子安装；中断保留旧缓存。切片绑定来源哈希、精确边界、编码和输出哈希，未变化不重切；同JSON不重写，避免无谓作废审核。

`audio`阶段只校验已有证据，不调用代理重复改写音频映射；证据不足明确needs_review，独立阶段仍可推进。后台任务区分“生成完毕”和“可信通过”。

`02_audio/timing.json`记录探测、静音、主/辅助转写、规划、切片和wall耗时。并发任务各duration不能直接相加为墙钟；历史small模型191秒不能当大模型耗时，文件mtime差也不能推算词对齐耗时。

## 体积

| 部分 | 体积 |
| --- | --- |
| 适配器（随插件分发） | 几十 KB |
| venv：`whisperx==3.8.6` + torch / torchaudio / transformers / pyannote / ctranslate2 / av / onnxruntime | CPU 版约 4–6 GB（PyPI 的 whisperx 会连带 pyannote 与 torchvision，即使我们不做说话人分离） |
| ASR 模型 `large-v3-turbo`（多语种，默认） | 以下载器实际大小为准，显著大于small；运行内存按CPU/batch预算控制 |
| 对齐模型 `facebook/wav2vec2-base-960h` | 约 360 MB |

依赖 **ffmpeg / ffprobe**（系统自带或 `brew install ffmpeg`）。不做说话人分离，
所以**不需要 HuggingFace token**；但 PyPI 的 whisperx 仍会装上 pyannote 依赖。

### 分发到别的机器：随包自带运行时

目标：**任何电脑装完插件、点一次「一键安装」就能用**，不要求用户预装 Python / ffmpeg。

做法是把运行时二进制放进插件包的 `app/audio/vendor/`（见 [vendor/README.md](../audio/vendor/README.md)）：

| 目录 | 内容 | 每平台约 |
| --- | --- | --- |
| `vendor/python/<platform>/` | 便携 CPython（3.11，含 pip/venv） | 30–50 MB |
| `vendor/ffmpeg/<platform>/bin/` | ffmpeg + ffprobe | 50–80 MB |

`bootstrap` 与切段的查找顺序：**vendor 优先** → 配置的 `venv` → 系统 PATH。

发布前在有网机器运行 `python app/audio/vendor/fetch_vendor.py --platform darwin-arm64 --platform win32-x64`
下载、校验 sha256、解压进 `vendor/`（二进制不进 git，但要进发布包）。打包后体积约 +150–260 MB。

启动顺序（`base_python`）：后端本身也优先用 vendor 里的便携 Python 起，Windows 用户没装
Python 也能启动；unix 上 vendor 的 `bin/python3` 直接可用，Windows 上 `python.exe`。

其余每台机器仍需具备的（缺少会在安装阶段明确报错并写进 `status.json` 的 `error`）：

| 条件 | 说明 |
| --- | --- |
| 能访问模型下载源 | 模型走 HuggingFace。默认自动探测：直连不通就用 `hf-mirror.com`；也可在设置里填公司内网镜像 `hf_endpoint` |
| 磁盘 6–10 GB | Linux 上默认先装 CPU 版 torch（`cpu_torch`），避免数 GB 的 nvidia-* CUDA 包；CPU 版仍约 4–6 GB |
| 首次联网 | 不点「安装」不下载；首次安装会建 venv、装依赖、缓存所选ASR和对齐模型，之后离线可用 |

**为什么不用 torchaudio 的对齐模型**：`WAV2VEC2_ASR_BASE_960H` 的权重在
`download.pytorch.org`，国内常不可达；改用 HF repo `facebook/wav2vec2-base-960h`
后与 ASR 模型共用同一镜像。VAD 默认 `pyannote`，用的是 whisperx wheel 自带的
`assets/pytorch_model.bin`，完全不联网；`silero` 反而要从 GitHub 拉 `torch.hub` 模型。

`bootstrap` 与 `segment` 共用 `config.model_env(cfg)` 构造的模型缓存与镜像环境，
否则会出现「安装时下到数据根、切段时却去 `~/.cache/huggingface` 找」而重新下载。

> **大模型不等于自动核验通过**。语音时间、真实静音与题号归属分别验证；不能用时长看起来合理代替内容核对。

## 用起来

1. 打开插件「模型与设置」→ 最下方「英语听力切段（可选）」，勾选启用、确认参数，保存。
2. 点「安装运行时与模型」。首次会建 venv、装依赖、缓存所选ASR和对齐模型，进度在卡片里显示。
   也可以填「复用已有 venv」指向本机现成的虚拟环境（仍会自动补齐缺失依赖）。
3. 在「为批次生成听力切段」选择**已上传整卷听力音频、且完成结构确认**的批次，点「生成切段」。
4. 完成后按正常流程推进 `audio` 阶段；导入器会拿 `segment-evidence.json` 逐字校验时间戳。

命令行等价操作（在 `app/` 目录下）：

```bash
python -m audio config                    # 查看配置与数据根
python -m audio status                    # 查看安装/切段进度与错误
python -m audio bootstrap                 # 安装运行时与模型（--force 可重装）
python -m audio segment --batch-dir workflows/<批次名>
```

REST 接口（由插件宿主的 `/wf-plugin/api/*` 透明代理到工作台后端）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/wf-plugin/api/audio/config` | 配置、数据根、venv 路径、生效的模型镜像、Python 版本、是否已就绪、ffmpeg 是否可用 |
| POST | `/wf-plugin/api/audio/config` | 保存配置（`{"config": {...}}`） |
| GET | `/wf-plugin/api/audio/status` | 后台任务进度：`phase` / `pct` / `detail` / `error` |
| POST | `/wf-plugin/api/audio/bootstrap` | 启动懒安装（`{"force": false}`） |
| POST | `/wf-plugin/api/audio/segment` | 启动切段（`{"name": "批次名"}`） |

安装与切段都在后台线程执行，不阻塞工作台；同一时刻只允许一个听力任务。

## 边界

- 自动生成的切片可播放，但只有首尾边界证据充分才允许通过audio契约；人工核验、上传、发布均不得伪造。
- 粗段锚点、插值切点与听音语义核验区分记录；`verified`只说明程序边界契约，`semantic_verified`单独说明是否真核对内容。
- 题库是否收录听力依用户批次指令，不能用该策略掩盖切片错误。
