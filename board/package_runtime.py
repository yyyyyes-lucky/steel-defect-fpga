"""Assemble the current board tree from the repository's canonical files."""
import argparse
import json
from pathlib import Path
import shutil
import zipfile




def assemble(output, repo=None):
    repo = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    output = Path(output).resolve()
    target = output / 'steel_board_runtime'
    archive = output / 'steel_board_runtime.zip'
    if target.exists() or archive.exists():
        raise FileExistsError('Choose a fresh output directory: ' + str(output))
    mapping = json.loads((repo / 'board/runtime_map.json').read_bytes())
    checked = []
    for name, row in mapping['files'].items():
        source = (repo / row['source']).resolve()
        destination = (target / name).resolve()
        if repo not in source.parents or target not in destination.parents:
            raise ValueError('Path outside package: ' + name)
        if not source.is_file():
            raise FileNotFoundError('Required source missing: ' + str(source))
        checked.append((source, destination))
    for source, destination in checked:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                stream.write(path, 'steel_board_runtime/' + path.relative_to(target).as_posix())
    with zipfile.ZipFile(archive) as stream:
        if stream.testzip() is not None:
            raise RuntimeError('ZIP CRC failed')
    extractor = output / 'extract_runtime.py'
    shutil.copy2(repo / 'board/extract_runtime.py', extractor)
    print('BOARD_DIRECTORY=' + str(target))
    print('BOARD_ZIP=' + str(archive))
    print('UPLOAD_WITH_ZIP=' + str(extractor))
    return target, archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    assemble(args.output or repo_root / 'build/output/board_package')
