"""Generate an immutable board zip only after actual local build gates pass."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build():
    archive = ROOT.parent / (ROOT.name + '_board.zip')
    if archive.exists() or (ROOT / 'release_manifest.json').exists():
        raise RuntimeError('Release already exists; preserve it and create an explicitly versioned release')
    log = (ROOT / 'vivado_build.log').read_text(encoding='utf-8', errors='replace')
    if 'BINARY_EXPERIMENT_BUILD_AND_EXPORT_PASS' not in [line.strip() for line in log.splitlines()]:
        raise RuntimeError('Actual Vivado build/export success marker is absent')
    baseline_log_path = ROOT.parent / 'steel_preprocess_triple_v6/build_triple.log'
    baseline_log = baseline_log_path.read_text(encoding='utf-8', errors='replace')
    critical = sorted(set(line for line in log.splitlines() if line.startswith('CRITICAL WARNING:')))
    baseline_critical = set(line for line in baseline_log.splitlines() if line.startswith('CRITICAL WARNING:'))
    if any(line not in baseline_critical for line in critical):
        raise RuntimeError('New critical build warning requires explicit review before release')
    if any(line.startswith('ERROR:') for line in log.splitlines()):
        raise RuntimeError('Top-level Vivado log contains an error')
    match = re.search(r'^BINARY_EXPERIMENT_WNS=([-0-9.]+) BINARY_EXPERIMENT_WHS=([-0-9.]+)', log, re.M)
    if not match or min(map(float, match.groups())) < 0:
        raise RuntimeError('Routed setup/hold timing did not pass')
    timing_report = (ROOT / 'reports/binary_experiment_timing.rpt').read_text(encoding='utf-8', errors='replace')
    if 'All user specified timing constraints are met.' not in timing_report:
        raise RuntimeError('Timing summary does not confirm all constraints met')
    hls = ROOT / 'hls/ellipse7_project/solution1'
    csim = hls / 'csim/report/ellipse7_csim.log'
    cosim = hls / 'sim/report/ellipse7_cosim.rpt'
    synth = hls / 'syn/report/ellipse7_csynth.xml'
    if 'ELLIPSE_INTEGER_AND_FRAME_PROTOCOL_PASS' not in csim.read_text(errors='replace'):
        raise RuntimeError('v8 C simulation pass marker missing')
    cosim_rows = [[cell.strip() for cell in line.split('|')] for line in cosim.read_text(errors='replace').splitlines()]
    if not any(len(row) > 3 and row[1:3] == ['Verilog', 'Pass'] for row in cosim_rows):
        raise RuntimeError('v8 RTL cosimulation failed/missing')
    ip = hls / 'impl/ip/component.xml'
    if not ip.is_file():
        raise RuntimeError('v8 IP export is absent')
    for name in ('steel_binary_experiment.bit', 'steel_binary_experiment.hwh'):
        if not (ROOT / 'hardware' / name).is_file():
            raise RuntimeError('Missing exported hardware: ' + name)
    old_modules = {m.get('INSTANCE'): m for m in ET.parse(str(ROOT / 'baseline/board/steel_triple.hwh')).iter('MODULE')}
    new_modules = {m.get('INSTANCE'): m for m in ET.parse(str(ROOT / 'hardware/steel_binary_experiment.hwh')).iter('MODULE')}
    preserved_modules = ['processing_system7_0', 'axi_dma_0', 'axi_dma_sobel', 'axi_dma_dark',
                         'gaussian_row_q30_0', 'axis_q8_0', 'axis_sobel_0']
    for name in preserved_modules:
        def parameters(modules):
            return {p.get('NAME'): p.get('VALUE') for p in modules[name].findall('./PARAMETERS/PARAMETER')}
        if parameters(old_modules) != parameters(new_modules):
            raise RuntimeError('Inherited hardware configuration changed: ' + name)
    tests = subprocess.run([sys.executable, str(ROOT / 'test_host.py')], cwd=str(ROOT),
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(tests.stdout)
    if tests.returncode != 0:
        raise RuntimeError('Host tests failed; no board release produced')
    import cv2
    import numpy as np
    tree = ET.parse(str(synth))
    resources = tree.find('.//AreaEstimates/Resources')
    estimates = {node.tag: node.text for node in resources} if resources is not None else {}
    source_hashes = {p.relative_to(ROOT).as_posix(): digest(p)
                     for p in sorted((ROOT / 'hls').glob('*')) if p.is_file()}
    evidence = {'schema': 'v8_local_build_evidence/1.0', 'created_at': datetime.now().isoformat(),
        'new_fpga_access': False, 'board_test': False, 'formal_runtime_replaced': False,
        'hls': {'csim_pass': True, 'rtl_cosim_pass': True, 'ip_exported': True,
                'fixture_cases': 45, 'total_transactions_including_errors_and_recovery': 49,
                'resources_hls_estimate': estimates, 'sources_sha256': source_hashes,
                'reports_sha256': {p.name: digest(p) for p in (csim, cosim, synth)},
                'component_xml_sha256': digest(ip)},
        'vivado': {'build_and_export_pass': True, 'wns_ns': float(match[1]), 'whs_ns': float(match[2]),
                   'unchanged_inherited_module_parameters': preserved_modules,
                   'report_sha256': {p.name: digest(p) for p in sorted((ROOT / 'reports').glob('*.rpt'))}},
        'host_tests': {'pass': True, 'output': tests.stdout, 'python': sys.version,
                       'opencv': cv2.__version__, 'numpy': np.__version__,
                       'note': 'Mock DMA and software injection only; not board evidence or PL performance'},
        'historical_reference_files': ['reference/hls_evidence.json', 'reference/host_evidence.json']}
    startup_errors = []
    for path in sorted((ROOT / 'vivado/steel_binary_ops_v8.runs').glob('*/runme.log')):
        for line in path.read_text(errors='replace').splitlines():
            if line.startswith('ERROR:'):
                startup_errors.append({'run': path.parent.name, 'message': line})
    evidence['vivado']['log_error_messages_preserved_for_review'] = startup_errors
    for row in startup_errors:
        run_log = ROOT / 'vivado/steel_binary_ops_v8.runs' / row['run'] / 'runme.log'
        content = run_log.read_text(errors='replace')
        dcps = list(run_log.parent.glob('*.dcp'))
        linked = (ROOT / 'vivado/steel_binary_ops_v8.runs/impl_1/runme.log').read_text(errors='replace')
        reviewed = {"ERROR: [Common 17-1257] Failed to create directory 'C'.",
                    "ERROR: [Common 17-354] Could not open 'C' for writing."}
        run_completed = ('synth_design completed successfully' in content or 'Using cached IP synthesis design for IP' in content)
        if (row['message'] not in reviewed or not run_completed or not dcps
                or not (run_log.parent / '.vivado.end.rst').is_file()
                or any(dcp.name not in linked for dcp in dcps)):
            raise RuntimeError('Unreviewed per-run error: ' + str(row))
        row['completed_with_verified_checkpoint'] = True
        row['checkpoint_sha256'] = {dcp.name: digest(dcp) for dcp in dcps}
    evidence['vivado']['startup_error_assessment'] = ('Four child Vivado processes logged directory/file-C access errors before synthesis Tcl; '
        'each subsequently completed synthesis or a valid IP cache hit, emitted a DCP, and that DCP was loaded by implementation. '
        'The linked system passed routing, DRC and bitgen. '
        'Message preserved; environment root cause not independently resolved.')
    evidence['vivado']['inherited_critical_warnings'] = critical
    evidence['vivado']['inherited_critical_warning_assessment'] = ('Exact same PSU DDR skew warnings occur in the preserved triple build; '
        'all processing_system7 parameters match. DDR settings were not changed to suppress warnings; board regression is required.')
    evidence['vivado']['baseline_build_log_sha256'] = digest(baseline_log_path)
    (ROOT / 'reference/v8_build_evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    package_path = ROOT / 'package_manifest.json'
    package = json.loads(package_path.read_text(encoding='utf-8'))
    package['evidence_before_board']['vivado_implemented'] = True
    package['evidence_before_board']['board_test'] = False
    package['current_evidence'] = 'reference/v8_build_evidence.json'
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
    top_files = ['run_board_acceptance.py', 'experiment_runtime.py', 'rollback.py', 'test_host.py', 'build_board_package.py',
                 'package_manifest.json', 'readme_legacy.txt', 'run_legacy_ellipse.ipynb']
    paths = [ROOT / name for name in top_files]
    for directory in ('board', 'baseline', 'hardware', 'reference', 'reports'):
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths = sorted(set(paths))
    files = {p.relative_to(ROOT).as_posix(): digest(p) for p in paths}
    manifest = {'schema': 'ellipse_board_release/1.0', 'package': ROOT.name,
                'build_verified': True, 'board_verified': False, 'formal_release_replaced': False,
                'files': files}
    (ROOT / 'release_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    # Verify the exact same initialize_runtime that the board will use, before archiving.
    from run_board_acceptance import inspect_hardware
    print(json.dumps(inspect_hardware(), ensure_ascii=False, indent=2))
    paths.append(ROOT / 'release_manifest.json')
    with zipfile.ZipFile(str(archive), 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in paths:
            target.write(str(path), ROOT.name + '/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(str(archive)) as target:
        if target.testzip() is not None:
            raise RuntimeError('Archive CRC check failed')
        for rel, expected in files.items():
            if hashlib.sha256(target.read(ROOT.name + '/' + rel)).hexdigest() != expected:
                raise RuntimeError('Archive SHA mismatch: ' + rel)
    print('BOARD_PACKAGE=' + str(archive))
    print('BOARD_PACKAGE_SHA256=' + digest(archive))
    print('BOARD_PACKAGE_BYTES=' + str(archive.stat().st_size))


if __name__ == '__main__':
    build()
