"""Standard-library bootstrap: supported interpreter, dependency check and launch."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SUPPORTED = {(3, 10), (3, 11), (3, 12)}
IMPORTS = 'torch, torchvision, ultralytics, cv2, requests, numpy'


def probe(executable, dependencies=False):
    code = "import sys,struct,json; print(json.dumps({'version':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8,'executable':sys.executable}))"
    if dependencies:
        code += '; import ' + IMPORTS
    try:
        result = subprocess.run([str(executable), '-c', code], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=90)
        details = json.loads(result.stdout.splitlines()[0])
        details['supported'] = tuple(details['version'][:2]) in SUPPORTED and details['bits'] == 64
        details['dependencies_ok'] = result.returncode == 0
        details['error'] = result.stderr.strip()[-1500:]
        return details
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {'supported': False, 'dependencies_ok': False, 'error': str(exc)}


def existing_candidates():
    candidates = [ROOT / '.venv/Scripts/python.exe']
    candidates.extend(parent / '.venv-train/Scripts/python.exe' for parent in list(ROOT.parents)[:3])
    return [p for p in candidates if p.is_file()]


def ready_python():
    for candidate in existing_candidates():
        details = probe(candidate, dependencies=True)
        if details['supported'] and details['dependencies_ok']:
            return str(candidate), details
    return None, None


def supported_base(explicit=None):
    if explicit:
        details = probe(explicit)
        if not details['supported']:
            raise RuntimeError('--python must name a Python 3.10 / 3.11 / 3.12 64-bit interpreter. ' + str(details))
        return str(Path(explicit).resolve())
    if tuple(sys.version_info[:2]) in SUPPORTED and probe(sys.executable)['supported']:
        return sys.executable
    if shutil.which('py'):
        for version in ('3.12', '3.11', '3.10'):
            try:
                result = subprocess.run(['py', '-' + version, '-c', 'import sys; print(sys.executable)'],
                                        capture_output=True, text=True, timeout=30)
                candidate = result.stdout.strip()
                if result.returncode == 0 and candidate and probe(candidate)['supported']:
                    return candidate
            except (OSError, subprocess.SubprocessError):
                pass
    return None


def explain_missing():
    print('当前启动器使用 Python ' + sys.version.split()[0] + '：' + sys.executable)
    print('本交付包的动态环境支持 Python 3.10 / 3.11 / 3.12 64 位；仅安装 3.13 不满足当前安装脚本要求。')
    print('install_environment.cmd 安装项目依赖，不会安装 Python 本体。')
    print('可以保留 3.13，另装 Python 3.12 64 位并启用 Python Launcher。安装后重开 PowerShell：')
    print('  py -3.12 --version')
    print('  .\\install_environment.cmd')
    print('如果没有注册到 Launcher，可以指定实际解释器：')
    print('  .\\install_environment.cmd --python "C:\\实际安装目录\\python.exe"')
    print('官网：https://www.python.org/downloads/windows/')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--install', action='store_true')
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--python', dest='explicit_python')
    args, rest = parser.parse_known_args(argv)
    if rest and not args.run:
        parser.error('Unexpected arguments: ' + ' '.join(rest))
    executable, details = ready_python()
    if args.install and not executable:
        target = ROOT / '.venv/Scripts/python.exe'
        if target.exists():
            info = probe(target)
            if not info['supported']:
                raise RuntimeError('现有 src/host/.venv 的 Python 版本不符合要求。请先重命名该环境目录保留备份，再重新安装；不会自动删除。')
        else:
            base = supported_base(args.explicit_python)
            if not base:
                explain_missing()
                return 2
            print('CREATE_VENV_WITH=' + base, flush=True)
            subprocess.run([base, '-m', 'venv', str(ROOT / '.venv')], check=True)
        for command in ([str(target), '-m', 'pip', 'install', '--upgrade', 'pip'],
                        [str(target), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')]):
            subprocess.run(command, cwd=str(ROOT), check=True)
        executable, details = ready_python()
    if not executable:
        print('DEMO_ENV_READY=False')
        explain_missing()
        print('如果已安装受支持版本但缺少依赖，运行 .\\install_environment.cmd；安装失败请保留首处 pip 报错。')
        return 2
    print('DEMO_ENV_READY=True PYTHON=' + executable)
    print('Python=' + '.'.join(map(str, details['version'])) + ' BITS=' + str(details['bits']))
    if args.install:
        print('Environment ready. Run start_demo.cmd.（根目录也可运行 start_dynamic.cmd）')
    elif args.check:
        print('仅检查电脑环境；尚未启动网页。下一步运行 .\\start_dynamic.cmd')
    else:
        print('启动 Windows 网页后台。请等待 DYNAMIC_WEB=...；板端 8767 是处理接口，不是网页。', flush=True)
        return subprocess.call([executable, str(ROOT / 'app.py')] + rest, cwd=str(ROOT))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('ENVIRONMENT_ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
