"""tkinter 图形界面：选单个文件或整个文件夹、开始提取、进度条、日志、导出失败清单。

AI 兜底：勾选后对查不到歌词的文件调用 AI 听歌转写（先预览改错字再写入），
也可从失败清单里挑歌手动转写；服务商与密钥在「AI 设置」里配置并持久化到本机。

外观（配色 / 字体 / 间距 / 控件样式）集中在下面「外观令牌」一节和 `_apply_theme()`
里，全部由 ttk.Style 约束；界面文案与业务逻辑不在这里改。
"""

from __future__ import annotations

import functools
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from lyrics_extractor import APP_NAME, __version__, batch
from lyrics_extractor import settings as settings_module
from lyrics_extractor.audio_file import SUPPORTED_EXTENSIONS, SUPPORTED_EXTENSIONS_TEXT
from lyrics_extractor.extractor import Status
from lyrics_extractor.lrc_writer import format_timestamp, parse_lrc_text

# 提取范围：整个文件夹（默认，保持老习惯）或单个文件
MODE_FOLDER = "folder"
MODE_FILE = "file"
MODE_AI = "ai"  # 从失败清单挑歌做 AI 转写的独立运行
MODE_LABELS = {MODE_FOLDER: "歌曲文件夹：", MODE_FILE: "音频文件："}
MODE_RADIOS = ((MODE_FOLDER, "整个文件夹"), (MODE_FILE, "单个文件"))
RUN_SCOPE_LABELS = {MODE_FOLDER: "文件夹", MODE_FILE: "单个文件", MODE_AI: "AI 转写"}


# ----------------------------------------------------------------------
# 外观令牌：一套浅色方案——中性灰底 + 白色面板 + 单一靛蓝主色。
# 状态色（绿/红）只出现在状态行，避免满屏彩色抢注意力。

COLOR_BG = "#F4F5F7"             # 窗口底色（中性浅灰，衬托白色面板）
COLOR_SURFACE = "#FFFFFF"        # 面板 / 输入框 / 日志底色
COLOR_INK = "#1B1F26"            # 主文字
COLOR_INK_SOFT = "#33383F"       # 日志正文（比主文字略软，长时间看不刺眼）
COLOR_MUTED = "#5F6672"          # 次要文字
COLOR_FAINT = "#8A909B"          # 说明性小字
COLOR_BORDER = "#E2E5EA"         # 面板描边 / 分隔线
COLOR_BORDER_STRONG = "#C6CBD4"  # 输入框、次级按钮的描边
COLOR_ACCENT = "#4C5BD4"         # 主色（靛蓝）
COLOR_ACCENT_HOVER = "#414FC2"   # 主色悬停 / 按下
COLOR_ACCENT_SOFT = "#EEF0FB"    # 主色浅底（选中态）
COLOR_ACCENT_EDGE = "#C7CCF2"    # 主色浅底配的描边
COLOR_DISABLED_BG = "#F1F2F5"    # 禁用态底色
COLOR_DISABLED_FG = "#A8ADB6"    # 禁用态文字
COLOR_SUCCESS = "#157F4B"        # 状态行：成功
COLOR_ERROR = "#BF3A2B"          # 状态行：失败

_FAMILY = "Microsoft YaHei UI"  # 界面字体族，_apply_theme 里按本机实际字体校正
_IMAGE_REFS: list = []          # 自绘图标必须常驻：被 GC 掉 Tcl 那边就画不出来了


def _font(size: int = 10, weight: str = "normal") -> tuple:
    return (_FAMILY, size, weight)


def _pick_family(widget) -> str:
    """按优先级挑一个本机有的中文字体族；都没有就沿用默认值。"""
    try:
        families = set(tkfont.families(widget))
    except tk.TclError:
        return _FAMILY
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "Segoe UI"):
        if name in families:
            return name
    return _FAMILY


def _asset_path(name: str) -> Path:
    """资源路径：源码运行时在包目录下，PyInstaller 打包后在 _MEIPASS 里。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "lyrics_extractor" / "assets" / name
    return Path(__file__).resolve().parent / "assets" / name


# ----------------------------------------------------------------------
# 自绘勾选框 / 单选圆点：clam 自带的指示器只有一个没有勾号的小方块，
# 看不出「选中」和「没选中」，所以这里用距离场逐像素画一套小图标。


def _on_stroke(cx: float, cy: float, points, width: float) -> bool:
    """点 (cx, cy) 是否落在折线上（线宽 width，两端圆头）。"""
    half = width / 2.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        vx, vy = x2 - x1, y2 - y1
        length2 = vx * vx + vy * vy
        if length2 <= 0:
            continue
        t = ((cx - x1) * vx + (cy - y1) * vy) / length2
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        dx, dy = cx - (x1 + t * vx), cy - (y1 + t * vy)
        if dx * dx + dy * dy <= half * half:
            return True
    return False


def _raster(size: int, gap: int, draw) -> tk.PhotoImage:
    """把 draw(x, y) -> 颜色 的采样函数变成一张 size×size 的图片，右侧留 gap 像素空白。"""
    width = size + gap
    image = tk.PhotoImage(width=width, height=size)
    rows = []
    for y in range(size):
        row = []
        for x in range(width):
            row.append(draw(x + 0.5, y + 0.5) if x < size else COLOR_SURFACE)
        rows.append("{" + " ".join(row) + "}")
    image.put(" ".join(rows))
    return image


def _rounded_rect(size: int, radius: float, fill: str, border: str,
                  border_w: float, mark=None, mark_w: float = 0.0):
    """圆角方框（勾选框底），mark 是勾的折线坐标。"""
    points = tuple(mark or ())

    def draw(cx: float, cy: float) -> str:
        dx = max(radius - cx, cx - (size - radius), 0.0)
        dy = max(radius - cy, cy - (size - radius), 0.0)
        distance = (dx * dx + dy * dy) ** 0.5
        if distance > radius:
            return COLOR_SURFACE
        if distance >= radius - border_w:
            return border
        if points and _on_stroke(cx, cy, points, mark_w):
            return "#FFFFFF"
        return fill

    return draw


def _circle(size: int, fill: str, border: str, border_w: float,
            dot_fill: str | None = None, dot_r: float = 0.0):
    """圆形（单选圆点底），dot_fill 是中间那个实心点。"""
    center = size / 2.0

    def draw(cx: float, cy: float) -> str:
        distance = ((cx - center) ** 2 + (cy - center) ** 2) ** 0.5
        if distance > center:
            return COLOR_SURFACE
        if distance >= center - border_w:
            return border
        if dot_fill and distance <= dot_r:
            return dot_fill
        return fill

    return draw


def _install_indicators(style: ttk.Style, widget) -> None:
    """装上自绘的勾选框 / 单选图标；同一主题下只装一次。"""
    if "Round.Check.indicator" in style.element_names():
        return
    try:
        scale = max(1.0, widget.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        scale = 1.0
    size = int(round(16 * scale))
    gap = int(round(7 * scale))
    edge = max(1.2, 1.2 * scale)
    mark = ((0.27 * size, 0.53 * size),
            (0.43 * size, 0.69 * size),
            (0.74 * size, 0.36 * size))
    mark_w = max(1.6, 0.13 * size)

    images = [
        _raster(size, gap, _rounded_rect(size, size * 0.28, COLOR_SURFACE,
                                         COLOR_BORDER_STRONG, edge)),
        _raster(size, gap, _rounded_rect(size, size * 0.28, COLOR_ACCENT,
                                         COLOR_ACCENT, edge, mark, mark_w)),
        _raster(size, gap, _rounded_rect(size, size * 0.28, COLOR_DISABLED_BG,
                                         "#D8DCE3", edge)),
        _raster(size, gap, _rounded_rect(size, size * 0.28, "#C3C9EC",
                                         "#C3C9EC", edge, mark, mark_w)),
        _raster(size, gap, _circle(size, COLOR_SURFACE, COLOR_BORDER_STRONG, edge)),
        _raster(size, gap, _circle(size, COLOR_ACCENT, COLOR_ACCENT, edge,
                                   "#FFFFFF", size * 0.20)),
        _raster(size, gap, _circle(size, COLOR_DISABLED_BG, "#D8DCE3", edge)),
        _raster(size, gap, _circle(size, "#C3C9EC", "#C3C9EC", edge,
                                   "#FFFFFF", size * 0.20)),
    ]
    _IMAGE_REFS.extend(images)  # 常驻引用，别让 Python 回收掉
    off, on, off_dis, on_dis, r_off, r_on, r_off_dis, r_on_dis = images

    style.element_create(
        "Round.Check.indicator", "image",
        off,
        ("disabled", off_dis),
        ("selected", on),
        (("selected", "disabled"), on_dis),
        sticky="",
    )
    style.element_create(
        "Round.Radio.indicator", "image",
        r_off,
        ("disabled", r_off_dis),
        ("selected", r_on),
        (("selected", "disabled"), r_on_dis),
        sticky="",
    )

    for base, element in (("Checkbutton", "Round.Check.indicator"),
                          ("Radiobutton", "Round.Radio.indicator")):
        style.layout(f"Round.T{base}", [
            (f"{base}.padding", {"sticky": "nswe", "children": [
                (element, {"side": "left", "sticky": ""}),
                (f"{base}.focus", {"side": "left", "sticky": "nswe", "children": [
                    (f"{base}.label", {"sticky": "nswe"})]}),
            ]}),
        ])


def _apply_theme(widget) -> None:
    """把整套 ttk 样式装到当前解释器上（重复调用安全：配置幂等，图标只装一次）。"""
    global _FAMILY
    _FAMILY = _pick_family(widget)
    style = ttk.Style(widget)
    # clam 才允许完全改色；vista/xpnative 由系统绘制，大部分配色会被忽略
    style.theme_use("clam")

    style.configure(
        ".",
        background=COLOR_SURFACE,
        foreground=COLOR_INK,
        font=_font(10),
        bordercolor=COLOR_BORDER,
        focuscolor=COLOR_ACCENT,
        troughcolor="#EAECF0",
        insertcolor=COLOR_INK,
        selectbackground=COLOR_ACCENT_SOFT,
        selectforeground=COLOR_ACCENT,
    )

    # 容器与文字
    style.configure("Canvas.TFrame", background=COLOR_BG)
    style.configure("Card.TFrame", background=COLOR_SURFACE)
    style.configure("TLabel", background=COLOR_SURFACE, foreground=COLOR_INK)
    style.configure("Field.TLabel", background=COLOR_SURFACE, foreground=COLOR_INK)
    style.configure("Hint.TLabel", background=COLOR_SURFACE,
                    foreground=COLOR_FAINT, font=_font(9))
    style.configure("Section.TLabel", background=COLOR_SURFACE,
                    foreground=COLOR_MUTED, font=_font(9, "bold"))
    style.configure("Status.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED)
    style.configure("TSeparator", background=COLOR_BORDER)

    # 按钮：次级（描边）、主按钮（主色实心）、幽灵（纯文字，用于弱化操作）
    style.configure(
        "TButton",
        background=COLOR_SURFACE, foreground=COLOR_INK,
        bordercolor=COLOR_BORDER_STRONG, lightcolor=COLOR_SURFACE,
        darkcolor=COLOR_SURFACE, focuscolor=COLOR_ACCENT,
        relief="solid", borderwidth=1, padding=(14, 6), anchor="center",
        width=0,
    )
    style.map(
        "TButton",
        background=[("disabled", COLOR_DISABLED_BG), ("pressed", "#E9EBF0"),
                    ("active", "#F5F6F9")],
        foreground=[("disabled", COLOR_DISABLED_FG)],
        bordercolor=[("disabled", COLOR_BORDER), ("pressed", COLOR_BORDER_STRONG)],
    )
    style.configure(
        "Primary.TButton",
        background=COLOR_ACCENT, foreground="#FFFFFF",
        bordercolor=COLOR_ACCENT, lightcolor=COLOR_ACCENT,
        darkcolor=COLOR_ACCENT, focuscolor=COLOR_ACCENT,
        relief="solid", borderwidth=1, padding=(18, 6), anchor="center",
        width=0,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", "#C9CEF0"), ("pressed", COLOR_ACCENT_HOVER),
                    ("active", COLOR_ACCENT_HOVER)],
        foreground=[("disabled", "#FFFFFF")],
        bordercolor=[("disabled", "#C9CEF0"), ("pressed", COLOR_ACCENT_HOVER),
                     ("active", COLOR_ACCENT_HOVER)],
    )
    style.configure(
        "Ghost.TButton",
        background=COLOR_BG, foreground=COLOR_MUTED,
        bordercolor=COLOR_BG, lightcolor=COLOR_BG,
        darkcolor=COLOR_BG, focuscolor=COLOR_ACCENT,
        relief="flat", borderwidth=0, padding=(10, 6), anchor="center",
        width=0,
    )
    style.map(
        "Ghost.TButton",
        background=[("pressed", "#E7E9EE"), ("active", "#EBEDF1")],
        foreground=[("disabled", COLOR_DISABLED_FG), ("active", COLOR_INK)],
    )
    # 行内小按钮（如「AI 设置…」）：比主按钮矮一档，别跟主操作抢注意力
    style.configure(
        "Small.TButton",
        background=COLOR_SURFACE, foreground=COLOR_MUTED,
        bordercolor=COLOR_BORDER, lightcolor=COLOR_SURFACE,
        darkcolor=COLOR_SURFACE, focuscolor=COLOR_ACCENT,
        relief="solid", borderwidth=1, padding=(11, 3), anchor="center",
        font=_font(9), width=0,
    )
    style.map(
        "Small.TButton",
        background=[("disabled", COLOR_DISABLED_BG), ("pressed", "#E9EBF0"),
                    ("active", "#F5F6F9")],
        foreground=[("disabled", COLOR_DISABLED_FG), ("active", COLOR_INK)],
        bordercolor=[("disabled", COLOR_BORDER), ("active", COLOR_BORDER_STRONG)],
    )

    # 「整个文件夹 / 单个文件」：去掉圆点做成两个分段按钮——灰底可点，选中项主色浅底
    style.layout("Segment.TRadiobutton", [
        ("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Radiobutton.label", {"sticky": "nswe"}),
        ]}),
    ])
    style.configure(
        "Segment.TRadiobutton",
        background="#F1F2F5", foreground=COLOR_MUTED,
        bordercolor="#F1F2F5", lightcolor="#F1F2F5",
        darkcolor="#F1F2F5", focuscolor=COLOR_ACCENT,
        relief="flat", borderwidth=0, padding=(14, 6), anchor="center",
        width=0,
    )
    style.map(
        "Segment.TRadiobutton",
        background=[("disabled", "#F7F7F9"), ("selected", "#DDE1F8"),
                    ("active", "#E9EAF0")],
        foreground=[("disabled", COLOR_DISABLED_FG), ("selected", COLOR_ACCENT)],
    )

    # 输入类
    style.configure(
        "TEntry",
        fieldbackground=COLOR_SURFACE, foreground=COLOR_INK,
        bordercolor=COLOR_BORDER_STRONG, lightcolor=COLOR_BORDER_STRONG,
        darkcolor=COLOR_BORDER_STRONG, insertcolor=COLOR_INK,
        relief="flat", padding=(8, 5),
    )
    style.map(
        "TEntry",
        bordercolor=[("disabled", COLOR_BORDER), ("focus", COLOR_ACCENT)],
        lightcolor=[("focus", COLOR_ACCENT)],
        darkcolor=[("focus", COLOR_ACCENT)],
        fieldbackground=[("disabled", COLOR_DISABLED_BG)],
        foreground=[("disabled", COLOR_DISABLED_FG)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=COLOR_SURFACE, background=COLOR_SURFACE,
        foreground=COLOR_INK, arrowcolor=COLOR_MUTED,
        bordercolor=COLOR_BORDER_STRONG, lightcolor=COLOR_BORDER_STRONG,
        darkcolor=COLOR_BORDER_STRONG, relief="flat", padding=(8, 5),
    )
    style.map(
        "TCombobox",
        # readonly 下拉里那一条蓝色选中高亮要压掉，否则文字像被反白了
        fieldbackground=[("readonly", COLOR_SURFACE), ("disabled", COLOR_DISABLED_BG)],
        foreground=[("readonly", COLOR_INK)],
        selectbackground=[("readonly", COLOR_SURFACE)],
        selectforeground=[("readonly", COLOR_INK)],
        bordercolor=[("focus", COLOR_ACCENT), ("hover", COLOR_ACCENT)],
        arrowcolor=[("disabled", COLOR_DISABLED_FG)],
    )

    # 进度条：细、平、主色
    style.configure(
        "Slim.Horizontal.TProgressbar",
        troughcolor="#EAECF0", background=COLOR_ACCENT,
        bordercolor="#EAECF0", lightcolor=COLOR_ACCENT,
        darkcolor=COLOR_ACCENT, thickness=6,
    )

    # 滚动条：细、浅，不抢视线
    style.configure(
        "Vertical.TScrollbar",
        background="#D3D7DE", troughcolor=COLOR_SURFACE,
        bordercolor=COLOR_SURFACE, lightcolor="#D3D7DE", darkcolor="#D3D7DE",
        arrowcolor=COLOR_FAINT, gripcount=0, arrowsize=12,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("pressed", "#AEB4BF"), ("active", "#BFC4CD")],
        arrowcolor=[("pressed", COLOR_INK)],
    )

    # 分组框（AI 设置里的「云端接口 / 本机模型」）
    style.configure(
        "TLabelframe",
        background=COLOR_SURFACE, bordercolor=COLOR_BORDER,
        lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
        relief="solid", borderwidth=1, padding=(10, 8),
    )
    style.configure("TLabelframe.Label", background=COLOR_SURFACE,
                    foreground=COLOR_MUTED, font=_font(9))

    _install_indicators(style, widget)

    style.configure("Round.TCheckbutton", background=COLOR_SURFACE,
                    foreground=COLOR_INK, padding=(0, 2))
    style.map("Round.TCheckbutton",
              background=[("active", COLOR_SURFACE)],
              foreground=[("disabled", COLOR_DISABLED_FG)])
    style.configure("Round.TRadiobutton", background=COLOR_SURFACE,
                    foreground=COLOR_INK, padding=(0, 2))
    style.map("Round.TRadiobutton",
              background=[("active", COLOR_SURFACE)],
              foreground=[("disabled", COLOR_DISABLED_FG)])

    # 下拉列表的弹出层是原生 Listbox，只能通过 option 数据库改色
    try:
        widget.option_add("*TCombobox*Listbox.background", COLOR_SURFACE)
        widget.option_add("*TCombobox*Listbox.foreground", COLOR_INK)
        widget.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT_SOFT)
        widget.option_add("*TCombobox*Listbox.selectForeground", COLOR_ACCENT)
        widget.option_add("*TCombobox*Listbox.borderWidth", 1)
        widget.option_add("*TCombobox*Listbox.font", "{%s} %d" % (_FAMILY, 10))
    except tk.TclError:
        pass


def _text_area(parent, *, height: int = 18, state: str = "normal"):
    """白底无边框的文本区 + 细滚动条（日志与歌词预览共用一套观感）。"""
    box = tk.Frame(parent, bg=COLOR_SURFACE, borderwidth=0,
                   highlightbackground=COLOR_BORDER, highlightthickness=1)
    text = tk.Text(
        box, height=height, width=1, state=state,
        bg=COLOR_SURFACE, fg=COLOR_INK_SOFT, font=_font(10),
        relief="flat", borderwidth=0, highlightthickness=0,
        padx=14, pady=12, spacing1=1, spacing3=3, wrap="char",
        insertbackground=COLOR_INK, selectbackground="#DCE0F7",
        selectforeground=COLOR_INK,
    )
    bar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    return box, text


def _card(parent, padx: int = 16, pady: int = 14):
    """白底带描边的卡片；返回 (外壳, 内层) —— 外壳自己 pack，控件放内层。"""
    shell = tk.Frame(parent, bg=COLOR_SURFACE, borderwidth=0,
                     highlightbackground=COLOR_BORDER, highlightthickness=1)
    inner = ttk.Frame(shell, style="Card.TFrame")
    inner.pack(fill="both", expand=True, padx=padx, pady=pady)
    return shell, inner


def _apply_window_icon(window) -> None:
    """给窗口装 .ico 图标；资源不在就静默跳过（不影响任何功能）。"""
    try:
        path = _asset_path("app.ico")
        if path.is_file():
            window.iconbitmap(default=str(path))
    except (tk.TclError, OSError):
        pass


def audio_filetypes() -> list[tuple[str, str]]:
    """文件选择框的过滤项：受支持扩展名拼成一条通配符串。"""
    patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
    return [("音频文件", patterns), ("所有文件", "*.*")]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.summary: batch.RunSummary | None = None
        self._mode = MODE_FOLDER
        self._running_mode = MODE_FOLDER  # 本次运行用的模式（状态行文案用）
        self._paths = {MODE_FOLDER: "", MODE_FILE: ""}  # 两种模式各自记住上次的路径
        self.settings = settings_module.load_settings()  # AI 转写服务设置（本机持久化）
        self.preview_bridge = PreviewBridge(self.queue, self.stop_event)

        _apply_theme(root)
        root.configure(bg=COLOR_BG)
        _apply_window_icon(root)
        root.title(f"{APP_NAME} v{__version__}")
        root.geometry("660x500")
        root.minsize(560, 440)
        self._build_widgets()
        root.after(100, self._poll_queue)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, style="Canvas.TFrame")
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        # ---- 上半张卡片：目标 + 选项 + 操作 + 进度，一眼能扫完 ----
        panel, body = _card(outer)
        panel.pack(fill="x")

        # 提取范围：两个分段按钮
        row0 = ttk.Frame(body, style="Card.TFrame")
        row0.pack(fill="x")
        ttk.Label(row0, text="提取范围：", style="Field.TLabel").pack(
            side="left", padx=(0, 10)
        )
        self.mode_var = tk.StringVar(value=MODE_FOLDER)
        self.mode_radios = []
        for value, text in MODE_RADIOS:
            radio = ttk.Radiobutton(
                row0,
                text=text,
                value=value,
                variable=self.mode_var,
                command=self._on_mode_change,
                style="Segment.TRadiobutton",
            )
            radio.pack(side="left")
            self.mode_radios.append(radio)

        # 路径：用 grid 让「支持格式」正好对齐在输入框左边缘
        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.pack(fill="x", pady=(12, 0))
        row1.columnconfigure(0, minsize=self._label_column_width())
        row1.columnconfigure(1, weight=1)
        self.path_label = ttk.Label(row1, text=MODE_LABELS[MODE_FOLDER],
                                    style="Field.TLabel")
        self.path_label.grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.path_var).grid(
            row=0, column=1, sticky="ew"
        )
        self.browse_btn = ttk.Button(row1, text="浏览…", command=self._browse)
        self.browse_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))
        hint = ttk.Label(
            row1,
            text=f"支持格式：{SUPPORTED_EXTENSIONS_TEXT}",
            style="Hint.TLabel",
        )
        # 跨到「浏览…」下面，再用动态折行兜住窄窗口，一行放不下也不会被切掉
        hint.grid(row=1, column=1, columnspan=2, sticky="we", pady=(7, 0))
        hint.bind("<Configure>", self._fit_wrap)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(14, 12))

        # 勾选项：四个开关 + 一个次级入口
        row2 = ttk.Frame(body, style="Card.TFrame")
        row2.pack(fill="x")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.recursive_var = tk.BooleanVar(value=True)
        self.online_var = tk.BooleanVar(value=True)
        self.ai_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row2, text="覆盖已有 lrc", variable=self.overwrite_var,
            style="Round.TCheckbutton",
        ).pack(side="left", padx=(0, 16))
        self.recursive_check = ttk.Checkbutton(
            row2, text="包含子文件夹", variable=self.recursive_var,
            style="Round.TCheckbutton",
        )
        self.recursive_check.pack(side="left", padx=(0, 16))
        ttk.Checkbutton(
            row2, text="在线兜底", variable=self.online_var,
            style="Round.TCheckbutton",
        ).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(
            row2, text="AI 兜底", variable=self.ai_var,
            style="Round.TCheckbutton",
        ).pack(side="left")
        self.ai_settings_btn = ttk.Button(
            row2, text="AI 设置…", command=self._open_ai_settings, style="Small.TButton"
        )
        self.ai_settings_btn.pack(side="right")

        # 操作行：主操作靠左，结果相关的靠右
        row3 = ttk.Frame(body, style="Card.TFrame")
        row3.pack(fill="x", pady=(16, 0))
        self.start_btn = ttk.Button(
            row3, text="开始提取", command=self._start, style="Primary.TButton"
        )
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(
            row3, text="停止", command=self._stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.export_btn = ttk.Button(
            row3, text="导出失败清单", command=self._export, state="disabled"
        )
        self.export_btn.pack(side="right")
        self.ai_pick_btn = ttk.Button(
            row3, text="AI 提取失败项…", command=self._open_ai_picker, state="disabled"
        )
        self.ai_pick_btn.pack(side="right", padx=(0, 8))

        # 进度 + 状态
        self.progress = ttk.Progressbar(
            body, mode="determinate", style="Slim.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", pady=(16, 9))
        self.status_var = tk.StringVar(value="等待开始")
        self.status_label = ttk.Label(
            body, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        )
        self.status_label.pack(fill="x")
        self.status_label.bind("<Configure>", self._fit_wrap)

        # ---- 下半张卡片：日志 ----
        log_panel, self.log_text = _text_area(outer, height=10, state="disabled")
        log_panel.pack(fill="both", expand=True, pady=(12, 0))

    def _label_column_width(self) -> int:
        """路径标签列的固定宽度：切模式时输入框不会左右跳。"""
        try:
            return tkfont.Font(font=_font(10)).measure("歌曲文件夹：") + 10
        except tk.TclError:
            return 96

    def _fit_wrap(self, event) -> None:
        """让说明 / 状态文字按可用宽度折行：窄窗口下宁可换行，也不要被截断。"""
        widget = event.widget
        if event.width > 20 and widget.cget("wraplength") != event.width:
            widget.configure(wraplength=event.width)

    def _set_status(self, text: str, tone: str = "idle") -> None:
        """状态行按结果着色：进行中主色、成功绿、失败红、其余中性灰。"""
        self.status_var.set(text)
        self.status_label.configure(
            foreground={
                "busy": COLOR_ACCENT,
                "ok": COLOR_SUCCESS,
                "error": COLOR_ERROR,
            }.get(tone, COLOR_MUTED)
        )

    # ------------------------------------------------------------------
    # 按钮动作
    def _on_mode_change(self) -> None:
        """切换「整个文件夹 / 单个文件」：换标签、换记忆的路径、按模式开关无关选项。"""
        new_mode = self.mode_var.get()
        if new_mode == self._mode:
            return
        self._paths[self._mode] = self.path_var.get()  # 存下旧模式填过的路径
        self._mode = new_mode
        self.path_var.set(self._paths[new_mode])  # 换回新模式上次用的路径
        self.path_label.config(text=MODE_LABELS[new_mode])
        # 「包含子文件夹」只对文件夹模式有意义
        self.recursive_check.config(
            state="disabled" if new_mode == MODE_FILE else "normal"
        )

    def _browse(self) -> None:
        if self.mode_var.get() == MODE_FILE:
            chosen = filedialog.askopenfilename(
                title="选择音频文件", filetypes=audio_filetypes()
            )
        else:
            chosen = filedialog.askdirectory(title="选择歌曲文件夹")
        if chosen:
            self.path_var.set(chosen)

    def _start(self) -> None:
        mode = self.mode_var.get()
        raw = self.path_var.get().strip()
        if mode == MODE_FILE:
            path = Path(raw) if raw else None
            if path is None or not path.is_file():
                messagebox.showwarning(APP_NAME, "请先选择一个存在的音频文件")
                return
            if not batch.is_supported_audio(path):
                messagebox.showwarning(
                    APP_NAME,
                    f"不支持的音频格式：{path.suffix or '（没有扩展名）'}\n"
                    f"支持格式：{SUPPORTED_EXTENSIONS_TEXT}",
                )
                return
            # 单文件模式只处理这一个文件，不走文件夹扫描，也没有「子文件夹」这回事
            job = functools.partial(batch.run_files, [path])
        else:
            if not raw or not Path(raw).is_dir():
                messagebox.showwarning(APP_NAME, "请先选择一个存在的歌曲文件夹")
                return
            job = functools.partial(
                batch.run_batch, raw, recursive=self.recursive_var.get()
            )

        if self.ai_var.get() and not self._ai_ready():
            return

        kwargs = self._worker_kwargs(use_ai=self.ai_var.get())
        self._running_mode = mode
        self._launch(job, kwargs, "正在处理…" if mode == MODE_FILE else "正在扫描…")

    def _ai_ready(self) -> bool:
        """勾了「AI 兜底」时的最低可用性检查；不满足时弹提示并返回 False。"""
        if self.settings.engine != settings_module.ENGINE_CLOUD:
            return True  # 本机引擎不需要密钥；依赖缺失会在逐首处理时报清楚的错
        if settings_module.clean_api_key(self.settings.api_key):
            return True
        messagebox.showwarning(
            APP_NAME,
            "已勾选「AI 兜底」，但还没有填写云端服务的密钥。\n\n"
            "请点旁边的「AI 设置…」填写（在服务商网站免费注册即可拿到），"
            "或取消勾选「AI 兜底」。",
        )
        return False

    def _worker_kwargs(self, *, use_ai: bool) -> dict:
        return dict(
            overwrite=self.overwrite_var.get(),
            use_online=self.online_var.get(),
            use_ai=use_ai,
            ai_settings=self.settings,
            confirm_cb=self.preview_bridge.confirm,
            stop_event=self.stop_event,
            progress_cb=lambda done, total, result=None: self.queue.put(
                ("progress", done, total, result)
            ),
            log_cb=lambda msg: self.queue.put(("log", msg)),
        )

    def _launch(self, job, kwargs: dict, status_text: str) -> None:
        self.stop_event.clear()
        self.summary = None
        self._set_running(True)
        self._log_clear()
        self.progress.config(value=0, maximum=100)
        self._set_status(status_text, "busy")
        self.worker = threading.Thread(
            target=self._run_worker, args=(job, kwargs), daemon=True
        )
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        """运行中锁住「开始」与所有会改变目标的控件，避免中途换目标对不上日志。"""
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        for widget in (self.browse_btn, self.ai_settings_btn, *self.mode_radios):
            widget.config(state="disabled" if running else "normal")
        self.recursive_check.config(
            state="disabled"
            if running or self.mode_var.get() == MODE_FILE
            else "normal"
        )
        # 失败清单挑选也一样：跑新任务时旧清单不再可用，跑完按结果重新启用
        self.ai_pick_btn.config(state="disabled")
        if running:
            # 本次结果还没出来，别让用户导出上一轮的失败清单
            self.export_btn.config(state="disabled")

    def _run_worker(self, job, kwargs: dict) -> None:
        try:
            self.queue.put(("done", job(**kwargs)))
        except Exception as exc:  # 线程里的异常必须送回主线程展示，不能静默消失
            self.queue.put(("failed", str(exc)))

    def _stop(self) -> None:
        self.stop_event.set()
        self.stop_btn.config(state="disabled")

    def _export(self) -> None:
        if not self.summary or not self.summary.failures:
            return
        path = filedialog.asksaveasfilename(
            title="导出失败清单",
            defaultextension=".txt",
            initialfile="失败清单.txt",
            filetypes=[("文本文件", "*.txt")],
        )
        if not path:
            return
        out = batch.export_failure_report(self.summary, path)
        messagebox.showinfo(APP_NAME, f"已导出 {len(self.summary.failures)} 条到：\n{out}")

    # ------------------------------------------------------------------
    # AI 兜底：设置、失败清单挑选、预览

    def _open_ai_settings(self) -> None:
        def on_save(new_settings: settings_module.Settings) -> None:
            self.settings = new_settings
            settings_module.save_settings(new_settings)

        AiSettingsDialog(self.root, self.settings, on_save)

    def _open_ai_picker(self) -> None:
        """失败清单里挑「未找到歌词」的歌交给 AI 转写（其它状态各有原因，不硬转）。"""
        not_found = [
            (path, reason)
            for path, status, reason in (self.summary.failures if self.summary else [])
            if status == Status.NOT_FOUND.value
        ]
        if not not_found:
            return
        AiPickerDialog(self.root, not_found, self._start_ai_for_files)

    def _start_ai_for_files(self, paths) -> None:
        """挑出的歌再跑一遍完整流水线（在线重查 + AI 兜底 + 预览确认）。"""
        self._running_mode = MODE_AI
        kwargs = self._worker_kwargs(use_ai=True)
        kwargs["use_online"] = True  # 上次可能只是没勾在线兜底，先让在线再试一遍
        job = functools.partial(batch.run_files, list(paths))
        self._launch(job, kwargs, "AI 转写中…")

    def _show_ai_preview(self, path, lines, synced, holder, done) -> None:
        def on_done(result) -> None:
            holder["result"] = result
            done.set()

        AiPreviewDialog(self.root, Path(path).name, lines, synced, on_done)

    # ------------------------------------------------------------------
    # 主线程轮询工作线程的消息
    def _poll_queue(self) -> None:
        if not self.log_text.winfo_exists():
            # 控件已被销毁（窗口关了、或测试里换了界面），别再排下一次轮询
            return
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1])
                elif kind == "progress":
                    _, done, total, _result = item
                    if total:
                        self.progress.config(maximum=total, value=done)
                    self._set_status(f"处理中 {done}/{total}", "busy")
                elif kind == "done":
                    self._on_done(item[1])
                elif kind == "failed":
                    self._on_failed(item[1])
                elif kind == "ai_preview":
                    # 工作线程的 AI 转写结果弹预览窗；对话框决定后由桥唤醒工作线程
                    _, path, lines, synced, holder, done = item
                    self._show_ai_preview(path, lines, synced, holder, done)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_done(self, summary: batch.RunSummary) -> None:
        self.summary = summary
        self._set_running(False)
        if summary.failures:
            self.export_btn.config(state="normal")
            if any(
                status == Status.NOT_FOUND.value for _, status, _ in summary.failures
            ):
                self.ai_pick_btn.config(state="normal")
        tail = "（已停止）" if summary.stopped else ""
        scope = RUN_SCOPE_LABELS.get(self._running_mode, "文件夹")
        self._set_status(
            f"完成{tail}（{scope}）：共 {summary.total} 个，写入 {summary.written}"
            f"（同步 {summary.synced} / 纯文本 {summary.plain}），"
            f"未找到 {summary.not_found}，纯音乐 {summary.instrumental}，"
            f"跳过 {summary.skipped}，失败 {summary.errors}",
            "idle" if summary.stopped else ("error" if summary.errors else "ok"),
        )

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self._set_status("运行出错", "error")
        messagebox.showerror(APP_NAME, f"批量处理时发生异常：\n{message}")

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_clear(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")


# ----------------------------------------------------------------------
# AI 兜底相关的桥与对话框


class PreviewBridge:
    """工作线程与 tkinter 主线程之间的预览桥。

    工作线程提交转写结果并阻塞等待；主线程弹预览窗，用户改完词或跳过后
    唤醒工作线程继续。用户点「停止」时不再等待，剩余预览按跳过处理。
    """

    def __init__(self, queue, stop_event):
        self.queue = queue
        self.stop_event = stop_event

    def confirm(self, path, lines, synced):
        holder = {}
        done = threading.Event()
        self.queue.put(("ai_preview", path, lines, synced, holder, done))
        while not done.wait(0.2):
            if self.stop_event.is_set():
                return None
        return holder.get("result")


class ModalDialog(tk.Toplevel):
    """带防御性模态抓取的对话框基类（窗口尚未可见时跳过抓取，便于自动化测试）。"""

    def __init__(self, master):
        super().__init__(master)
        _apply_theme(self)  # 单独构造对话框时，也保证和主窗是同一套样式
        self.configure(bg=COLOR_BG)

    def _try_grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _place_near(self, master) -> None:
        """把对话框摆在主窗中间偏上（纯观感，取不到尺寸就交给窗口管理器）。"""
        try:
            self.update_idletasks()
            width = self.winfo_width()
            height = self.winfo_height()
            if width <= 1:
                width = self.winfo_reqwidth()
            if height <= 1:
                height = self.winfo_reqheight()
            master_w = master.winfo_width()
            master_h = master.winfo_height()
            if master_w <= 1:
                master_w, master_h = 900, 600
            x = master.winfo_rootx() + max(0, (master_w - width) // 2)
            y = master.winfo_rooty() + max(0, (master_h - height) // 3)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass


class AiPreviewDialog(ModalDialog):
    """AI 转写结果预览：可改错字再保存，也可跳过不写。

    带时间戳的行保存后仍是同步歌词；把时间戳删掉再保存则降级为纯文本歌词。
    """

    def __init__(self, master, filename: str, lines, synced: bool, on_done):
        super().__init__(master)
        self.title(f"AI 转写结果 —— {filename}")
        self.on_done = on_done
        # 只给下限、不写死尺寸：内容（含高 DPI 放大后的字）永远放得下
        self.minsize(660, 540)

        ttk.Label(
            self,
            style="Hint.TLabel",
            justify="left",
            wraplength=608,
            text="检查下面的歌词，改完错字点「保存 .lrc」。\n"
                 "行首的 [分:秒.毫秒] 就是这一行的开始时间，保留它就是带时间轴的歌词，"
                 "全部删掉则保存为纯文本。",
        ).pack(fill="x", padx=18, pady=(16, 10))

        text_panel, self.text = _text_area(self, height=14)
        text_panel.pack(fill="both", expand=True, padx=18)
        if synced:
            content = "\n".join(f"[{format_timestamp(ms)}]{body}" for ms, body in lines)
        else:
            content = "\n".join(str(line) for line in lines)
        self.text.insert("1.0", content)

        row = ttk.Frame(self, style="Canvas.TFrame")
        row.pack(fill="x", padx=18, pady=(12, 16))
        ttk.Button(
            row, text="保存 .lrc", command=self._save, style="Primary.TButton"
        ).pack(side="right")
        ttk.Button(
            row, text="跳过这首", command=self._finish_skip, style="Ghost.TButton"
        ).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self._finish_skip)
        self.transient(master)
        self._place_near(master)
        self._try_grab()

    def _save(self) -> None:
        text = self.text.get("1.0", "end-1c")
        parsed = parse_lrc_text(text)
        if parsed:
            self._finish((parsed, True))
            return
        plain = [line.strip() for line in text.splitlines() if line.strip()]
        self._finish((plain, False))

    def _finish_skip(self) -> None:
        self._finish(None)

    def _finish(self, result) -> None:
        callback = self.on_done
        self.on_done = None  # 关窗路径与按钮路径只会走到一个，防重复回调
        self.destroy()
        if callback is not None:
            callback(result)


class AiSettingsDialog(ModalDialog):
    """AI 转写服务设置：引擎、云端三件套（地址/模型/密钥）、本地模型、人声分离。"""

    LOCAL_MODELS = ("tiny", "base", "small", "medium", "large-v3")

    def __init__(self, master, current: settings_module.Settings, on_save):
        super().__init__(master)
        self.title("AI 设置")
        self.on_save = on_save
        self.resizable(False, False)

        body = ttk.Frame(self, style="Canvas.TFrame")
        body.pack(fill="both", expand=True, padx=16, pady=14)

        # 所有设置收在一张卡片里，小节之间用细分隔线——不再用嵌套的方框
        card, inner = _card(body, padx=18, pady=16)
        card.pack(fill="x")

        # 引擎选择
        self.engine_var = tk.StringVar(value=current.engine)
        self.engine_var.trace_add("write", lambda *_: self._refresh_panels())
        engine_row = ttk.Frame(inner, style="Card.TFrame")
        engine_row.pack(fill="x")
        ttk.Label(engine_row, text="转写引擎：", style="Field.TLabel").pack(
            side="left", padx=(0, 12)
        )
        for value, text in settings_module.ENGINE_LABELS.items():
            ttk.Radiobutton(
                engine_row, text=text, value=value, variable=self.engine_var,
                style="Round.TRadiobutton",
            ).pack(side="left", padx=(0, 18))

        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=(14, 12))

        # 云端 / 本机两个面板轮流放在这个槽里（顺序固定，不会被后面的控件挤到下面）
        self._panel_slot = ttk.Frame(inner, style="Card.TFrame")
        self._panel_slot.pack(fill="x")

        self.cloud_panel = ttk.Frame(self._panel_slot, style="Card.TFrame")
        ttk.Label(self.cloud_panel, text="云端接口", style="Section.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        provider_row = ttk.Frame(self.cloud_panel, style="Card.TFrame")
        provider_row.pack(fill="x", pady=(0, 4))
        ttk.Label(provider_row, text="服务商：", style="Field.TLabel").pack(
            side="left", padx=(0, 8)
        )
        # 下拉里显示中文标签，保存时再映射回内部值
        self._provider_label_to_value = dict(
            (label, value) for value, label in settings_module.PROVIDER_LABELS.items()
        )
        self.provider_var = tk.StringVar(
            value=settings_module.PROVIDER_LABELS.get(
                current.provider,
                settings_module.PROVIDER_LABELS[settings_module.PROVIDER_DASHSCOPE],
            )
        )
        self.provider_var.trace_add("write", lambda *_: self._refresh_panels())
        ttk.Combobox(
            provider_row, textvariable=self.provider_var,
            values=tuple(settings_module.PROVIDER_LABELS.values()),
            state="readonly", width=24,
        ).pack(side="left")

        # 百炼面板（默认服务商）
        self.dashscope_panel = ttk.Frame(self.cloud_panel, style="Card.TFrame")
        self.api_key_var = tk.StringVar(value=current.api_key)
        self._entry_row(self.dashscope_panel, "密钥：", self.api_key_var, show="*")
        ttk.Label(
            self.dashscope_panel,
            style="Hint.TLabel",
            justify="left",
            wraplength=520,
            text="百炼控制台 bailian.console.aliyun.com 右上角头像 →「API-KEY」→"
                 "「创建我的 API-KEY」（国内手机号注册）。\n"
                 "Paraformer 引擎：词级时间戳，直接出带时间轴歌词"
                 "（新开送免费额度，用完约 0.29 元/小时）。",
        ).pack(fill="x", pady=(4, 0))

        # OpenAI 兼容面板（Groq 等）
        self.openai_panel = ttk.Frame(self.cloud_panel, style="Card.TFrame")
        self.base_url_var = tk.StringVar(value=current.base_url)
        self.model_var = tk.StringVar(value=current.model)
        self._entry_row(self.openai_panel, "接口地址：", self.base_url_var)
        self._entry_row(self.openai_panel, "模型名：", self.model_var)
        self._entry_row(self.openai_panel, "密钥：", self.api_key_var, show="*")
        ttk.Label(
            self.openai_panel,
            style="Hint.TLabel",
            justify="left",
            wraplength=520,
            text="默认 Groq：在 console.groq.com 免费注册拿密钥（部分网络打不开该网页）。\n"
                 "任何 OpenAI 兼容的转写服务都能用，改地址、模型名和密钥即可。",
        ).pack(fill="x", pady=(4, 0))

        self.local_panel = ttk.Frame(self._panel_slot, style="Card.TFrame")
        ttk.Label(
            self.local_panel, text="本机模型（离线，需装增强包）", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 8))
        self.local_model_var = tk.StringVar(value=current.local_model)
        row = ttk.Frame(self.local_panel, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="Whisper 型号：", style="Field.TLabel").pack(
            side="left", padx=(0, 8)
        )
        ttk.Combobox(
            row, textvariable=self.local_model_var,
            values=self.LOCAL_MODELS, state="readonly", width=12,
        ).pack(side="left")
        ttk.Label(
            self.local_panel,
            style="Hint.TLabel",
            justify="left",
            wraplength=520,
            text="需先安装增强包：命令行执行 pip install whisper-timestamped，"
                 "并确保 ffmpeg 可用。\n注意：安装版（exe）暂不支持本机引擎，"
                 "需用源码方式运行才能安装增强包（详见 README）。",
        ).pack(fill="x", pady=(4, 0))

        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=(14, 12))

        self.separate_var = tk.BooleanVar(value=current.separate_vocals)
        ttk.Checkbutton(
            inner,
            style="Round.TCheckbutton",
            text="人声分离增强（先剥离伴奏再转写，更准但更慢；需装 Demucs 增强包，未装自动跳过）",
            variable=self.separate_var,
        ).pack(anchor="w")

        btns = ttk.Frame(body, style="Canvas.TFrame")
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(
            btns, text="保存", command=self._save, style="Primary.TButton"
        ).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.transient(master)
        self._refresh_panels()
        self._place_near(master)
        self._try_grab()

    def _entry_row(self, parent, label: str, var, show=None) -> None:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=10, style="Field.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=var, show=show).pack(
            side="left", fill="x", expand=True
        )

    def _refresh_panels(self) -> None:
        """按引擎与服务商切换面板：本机/云端，百炼/OpenAI 兼容。"""
        if self.engine_var.get() == settings_module.ENGINE_LOCAL:
            self.cloud_panel.pack_forget()
            self.local_panel.pack(fill="x")
            return
        self.local_panel.pack_forget()
        self.cloud_panel.pack(fill="x")
        provider = self._provider_label_to_value.get(self.provider_var.get())
        if provider == settings_module.PROVIDER_OPENAI:
            self.dashscope_panel.pack_forget()
            self.openai_panel.pack(fill="x")
        else:
            self.openai_panel.pack_forget()
            self.dashscope_panel.pack(fill="x")

    def _save(self) -> None:
        provider = self._provider_label_to_value.get(
            self.provider_var.get(), settings_module.PROVIDER_DASHSCOPE
        )
        settings = settings_module.Settings(
            engine=self.engine_var.get(),
            provider=provider,
            base_url=self.base_url_var.get().strip() or settings_module.DEFAULT_BASE_URL,
            # 密钥剥掉随手粘进来的注释/空白，只留令牌（带中文会让请求头崩掉）
            api_key=settings_module.clean_api_key(self.api_key_var.get()),
            model=self.model_var.get().strip() or settings_module.DEFAULT_MODEL,
            local_model=self.local_model_var.get() or settings_module.DEFAULT_LOCAL_MODEL,
            separate_vocals=self.separate_var.get(),
        )
        self.destroy()
        self.on_save(settings)


class AiPickerDialog(ModalDialog):
    """失败清单挑选：选「未找到歌词」的歌交给 AI 转写。"""

    def __init__(self, master, failures, on_start):
        super().__init__(master)
        self.title("AI 提取失败项")
        self.on_start = on_start
        self.minsize(600, 440)

        ttk.Label(
            self,
            style="Hint.TLabel",
            justify="left",
            wraplength=548,
            text=f"共 {len(failures)} 首没找到歌词。选中要交给 AI 转写的歌"
                 f"（Ctrl/Shift 多选，Ctrl+A 全选），点「开始 AI 转写」。",
        ).pack(fill="x", padx=18, pady=(16, 10))

        list_panel = tk.Frame(self, bg=COLOR_SURFACE, borderwidth=0,
                              highlightbackground=COLOR_BORDER, highlightthickness=1)
        list_panel.pack(fill="both", expand=True, padx=18)
        self.listbox = tk.Listbox(
            list_panel, selectmode="extended", width=1, height=16,
            bg=COLOR_SURFACE, fg=COLOR_INK, font=_font(10),
            relief="flat", borderwidth=0, highlightthickness=0,
            selectbackground=COLOR_ACCENT_SOFT, selectforeground=COLOR_ACCENT,
            activestyle="none", exportselection=False,
        )
        bar = ttk.Scrollbar(list_panel, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8)
        for path, _reason in failures:
            self.listbox.insert("end", path)
        self.listbox.bind("<Control-a>", self._select_all)

        row = ttk.Frame(self, style="Canvas.TFrame")
        row.pack(fill="x", padx=18, pady=(12, 16))
        ttk.Button(
            row, text="开始 AI 转写", command=self._start, style="Primary.TButton"
        ).pack(side="right")
        ttk.Button(row, text="全选", command=self._select_all).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(row, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.transient(master)
        self._place_near(master)
        self._try_grab()

    def _select_all(self, event=None):
        self.listbox.selection_set(0, "end")
        return "break"

    def _start(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        paths = [Path(self.listbox.get(index)) for index in selection]
        callback = self.on_start
        self.on_start = None
        self.destroy()
        if callback is not None:
            callback(paths)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
