"""Extract a board ZIP into a fresh location without deleting any earlier runs."""
import argparse
from datetime import datetime
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZipFile


def extract(archive, directory=None):
    archive = Path(archive).resolve()
    parent = Path(directory or archive.parent).resolve()
    if not archive.is_file():
        raise FileNotFoundError('Upload steel_board_runtime.zip first: ' + str(archive))
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='steel_repro_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_', dir=parent))
    with ZipFile(archive) as zipped:
        for item in zipped.infolist():
            relative = PurePosixPath(item.filename)
            target = (destination / item.filename).resolve()
            if relative.is_absolute() or '..' in relative.parts or '\\' in item.filename or destination not in target.parents:
                raise ValueError('Unsafe ZIP entry: ' + item.filename)
            if not relative.parts or relative.parts[0] != 'steel_board_runtime':
                raise ValueError('Unexpected ZIP root: ' + item.filename)
        bad = zipped.testzip()
        if bad is not None:
            raise ValueError('ZIP CRC failed: ' + bad)
        zipped.extractall(destination)
    root = destination / 'steel_board_runtime'
    if not all((root / name).is_file() for name in ('run_static.ipynb', 'run_dynamic.ipynb')):
        raise ValueError('Required board files missing in ' + str(root))
    print('BOARD_DIRECTORY=' + str(root))
    print('Open run_static.ipynb or run_dynamic.ipynb inside this NEW directory; previous runs were preserved.')
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?', default='steel_board_runtime.zip')
    parser.add_argument('--directory')
    args = parser.parse_args()
    extract(args.archive, args.directory)
