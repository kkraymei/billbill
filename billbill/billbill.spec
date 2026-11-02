# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    python -m PyInstaller billbill.spec --noconfirm

产出单文件 dist/billbill.exe。data/ 和 templates/ 在运行时创建在 exe 旁边
（见 billbill/config.py），不能放进临时解包目录，否则每次启动数据都会丢。
"""

import sys
from pathlib import Path, PurePath

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
ENV = Path(sys.executable).parent
IS_MAC = sys.platform == 'darwin'
IS_CONDA = (ENV / 'conda-meta').is_dir()

# 窗口图标和样式表里的箭头都是按文件读的，得跟着进包。
datas = [(str(ROOT / "assets"), "assets")]


def _runtime_tool(path):
    """llama.cpp 发行包里带着二十来个命令行工具（cli、bench、quantize、tts…），
    本程序只启动 llama-server。工具本身和它们各自的 *-impl 库都不打包；
    ggml 的各个后端（vulkan / metal / 各代 CPU）照旧全带，换机器才跑得起来。"""
    name = path.name.lower()
    stem = name[:-len(path.suffix)] if path.suffix.lower() in ('.exe', '.dll') else name
    if name in ('llama', 'llama.exe') or stem in ('rpc-server', 'ggml-rpc-server'):
        return True
    return stem.startswith('llama-') and stem not in ('llama-server', 'llama-server-impl', 'llama-common')


# Keep the verified native runtime and its licenses together. Models remain external.
runtime_dir = ROOT / 'runtime' / 'llama.cpp'
server_name = 'llama-server' if IS_MAC else 'llama-server.exe'
if not list(runtime_dir.rglob(server_name)):
    raise SystemExit('Missing bundled llama.cpp: run python ci/prepare_runtime.py before building')
for item in sorted(runtime_dir.rglob('*')):
    if item.is_file() and not _runtime_tool(item):
        datas.append((str(item), str(PurePath('runtime/llama.cpp') / item.parent.relative_to(runtime_dir))))
binaries = []
hiddenimports = []

# Conda Python/Pillow use shared DLLs outside package directories. PyInstaller does
# not reliably discover those transitively on Windows (sqlite, webp, decimal).
# 这样收得偏宽（连 Tcl/Tk、cairo、GLUT 都会进来），打包后再按实际链接关系剪掉。
if IS_CONDA:
    from PyInstaller.utils.hooks import conda_support
    for distribution in ('python', 'pillow'):
        binaries += conda_support.collect_dynamic_libs(distribution, dependencies=True)

# conda 环境打包时 _ctypes 找不到 ffi 库，手动带上。
for dll in ("ffi-8.dll", "ffi.dll", "libffi-8.dll"):
    candidate = ENV / "Library" / "bin" / dll
    if candidate.exists():
        binaries.append((str(candidate), "."))

# pdfplumber 依赖 pypdfium2 的原生库，pdfminer 有数据文件，都得完整收集。
for package in ("pdfplumber", "pdfminer", "pypdfium2", "pypdfium2_raw"):
    try:
        found = collect_all(package)
    except Exception:
        continue
    datas += found[0]
    binaries += found[1]
    hiddenimports += found[2]

hiddenimports += ["openpyxl", "xlrd", "chardet", "olefile", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"]

# Conda 的 Qt DLL 位于 Library/bin，Qt 自报的目录可能不存在。
for dll in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll"):
    candidate = ENV / "Library" / "bin" / dll
    if candidate.is_file():
        binaries.append((str(candidate), "."))

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 用不到的重量级依赖，排除掉能显著缩小体积。
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython",
        "notebook", "PIL.ImageQt", "PyQt5", "PySide2", "PySide6", "test", "unittest",
        # openpyxl 有 lxml 就用、没有就用标准库，结果一样；pip 环境本来就不装它。
        # AVIF 不在可导入的图片类型里，Tk 界面也用不到。
        "lxml", "PIL._avif", "PIL._imagingtk", "PIL.ImageTk",
        # 界面只用 QtWidgets 那一套，其余 Qt 模块连同它们的 DLL 一并排除。
        "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets", "PyQt6.QtWebChannel",
        "PyQt6.QtWebSockets", "PyQt6.QtDesigner", "PyQt6.QtHelp", "PyQt6.QtTest",
        "PyQt6.QtSql", "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtSvgWidgets",
        "PyQt6.QtXml", "PyQt6.QtDBus", "PyQt6.QtPrintSupport", "PyQt6.uic",
    ],
    noarchive=False,
    optimize=0,
)


def _inside(src, folder):
    try:
        Path(src).resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _is_top(dest):
    return len(PurePath(dest).parts) == 1


# llama-server 从自己所在的 runtime/ 目录加载依赖。依赖分析又把它们
# 在包根目录复制了一份（二十多 MB），那份没有人用。
a.binaries = [entry for entry in a.binaries
              if not (_is_top(entry[0]) and _inside(entry[1], runtime_dir))]

# 非交互的 Qt 平台插件：direct2d 是可选的另一套绘制后端，generic 是 TUIO 触控。
a.binaries = [entry for entry in a.binaries
              if not any(part in PurePath(entry[0]).as_posix()
                         for part in ('plugins/platforms/qdirect2d', 'plugins/generic/'))]

if IS_CONDA and not IS_MAC:
    # 只留下真正被链接到的 DLL：从 Python 扩展模块、Qt 插件、ctypes 加载的库
    # 和 llama.cpp 运行时出发，沿 PE 导入表走一遍，走不到的包根 DLL 剪掉。
    # VC/UCRT 运行库不管走不走得到都留着——llama-server 在没装 VC 运行库的机器上靠它们。
    from PyInstaller.depend import bindepend

    def _always(name):
        return name.startswith(('api-ms-win-', 'ucrtbase', 'vcruntime', 'msvcp140', 'python'))

    top = {PurePath(dest).name.lower(): src for dest, src, _ in a.binaries if _is_top(dest)}
    runtime_names = {item.name.lower() for item in runtime_dir.rglob('*')}
    queue = [src for dest, src, kind in a.binaries if kind == 'EXTENSION' or not _is_top(dest)]
    queue += [src for src, _ in datas if _inside(src, runtime_dir)]
    queue += [src for name, src in top.items() if _always(name)]
    reached = set()
    while queue:
        src = queue.pop()
        if src in reached or Path(src).suffix.lower() not in ('.dll', '.exe', '.pyd'):
            continue
        reached.add(src)
        from_runtime = _inside(src, runtime_dir)
        for name, _ in bindepend.get_imports(src):
            name = name.lower()
            if from_runtime and name in runtime_names:
                continue  # 运行时的兄弟库在 runtime/ 里，不算包根的
            if name in top:
                queue.append(top[name])
    a.binaries = [entry for entry in a.binaries
                  if not _is_top(entry[0]) or entry[1] in reached]

pyz = PYZ(a.pure)

if IS_MAC:
    from PIL import Image
    icon_path = ROOT / 'build' / 'BillBill.icns'
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ROOT / 'assets' / 'billbill.ico') as icon:
        icon.convert('RGBA').resize((1024, 1024)).save(icon_path)
else:
    icon_path = ROOT / 'assets' / 'billbill.ico'

exe = EXE(
    pyz,
    a.scripts,
    *([] if IS_MAC else [a.binaries, a.datas]),
    [],
    name="billbill",
    icon=str(icon_path),
    exclude_binaries=IS_MAC,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if IS_MAC:
    collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BillBill')
    app = BUNDLE(collection, name='BillBill.app', icon=str(icon_path),
                 bundle_identifier='com.billbill.desktop', version='1.0.0',
                 info_plist={'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
                             'CFBundleDisplayName': 'BillBill'})
