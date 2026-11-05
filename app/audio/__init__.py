# -*- coding: utf-8 -*-
"""英语听力切段适配器（可选能力，懒加载）。

设计要点（对应 docs/audio.md）：
- 插件包里只有这些纯 Python 文件；torch / whisperx / 模型权重都在数据根里按需安装。
- 切点骨架来自 ffmpeg silencedetect 的长静音，WhisperX 词级时间戳只用来定位
  「Question N / 第 N 题」题号锚点，绝不直接把转写时间戳当切点
  （见 skills-reference/lessons-learned.md 第 4 条）。
- 产出 02_audio/segment-evidence.json，供 workflow.agent_tasks 的 audio 阶段
  逐字校验（verified=true + file/start/end/evidence 必须与真实切片一致）。
"""
