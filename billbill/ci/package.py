"""Create clean EXE/DMG artifacts after a native PyInstaller build."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, '-m', 'PyInstaller', 'billbill.spec', '--noconfirm', '--clean'], cwd=ROOT, check=True)
    if sys.platform == 'win32':
        executable = ROOT / 'dist/billbill.exe'
        label = 'windows-x64'
    else:
        executable = ROOT / 'dist/BillBill.app/Contents/MacOS/billbill'
        label = 'macos-' + ('arm64' if platform.machine() == 'arm64' else 'x64')

    # Never write self-test state into the artifact or application bundle.
    with tempfile.TemporaryDirectory(prefix='billbill-package-') as folder:
        env = dict(os.environ, BILLBILL_HOME=folder, PYTHONIOENCODING='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'ci/smoke_bundle.py'), str(executable)], env=env, check=True, timeout=300)

    output = ROOT / 'artifacts'
    output.mkdir(exist_ok=True)
    if sys.platform == 'win32':
        artifact = output / 'BillBill-windows-x64.exe'
        shutil.copy2(executable, artifact)
    else:
        artifact = output / f'BillBill-{label}.dmg'
        with tempfile.TemporaryDirectory(prefix='billbill-dmg-') as folder:
            stage = Path(folder)
            subprocess.run(['ditto', str(ROOT / 'dist/BillBill.app'), str(stage / 'BillBill.app')], check=True)
            (stage / 'Applications').symlink_to('/Applications')
            (stage / 'Read me.txt').write_text(
                'Drag BillBill.app to Applications.\n'
                'This build is ad-hoc signed, not Apple notarized.\n'
                'Data is stored in ~/Library/Application Support/BillBill.\n'
                'Models are not included; select your local GGUF files in Settings.\n', encoding='utf-8')
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(stage / 'BillBill.app')], check=True)
            subprocess.run(['hdiutil', 'create', '-volname', 'BillBill', '-srcfolder', str(stage),
                            '-format', 'UDZO', '-ov', str(artifact)], check=True)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / f'{artifact.name}.sha256').write_text(f'{digest}  {artifact.name}\n', encoding='ascii')
    print(f'Artifact: {artifact.name} ({artifact.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
