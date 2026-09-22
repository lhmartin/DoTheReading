# PyInstaller spec: freezes the Python pipeline into a folder the desktop app
# ships, so the installed app needs no Python.
#
#     pyinstaller pipeline.spec --noconfirm
#
# Produces dist/pipeline/ with study_api[.exe] plus its runtime.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# pdfminer ships CMap data files, pypdfium2 a native library; missing either
# only shows up at runtime, so collect them wholesale.
for package in ("pdfplumber", "pdfminer", "pypdfium2", "pypdfium2_raw", "pytesseract"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    ["study_api.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    # Imported lazily by study_api/process_inbox, so PyInstaller can't see them.
    hiddenimports=hiddenimports + ["paper_qa_lib", "process_inbox", "qa_format", "quiz_history"],
    excludes=["tkinter", "matplotlib", "numpy.testing", "pytest"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="study_api",
    console=True,
    upx=False,
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="pipeline",
)
