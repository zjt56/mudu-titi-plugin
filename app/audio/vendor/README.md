# vendor/ —— 随插件分发的运行时二进制（可选）

把这两类二进制放在这里，插件安装后即可「开箱即用」，无需用户机器预装 Python 或 ffmpeg：

```
vendor/
  python/
    darwin-arm64/        # 解压后的便携 CPython，含 bin/python3
    win32-x64/           # 解压后的便携 CPython，含 python.exe
  ffmpeg/
    darwin-arm64/bin/    # ffmpeg 与 ffprobe
    win32-x64/bin/       # ffmpeg.exe 与 ffprobe.exe
```

目录名 = `platform_key()`：`darwin-arm64` / `darwin-x64` / `win32-x64` / `linux-x64`。

## 怎么放

在有网络的机器上运行：

```bash
python app/audio/vendor/fetch_vendor.py --platform darwin-arm64 --platform win32-x64
```

它会按 `platforms.json` 里登记的 URL 下载、校验 sha256、解压到上面这些目录。
也可手工放入：便携 Python 解压后把整层目录放到 `python/<platform>/`，
ffmpeg/ffprobe 放到 `ffmpeg/<platform>/bin/`。

## 优先级

`bootstrap` 与 `segment` 查找运行时的顺序：

1. 本目录（vendor）——随插件分发，最稳；
2. 配置 `venv` 指定的虚拟环境（复用）；
3. 系统 PATH / 常见路径（开发机常见）。

任一层都没有时，安装会明确报错并告诉缺什么，不会静默失败。

体积参考：便携 CPython 每平台约 30–50 MB，ffmpeg+ffprobe 每平台约 50–80 MB。
