## 更新内容

v0.6.1 是稳定性版本：本机离线转写引擎实测转正并修复两个真实缺陷。安装版用户无功能变化，按需升级即可。

### 本机转写引擎（源码增强包）实测转正

- 实测数据（本机 CPU、5 分 40 秒真实歌曲）：**base** 型号约 20 秒转完，
  歌词覆盖到全曲人声段（03:33）——优于云端原曲直转（02:07），且免费、断网可用
- 默认型号由 small 调整为 base：实测 small 在歌曲上比实时还慢且偶发复读幻觉，
  tiny 错字偏多，base 的速度与质量平衡最佳
- 修复：whisper-timestamped 1.15.9 的词级字段实际叫 `text`（文档写 `word` 且恒为
  空值）——旧代码会静默丢掉全部词级时间戳、整首退化为无时间轴纯文本
- 安装更简单：ffmpeg 不必单独安装，缺失时自动借用 `imageio_ffmpeg` 包自带的
  静态版（修复了其二进制文件名不规则导致找不到命令的问题）。增强包一条命令：
  `pip install whisper-timestamped imageio-ffmpeg`

### 其它修复与加固

- 全链路冒烟测试（内嵌 / lrclib 在线 / 百炼云端 / 本机引擎 / 设置读写 / 失败
  清单 / GUI 双模式）全部通过，并修复过程中发现的两处缺陷（见上一条）与一处
  仅在 macOS/Linux 上暴露的测试断言问题
- 三平台 CI（Windows / macOS / Ubuntu）持续全绿，261 个自动化测试通过

### 安装方法

按你的系统下载对应附件：

| 系统 | 附件 | 安装方式 |
| --- | --- | --- |
| Windows | `LyricsTranscriber-Setup-v0.6.1.exe` | 双击安装，覆盖旧版升级 |
| macOS | `LyricsTranscriber-v0.6.1-macOS.dmg` | 打开 dmg，拖入应用程序；首次运行右键→打开 |
| Linux | `LyricsTranscriber-v0.6.1-Linux.AppImage` | `chmod +x` 后直接运行 |

> Windows 安装包未做数字签名，SmartScreen 提示时点「更多信息 → 仍要运行」。

**Full Changelog**: https://github.com/LainOuO/Lyrics-Transcriber/compare/v0.6.0...v0.6.1
