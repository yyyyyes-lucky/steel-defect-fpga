"""Portable evidence exports; every path inside the exported page is relative."""
from datetime import datetime
import html
import json
from pathlib import Path
import uuid
import zipfile

from wire import digest, json_bytes


def validate_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('请选择完整的绝对保存路径')
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    probe = path/('.steel_write_check_'+uuid.uuid4().hex)
    with probe.open('xb') as stream:
        stream.write(b'ok')
    probe.unlink()
    return path


def chronological_name(metadata):
    stamp = datetime.fromisoformat(metadata['captured_at'].replace('Z', '+00:00')).astimezone()
    return 'frame_'+stamp.strftime('%Y-%m-%d_%H-%M-%S-')+('%03d' % (stamp.microsecond//1000))+'_%06d_' % metadata.get('sequence', 0)+metadata['frame_id'][:10]


def export_frame(root, metadata, images):
    root = validate_directory(root)
    folder = root/chronological_name(metadata)
    archive = folder.with_suffix('.zip')
    # Retry export/index failures without duplicating or overwriting complete evidence.
    if folder.exists():
        saved = json.loads((folder/'frame.json').read_bytes())
        if saved['frame_id'] != metadata['frame_id']:
            raise ValueError('保存目录与当前帧身份冲突')
        manifest = json.loads((folder/'manifest.json').read_bytes())
        if any(not (folder/n).is_file() for n in manifest):
            raise ValueError('已有保存材料缺少文件，请选择新目录后重试')
        if not archive.exists():
            pack(folder, archive)
        return {'folder': str(folder), 'zip': str(archive), 'frame_id': metadata['frame_id']}
    images = dict(images)
    if 'yolo.png' not in images or 'fpga.png' not in images:
        import cv2
        import numpy as np
        from live import render
        from wire import png
        original = cv2.imdecode(np.frombuffer(images['original.png'], np.uint8), cv2.IMREAD_COLOR)
        overlay = cv2.imdecode(np.frombuffer(images['overlay.png'], np.uint8), cv2.IMREAD_UNCHANGED)
        if original is None or overlay is None:
            raise ValueError('保存帧图像解码失败')
        region = metadata['processing_region_xyxy']
        images['yolo.png'] = png(render(original, metadata['predictions'], region=region))
        images['fpga.png'] = png(render(original, metadata['predictions'], overlay, region=region))
    frame_id = metadata['frame_id']
    temp = root/('.export_'+uuid.uuid4().hex)
    temp.mkdir()
    try:
        for name, value in images.items():
            dest = (temp/name).resolve()
            if temp.resolve() not in dest.parents:
                raise ValueError('Invalid evidence filename')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(value)
        (temp/'frame.json').write_bytes(json_bytes(metadata))
        rows = ''.join('<tr><td>%s</td><td>%.1f%%</td><td>%s</td></tr>' %
            (html.escape(str(r['class_id'])), r['confidence']*100, html.escape(str(r.get('branch_state', ''))))
            for r in metadata['board_response']['records'])
        page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>钢板缺陷 · 保存帧</title><style>body{font:15px "Microsoft YaHei",sans-serif;background:#f5f6f6;color:#20332b;margin:24px}main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}img{width:100%%}figure{margin:0}figcaption{padding:12px 0}td,th{padding:10px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:700px){main{grid-template-columns:1fr}}</style>
<h1>钢板缺陷 · 动态检测保存帧</h1><p>帧号：%s · 采集时间：%s</p><main><figure><figcaption>原始画面</figcaption><img src="original.png"></figure><figure><figcaption>Windows YOLO</figcaption><img src="yolo.png"></figure><figure><figcaption>FPGA 路径后处理</figcaption><img src="fpga.png"></figure></main>
<p>YOLO：%.2f ms · FPGA 路径后处理：%.2f ms · PC处理流程：%.2f ms</p><p>后处理包含 DMA 和 ARM 工作。PC处理流程不含浏览器显示和保存；上述耗时有包含关系，不相加。</p>
<table><tr><th>类别 ID</th><th>置信度</th><th>后处理状态</th></tr>%s</table><p>类别 ID：0 裂纹，1 夹杂，2 斑块，3 麻面，4 氧化皮压入，5 划痕。候选区域不代表精确分割真值。</p>
<p><a href="frame.json">完整记录与模型哈希</a> · <a href="input.png">实际512×512灰度输入</a> · <a href="manifest.json">文件哈希清单</a></p></html>''' % (
            html.escape(frame_id), html.escape(metadata['captured_at']), metadata['timing_ms']['yolo'],
            metadata['timing_ms']['fpga_pipeline'], metadata['timing_ms']['processing_wall'], rows)
        if metadata.get('test_fixture'):
            page = page.replace('<h1>', '<p style="background:#fff0bb;padding:16px">仅供界面测试：非真实相机 / FPGA 结果，耗时为模拟值。</p><h1>', 1)
        region = metadata.get('processing_region_xyxy')
        if region:
            page = page.replace('<table>', '<p>仅检测原图中心方形区域 %s（像素坐标）；区域外未参与检测。</p><table>' % html.escape(str(region)), 1)
        (temp/'index.html').write_text(page, encoding='utf-8')
        manifest = {str(p.relative_to(temp)).replace('\\', '/'): digest(p.read_bytes()) for p in temp.rglob('*') if p.is_file()}
        (temp/'manifest.json').write_bytes(json_bytes(manifest))
        temp.rename(folder)
    except Exception:
        # Preserve incomplete evidence for diagnosis, never present it as complete.
        raise
    pack(folder, archive)
    return {'folder': str(folder), 'zip': str(archive), 'frame_id': frame_id}


def pack(folder, archive):
    staging = folder.with_suffix('.zip.partial')
    with zipfile.ZipFile(staging, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for p in folder.rglob('*'):
            if p.is_file():
                zipped.write(p, p.relative_to(folder))
    staging.rename(archive)


def save_session_index(directory, entries):
    rows = []
    for entry in sorted(entries, key=lambda e: e['sequence']):
        name = Path(entry['folder']).name
        rows.append('<tr><td>%06d</td><td>%s</td><td>%d</td><td><a href="%s/index.html">查看全套结果</a></td><td><a href="%s.zip">ZIP</a></td></tr>' %
            (entry['sequence'], html.escape(entry['captured_at_local']), entry['detections'], html.escape(name), html.escape(name)))
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>动态检测 · 时间顺序记录</title><style>body{font:15px "Microsoft YaHei",sans-serif;margin:28px;background:#f5f6f6}td,th{padding:12px;border-bottom:1px solid #ddd}a{color:#176947}</style><h1>动态检测 · 自动保存记录</h1><p>按采集序号排列；时间来自采集电脑本地时钟。每一行包含完整图片、掩膜、数据和离线页面。</p><table><tr><th>序号</th><th>本地采集时间</th><th>检测框</th><th>结果</th><th>打包下载</th></tr>'+''.join(rows)+'</table></html>'
    temp = directory/'index.html.tmp'
    temp.write_text(page, encoding='utf-8')
    temp.replace(directory/'index.html')
