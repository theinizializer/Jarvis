"""Additive, content-addressed KyGlove releases. The reference sketch is input only."""
import hashlib
import json
import os
import threading
from pathlib import Path
import re
import shutil
import tempfile

from task_io import run_command

ROOT = Path(os.environ.get('KYGLOVE_ROOT') or Path(__file__).resolve().parents[1] / 'kyglove-firmware').expanduser().resolve()
SKETCH = 'KYGlove_MPU6050'
STORE = ROOT / 'jarvis-extensions'
BASE = '2026.11.11-ota5-gyro'
TESTS = ['logic', 'sensors', 'ota', 'tuning', 'balance', 'panel', 'diagnostics', 'mouse_pipeline', 'page']
CASES = ['nominale', 'limite', 'ripetizione']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def directory(revision):
    if not re.fullmatch(r'[0-9a-f]{24}', revision):
        raise ValueError('Identificatore revisione non valido.')
    return STORE / revision


def read_modules(revision):
    if revision == BASE:
        return []
    raw = (directory(revision) / 'modules.json').read_bytes()
    if digest(raw)[:24] != revision:
        raise ValueError('Sorgenti della revisione alterati.')
    return json.loads(raw)['modules']


def add(parent, name, code, tests):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name):
        raise ValueError('Usa un nome C++ minuscolo, massimo 32 caratteri.')
    if parent != BASE:
        release(parent)
    modules = read_modules(parent)
    if any(m['name'] == name for m in modules):
        raise ValueError('Nome già usato nella versione di partenza.')
    if not code.strip() or not tests.strip() or max(len(code), len(tests)) > 32000:
        raise ValueError('Servono codice e tre test, massimo 32.000 caratteri per campo.')
    modules.append(dict(name=name, code=code, tests=tests))
    raw = canonical(dict(parent=parent, modules=modules))
    rev = digest(raw)[:24]
    STORE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STORE) as tmp:
        staged = Path(tmp) / 'draft'
        staged.mkdir()
        (staged / 'modules.json').write_bytes(raw)
        dest = directory(rev)
        try:
            staged.rename(dest)
        except OSError:
            if not dest.is_dir() or (dest / 'modules.json').read_bytes() != raw:
                raise
    return {'revision': rev, 'parent': parent, 'directory': str(dest)}


def release(rev):
    root = directory(rev)
    read_modules(rev)
    proof = json.loads((root / 'verified' / 'report.json').read_bytes())
    data = (root / 'verified' / 'app.bin').read_bytes()
    if (proof['revision'] != rev or proof['sha256'] != digest(data)
            or proof['suite'] != TESTS or proof['extension_cases'] != CASES):
        raise ValueError('Il binario non corrisponde al rapporto dei test.')
    return proof, data


PUBLIC_MANIFEST = "62afe2b5feecb225bef161ef0745acf6d55284bb2d7556466f366bfc01ec6120"
LEGACY_MANIFEST = "af0c195dd9127536513a5d7e04ffcbf201fcf9b7e22390bafa319a79a674d956"


def baseline():
    manifest = (ROOT/'build-info.json').read_bytes()
    kind = digest(manifest)
    if kind not in {PUBLIC_MANIFEST, LEGACY_MANIFEST}:
        raise ValueError('Manifest OTA5 inatteso.')
    info = json.loads(manifest)
    if kind == PUBLIC_MANIFEST:
        private = json.loads((ROOT/'firmware_OTA/baseline.json').read_bytes())
        for path in ['firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin', SKETCH+'/KyGloveLocal.h']:
            info['sha256'][path] = private[Path(path).name]
    return info


def build(rev, cancel=None):
    cancel = cancel or threading.Event()
    root = directory(rev)
    modules = read_modules(rev)
    if not modules:
        raise ValueError('Seleziona una revisione con estensioni.')
    if (root / 'verified').exists():
        return release(rev)[0]
    info = baseline()
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        work = Path(tmp)
        sketch = work / SKETCH
        sketch.mkdir()
        for rel, expected in info['sha256'].items():
            data = (ROOT / rel).read_bytes()
            if digest(data) != expected:
                raise ValueError('Baseline diversa dal manifest: ' + rel)
            if Path(rel).parent == Path(SKETCH):
                (sketch / Path(rel).name).write_bytes(data)

        def run(argv, timeout=120):
            result = run_command(argv, cwd=work, text=True, cancel_event=cancel, timeout=timeout)
            if result.returncode:
                raise ValueError(f'{Path(argv[0]).name} fallito: ' + (result.stdout + result.stderr)[-3500:])

        calls = []
        for mod in modules:
            name = mod['name']
            header = sketch / ('addon_' + name + '.h')
            header.write_text(mod['code'])
            # No WASI, imports or filesystem for generated test executables.
            cases = work / 'cases.cpp'
            cases.write_text('#include "' + header.name + '"\n' + mod['tests'] + '\n' +
                             '\n'.join(f'extern "C" int test_{c}() {{ return {c}(); }}' for c in CASES))
            wasm = work / 'cases.wasm'
            run(['clang++', '--target=wasm32', '-std=c++17', '-nostdlib', '-Wl,--no-entry',
                 *['-Wl,--export=test_' + c for c in CASES], '-I', str(sketch), str(cases), '-o', str(wasm)])
            for case in CASES:
                run(['node', '-e', 'const fs=require("fs"); const m=new WebAssembly.Module(fs.readFileSync(process.argv[1]));'
                     'const ok=new WebAssembly.Instance(m,{}).exports["test_"+process.argv[2]]();'
                     'if(ok!==1)throw Error("Test fallito: "+process.argv[2]);', str(wasm), case], 8)
            unit = sketch / ('addon_' + name + '.cpp')
            unit.write_text('#include <Arduino.h>\n#include "' + header.name + '"\n'
                            + f'void jarvis_{name}(unsigned long now, bool writing) {{ {name}::tick(now, writing); }}\n')
            calls.append(f'jarvis_{name}')
        # ESP32 Arduino calls loop() from a separate translation unit.
        wrapper = sketch / 'jarvis_loop.cpp'
        wrapper.write_text('#include <Arduino.h>\n#include "GloveOta.h"\n'
                           'extern GloveOta ota; extern GlovePanel panel; extern bool writingMode;\n'
                           'extern "C" void __real__Z4loopv();\n' +
                           '\n'.join('extern void ' + fn + '(unsigned long, bool);' for fn in calls) +
                           '\nextern "C" void __wrap__Z4loopv() { __real__Z4loopv(); '
                           'if (ota.suspendInputs() || panel.testing()) return;\n' +
                           '\n'.join(fn + '(millis(), writingMode);' for fn in calls) + '\n}\n')
        version = 'jarvis-' + rev
        cfg = sketch / 'GloveWiFiConfig.h'
        cfg.write_text(cfg.read_text().replace('"' + BASE + '"', '"' + version + '"'))
        shutil.copytree(ROOT / 'tests', work / 'tests')
        run(['bash', str(work / 'tests/run_tests.sh')])
        run(['arduino-cli', 'compile', '--fqbn', info['fqbn'], '--warnings', 'all',
             '--build-property', 'compiler.c.elf.extra_flags=-Wl,--wrap=_Z4loopv',
             '--build-path', str(work / 'build'), '--output-dir', str(work / 'dist'), str(sketch)], 1200)
        data = (work / 'dist' / (SKETCH + '.ino.bin')).read_bytes()
        if (len(data) > min(s['bytes'] for s in info['ota_slots']) or data[0] != 0xe9
                or int.from_bytes(data[12:14], 'little') != 9 or version.encode() not in data
                or b'KYGlove-OTA-v1:ESP32S3' not in data):
            raise ValueError('Immagine OTA incompatibile.')
        proof = dict(revision=rev, version=version, sha256=digest(data), bytes=len(data),
                     suite=TESTS, extension_cases=CASES, modules=[m['name'] for m in modules])
        verified = work / 'verified'
        verified.mkdir()
        (verified / 'app.bin').write_bytes(data)
        (verified / 'report.json').write_bytes(canonical(proof))
        try:
            verified.rename(root / 'verified')
        except OSError:
            if not (root / 'verified').is_dir():
                raise
        return release(rev)[0]
