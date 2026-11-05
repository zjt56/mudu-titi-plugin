# 英语听力切段（WhisperX 可选能力）

插件默认**不安装、不下载**任何语音模型。核心流程零第三方依赖，听力切段是可选能力：
适配器代码随插件分发（几十 KB），`torch` / `whisperx` / 模型权重在设置页点击「安装」后
才懒下载到**数据根的 `audio/` 子目录**（与宿主 `resolveDataRoot()` 完全一致：
`WF_DATA_DIR` > 插件目录内已有 `workflows/` 时沿用插件目录 > `~/.dsh/wf-plugin-data`），
升级或重装插件不丢，且宿主新旧版本解析到同一处。
随包分发的 `config/audio.json` 只是只读默认值；设置页的改动写到数据根的
`audio/config.json`，不会写进（可能只读的）npm 包目录。

## 为什么用 WhisperX，以及切点怎么定

`skills-reference/lessons-learned.md` 第 4 条记录过踩坑：**Whisper 的时间戳不可靠**，
VAD 会把试音切成大量极短片段，长空白后时间戳漂移。所以本适配器的切点是：

1. `ffmpeg silencedetect` 找出所有静音，**≥ `skeleton_silence`（默认 3 秒）的长静音中点**
   作为切点骨架；
2. WhisperX（`faster-whisper` 转写 + `wav2vec2` 强制对齐）给出**词级时间戳**，
   只用来定位 `Question 1` / `第 3 题` 这类题号锚点；
3. 把锚点吸附到「锚点之前最近的骨架切点」，相邻单元首尾相接；
4. `ffmpeg` 按整卷真实偏移切片，并把命令、静音骨架、锚点写进证据。

**转写时间戳绝不直接当切点。** 没有锚点的单元会按骨架等分，并在证据里标 `interpolated`，
供人工回听确认。

产物（都写在批次的 `02_audio/`）：

| 文件 | 用途 |
| --- | --- |
| `segment-evidence.json` | `workflow.agent_tasks` 的 `audio` 阶段逐字校验：`verified=true` 且 `unit_id/file/start/end/evidence` 必须与真实切片一致 |
| `audio_map.json` | 审核界面播放听力附件 |
| `full-transcript.json` | 语义审核阶段的完整转写与词级时间戳 |

## 体积

| 部分 | 体积 |
| --- | --- |
| 适配器（随插件分发） | 几十 KB |
| venv：`whisperx==3.8.6` + torch / torchaudio / transformers / pyannote / ctranslate2 / av / onnxruntime | CPU 版约 4–6 GB（PyPI 的 whisperx 会连带 pyannote 与 torchvision，即使我们不做说话人分离） |
| ASR 模型 `small.en`（int8） | 约 250 MB；`base.en` 约 90 MB |
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
| 首次联网 | 不点「安装」不下载；首次安装会建 venv、装依赖、缓存约 600 MB 模型，之后离线可用 |

**为什么不用 torchaudio 的对齐模型**：`WAV2VEC2_ASR_BASE_960H` 的权重在
`download.pytorch.org`，国内常不可达；改用 HF repo `facebook/wav2vec2-base-960h`
后与 ASR 模型共用同一镜像。VAD 默认 `pyannote`，用的是 whisperx wheel 自带的
`assets/pytorch_model.bin`，完全不联网；`silero` 反而要从 GitHub 拉 `torch.hub` 模型。

`bootstrap` 与 `segment` 共用 `config.model_env(cfg)` 构造的模型缓存与镜像环境，
否则会出现「安装时下到数据根、切段时却去 `~/.cache/huggingface` 找」而重新下载。

> `small.en` 是英语专用模型。若录音带中文导语（「听下面一段对话，回答第 1 题」），
> 请改用 `small`（多语种），但英文词级对齐模型对中文词的对齐质量会下降，
> 题号锚点可能仍需人工回听校正。

## 用起来

1. 打开插件「模型与设置」→ 最下方「英语听力切段（可选）」，勾选启用、确认参数，保存。
2. 点「安装运行时与模型」。首次会建 venv、装依赖、缓存约 600 MB 模型，进度在卡片里显示。
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

- 不自动把候选写进正式阶段目录，也不替任务造 `verified` 证据以外的完成状态；
  切片失败会如实写进 `status.json` 的 `error`。
- `verified=true` 的含义是「这些时间戳是 ffmpeg 真实切出来的、与磁盘上的切片文件一一对应」，
  不是「题号归属已通过语义审核」。锚点插值、题号归属仍需回听与人工确认。
- 题库完整性判定里「英语听力直接跳过」是另一条口径，与本切段能力不冲突。
