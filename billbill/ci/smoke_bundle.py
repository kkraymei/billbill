"""Exercise the packaged app with synthetic files: bundle contents, every Office format, the window."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    executable = sys.argv[1]
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    with tempfile.TemporaryDirectory(prefix='billbill-smoke-') as folder:
        report = Path(folder) / 'selftest.json'
        subprocess.run([executable, '--selftest', str(report)], env=env, stdin=subprocess.DEVNULL, check=True, timeout=120)
        assert all(value is True for value in json.loads(report.read_text('utf-8')).values())

        output = Path(folder) / 'out.xlsx'
        fixtures = sorted(str(p) for p in (ROOT / 'tests/fixtures').iterdir())
        subprocess.run([executable, '--convert', str(output), *fixtures], env=env, stdin=subprocess.DEVNULL,
                       check=True, timeout=120)
        from openpyxl import load_workbook
        sheet = load_workbook(output).active
        amounts = sorted(sheet.cell(row, 3).value for row in range(2, sheet.max_row + 1))
        assert amounts == [0.5, 50.0, 1200.5, 8000.0], amounts   # the same bill in every format counts once

        subprocess.run([executable, '--smoke-window'], env=env, stdin=subprocess.DEVNULL, check=True, timeout=60)
    print('Bundle, Office formats and window passed')


if __name__ == '__main__':
    main()
