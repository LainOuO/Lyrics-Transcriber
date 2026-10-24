## 更新内容

v0.6.0 起正式支持三大平台，每个版本的 Release 都会同时提供三份安装包。

### 新增：macOS 与 Linux 支持

- **macOS**：下载 `.dmg`，打开后把「LyricsTranscriber」拖进「应用程序」即可。
  未做开发者签名与公证——首次打开若被 GateScreen 拦截，在「访达」里
  **右键点程序图标 → 打开 → 再点打开**（或系统设置 → 隐私与安全性 → 仍要打开）
- **Linux**：下载 `.AppImage`，赋予可执行权限后双击/终端运行：
  `chmod +x LyricsTranscriber-v0.6.0-Linux.AppImage && ./LyricsTranscriber-v0.6.0-Linux.AppImage`
- **Windows**：与此前一致，安装包（`LyricsTranscriber-Setup-v0.6.0.exe`）覆盖安装即可升级
- 三平台功能完全一致：12 格式歌词提取、lrclib 在线兜底、AI 听歌转写（百炼
  Paraformer）、预览改错字、失败清单挑选；界面自动选平台中文字体
  （Windows 微软雅黑 / macOS 苹方 / Linux Noto）

### 工程变化（开发者相关）

- GitHub Actions 双流水线：每次提交在 Windows/macOS/Ubuntu 三平台跑全量测试；
  打版本 tag 自动构建三平台安装包并发布 Release
- 发布说明随仓库入库（docs/release-notes-v*.md）；打包 spec 三平台通用

### 安装方法

按你的系统下载对应附件：

| 系统 | 附件 | 安装方式 |
| --- | --- | --- |
| Windows | `LyricsTranscriber-Setup-v0.6.0.exe` | 双击安装，覆盖旧版升级 |
| macOS | `LyricsTranscriber-v0.6.0-macOS.dmg` | 打开 dmg，拖入应用程序；首次运行右键→打开 |
| Linux | `LyricsTranscriber-v0.6.0-Linux.AppImage` | `chmod +x` 后直接运行 |

> Windows 安装包未做数字签名，SmartScreen 提示时点「更多信息 → 仍要运行」。

**Full Changelog**: https://github.com/LainOuO/Lyrics-Transcriber/compare/v0.5.2...v0.6.0
