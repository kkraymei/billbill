"""Fetch a fixed official llama.cpp build and verify it before extraction."""
import hashlib
import json
import platform
from pathlib import Path
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'b10809'
PACKAGES = {
    ('win32', 'AMD64'): ('win-vulkan-x64.zip', 'vulkan', '97e50b3ef0cdd2cb4d5afd446a9006b3496bee6c0d0ba7083d32f36075771870'),
    ('darwin', 'arm64'): ('macos-arm64.tar.gz', 'metal', '7d692df9e1e386e62f1c12b843903218041e6cd74c9415aa39a7ed3176f9eaa2'),
    ('darwin', 'x86_64'): ('macos-x64.tar.gz', 'metal', '13b34aa8a5d87341a21065a83f54a8167e1aaa6fe0d66065de01632a1ed64be6'),
}


def main():
    suffix, backend, digest = PACKAGES[(sys.platform, platform.machine())]
    name = f'llama-{VERSION}-bin-{suffix}'
    target = ROOT / 'runtime' / 'llama.cpp' / backend
    target.mkdir(parents=True, exist_ok=True)
    archive = ROOT / 'runtime' / name
    urllib.request.urlretrieve(f'https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{name}', archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('llama.cpp checksum mismatch')
    if name.endswith('.zip'):
        with zipfile.ZipFile(archive) as package:
            package.extractall(target)
    else:
        with tarfile.open(archive) as package:
            package.extractall(target, filter='data')
    urllib.request.urlretrieve(f'https://raw.githubusercontent.com/ggml-org/llama.cpp/{VERSION}/LICENSE', target / 'LICENSE')
    (target / 'runtime.json').write_text(json.dumps({'version': VERSION, 'backend': backend, 'sha256': digest}), encoding='utf-8')
    print(f'Verified llama.cpp {VERSION}: {suffix}')


if __name__ == '__main__':
    main()
