"""Sequential LAN bridge to the already-accepted v10 entry; manual start only."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import json
from pathlib import Path
import sys
import time
import numpy as np
from protocol import SCHEMA, MAX_BODY, decode_request

ROOT = Path(__file__).resolve().parent
ENTRY_NAME = 'steel_runtime_v10_candidate_20261025'


def load_entry():
    entry = ROOT.parent/ENTRY_NAME
    old = sys.modules.get('runtime_v10')
    if old is not None and Path(old.__file__).resolve().parent != entry.resolve():
        raise RuntimeError('Conflicting runtime_v10 module; restart kernel')
    sys.path.insert(0,str(entry))
    return importlib.import_module('runtime_v10')


class BridgeServer(HTTPServer):
    def __init__(self, address, runtime):
        super().__init__(address, Handler)
        self.runtime = runtime
        self.processed = 0
        self.failure = None
        self.last = None
        self.timeout = 1


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass

    def respond(self, code, value):
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != '/health':
            self.respond(404,{'error':'Unknown route'});return
        self.respond(200,{'service':SCHEMA,'status':'ready' if self.server.failure is None else 'failed',
                          'hardware':'v10','width':512,'height':512,
                          'bit_sha256':self.server.runtime.report['integrity']['new_bit_sha256']})

    def do_POST(self):
        if self.path != '/process':
            self.respond(404,{'error':'Unknown route'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Transfer-Encoding') or not 0<size<=MAX_BODY:
                raise ValueError('Invalid request length')
            payload=self.rfile.read(size)
            if len(payload)!=size:
                raise ValueError('Truncated request')
            header,raw=decode_request(payload)
        except (ValueError,UnicodeError,TimeoutError) as exc:
            self.respond(400,{'error':str(exc)});return
        digest=hashlib.sha256(payload).hexdigest()
        previous=self.server.last
        if previous and header['request_id']==previous[0]:
            if digest!=previous[1]:
                self.respond(409,{'error':'Request ID reused for different data'});return
            self.respond(200,previous[2]);return
        if self.server.failure is not None:
            self.respond(503,{'error':'Hardware session failed; preserve report and recover'});return
        try:
            gray=np.frombuffer(raw,np.uint8).reshape(512,512).copy()
            begin=time.perf_counter()
            result=self.server.runtime.process_frame(gray,header['predictions'],case='camera_'+header['request_id'])
            wall_ms=(time.perf_counter()-begin)*1000
            response={'schema':SCHEMA,'request_id':header['request_id'],'image_sha256':header['image_sha256'],
                      'records':result['records'],'pipeline_ms':result['timing']['end_to_end_static_pipeline_ms'],
                      'board_runtime_wall_ms':wall_ms,'hardware':'v10',
                      'note':'runtime wall includes qualified checks/report writes; pipeline excludes oracle. No live-image golden reference.'}
            # Ensure serialization succeeds before marking this request complete.
            json.dumps(response,ensure_ascii=False,allow_nan=False)
            self.server.processed+=1
            self.server.last=(header['request_id'],digest,response)
        except Exception as exc:
            self.server.failure=exc
            self.respond(500,{'error':repr(exc),'session_report':str(self.server.runtime.path)})
            return
        self.respond(200,response)


def serve(host='0.0.0.0',port=8766,max_frames=5,idle_seconds=300):
    if type(max_frames) is not int or not 1<=max_frames<=1000 or idle_seconds<=0:
        raise ValueError('Invalid service limits')
    entry=load_entry()
    with entry.Runtime(purpose='camera_application') as runtime:
        with BridgeServer((host,port),runtime) as server:
            print('CAMERA_SERVICE_READY=True PORT='+str(server.server_port),flush=True)
            deadline=time.monotonic()+idle_seconds
            previous=0
            while server.processed<max_frames and time.monotonic()<deadline:
                server.handle_request()
                if server.failure is not None:
                    raise RuntimeError('Camera bridge hardware processing failed') from server.failure
                if server.processed!=previous:
                    previous=server.processed;deadline=time.monotonic()+idle_seconds
            count=server.processed
    print('PROCESSED_FRAMES='+str(count),flush=True)
    print('HARDWARE_SESSION_SAFE='+str(runtime.report['hardware_session_safe']),flush=True)
    print('BOARD_REPORT_FILE='+str(runtime.path),flush=True)
    return runtime.report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--max-frames',type=int,default=5)
    args=parser.parse_args()
    serve(args.host,args.port,args.max_frames)
