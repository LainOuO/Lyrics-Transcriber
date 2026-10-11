# 歌词提取器

从 mp3 文件中提取歌词，生成配套的 `.lrc` 文件（与 mp3 同目录、同名，播放器可自动加载）。

## 歌词来源（按优先级）

1. **内嵌歌词标签**：优先读取 mp3 的 `SYLT`（带时间轴的同步歌词），其次 `USLT`（纯文本歌词；若文本内本身带有 `[mm:ss.xx]` 时间戳，也会解析为同步歌词）。
2. **在线兜底**：内嵌没有时，按“歌名 + 歌手 + 时长”查询 [lrclib.net](https://lrclib.net) 免费公开歌词接口，优先取带时间轴的版本。
3. **失败清单**：两者都拿不到的文件记入失败清单，可在界面上导出为 txt。

拿不到时间轴时降级输出纯文本行（无时间戳的 LRC），并在日志中标记。

## 使用

开发模式运行（需要 Python 环境）：

```bash
python -m lyrics_extractor
```

打包与安装：见 `packaging/README.md`。

## 开发

```bash
python -m pip install -r requirements.txt
python -m pytest
```

## 目录结构

```
lyrics_extractor/     源码包
  id3_reader.py       mutagen 读取 ID3 标签与内嵌歌词
  lrc_writer.py       LRC 内容生成与文件写入
  online.py           lrclib.net 在线歌词客户端
  extractor.py        单文件处理流水线（内嵌 → 在线 → 写入）
  batch.py            文件夹批量调度、汇总统计、失败清单
  gui.py              tkinter 图形界面
tests/                pytest 测试
packaging/            PyInstaller 与 Inno Setup 打包脚本
```
