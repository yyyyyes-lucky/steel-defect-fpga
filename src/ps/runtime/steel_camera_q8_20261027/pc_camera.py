"""Windows USB camera -> local YOLO -> PYNQ v10. No FPGA work on PC."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import uuid
import cv2
import numpy as np
import requests
from protocol import SCHEMA, encode_request
from camera_runtime import RELEASE

ROOT=Path(__file__).resolve().parent
NAMES=('crack','inclusion','patches','pitted_surface','rolled_in_scale','scratches')


def predictions_from_result(result):
    return [{'class_id':int(cls),'detection_index':i,'confidence':float(conf),'box_xyxy_pixel':[float(x) for x in box]}
            for i,(box,cls,conf) in enumerate(zip(result.boxes.xyxy.cpu().tolist(),result.boxes.cls.cpu().tolist(),result.boxes.conf.cpu().tolist()))]


def request_frame(session,url,gray,predictions):
    request_id=uuid.uuid4().hex
    raw=np.ascontiguousarray(gray,dtype=np.uint8).tobytes()
    payload=encode_request(raw,predictions,request_id)
    begin=time.perf_counter()
    response=session.post(url+'/process',data=payload,headers={'Content-Type':'application/octet-stream'},timeout=(3,120))
    http_ms=(time.perf_counter()-begin)*1000
    response.raise_for_status()
    result=response.json()
    if result.get('camera_release') != RELEASE:
        raise RuntimeError('Wrong camera application release; start the Q8 camera notebook')
    if result['schema']!=SCHEMA or result['hardware']!='v10' or result['request_id']!=request_id or result['image_sha256']!=hashlib.sha256(raw).hexdigest():
        raise RuntimeError('Mismatched board response identity')
    if len(result['records'])!=len(predictions) or any(r['class_id']!=p['class_id'] or r['detection_index']!=p['detection_index'] for r,p in zip(result['records'],predictions)):
        raise RuntimeError('Mismatched board ROI records')
    return result,http_ms


def save_png(path,image):
    ok,data=cv2.imencode('.png',image)
    if not ok:raise RuntimeError('PNG encoding failed')
    path.write_bytes(data.tobytes())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--board',required=True,help='Example: http://192.168.2.99:8766')
    parser.add_argument('--camera',type=int,default=0)
    parser.add_argument('--frames',type=int,default=30)
    parser.add_argument('--conf',type=float,default=.25)
    parser.add_argument('--weights',type=Path,default=ROOT.parent/'runs/steel_yolov8n/weights/best.pt')
    parser.add_argument('--no-window',action='store_true')
    args=parser.parse_args()
    if not 1<=args.frames<=1000 or not 0<args.conf<1 or not args.weights.is_file():
        parser.error('Check frames/conf/existing local weights; no automatic model download')
    # Load model once before capture, preserving the original gray->3-channel contract.
    import torch
    from ultralytics import YOLO
    model=YOLO(str(args.weights))
    if len(model.names)!=6:raise RuntimeError('Expected six-class steel model')
    device=0 if torch.cuda.is_available() else 'cpu'
    out=ROOT/'results'/('camera_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True,exist_ok=False)
    report={'schema':'steel_camera_application/1.0','completed':False,'errors':[],'frames':[],
            'camera_release':RELEASE,
            'camera_host':'Windows','detector_host':'Windows','postprocess_host':'PYNQ-v10',
            'weights_sha256':hashlib.sha256(args.weights.read_bytes()).hexdigest(),
            'device':str(device),'model_names':model.names,'camera_index':args.camera,'board':args.board,
            'timing_scope':'Each frame: read+resize+YOLO+pack+HTTP+render+PNG save. Excludes model load and final JSON write. HTTP contains board work; do not add them.',
            'camera_capture_to_exposure_latency_measured':False,'full184_equivalence_claim':False}
    path=out/'camera_application.json'
    def persist():
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
    capture=None
    try:
        with requests.Session() as session:
            session.trust_env=False
            health=session.get(args.board.rstrip('/')+'/health',timeout=(3,10));health.raise_for_status()
            status=health.json()
            report['board_health']=status
            if status.get('camera_release') != RELEASE:
                raise RuntimeError('Wrong camera application release; start run_camera_q8.ipynb')
            if status.get('service')!=SCHEMA or status.get('status')!='ready':
                raise RuntimeError('Unexpected v10 camera service')
            capture=cv2.VideoCapture(args.camera,cv2.CAP_DSHOW)
            if not capture.isOpened():raise RuntimeError('Cannot open selected Windows USB camera')
            report['capture_backend']=capture.getBackendName()
            for index in range(args.frames):
                start=time.perf_counter();ok,frame=capture.read();read_end=time.perf_counter()
                if not ok or frame is None:raise RuntimeError('Camera frame read failed')
                gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
                gray=cv2.resize(gray,(512,512),interpolation=cv2.INTER_AREA)
                prepared=time.perf_counter()
                detection=model.predict(source=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR),imgsz=512,conf=args.conf,max_det=100,device=device,save=False,verbose=False)[0]
                predictions=predictions_from_result(detection);inferred=time.perf_counter()
                returned,http_ms=request_frame(session,args.board.rstrip('/'),gray,predictions)
                received=time.perf_counter()
                canvas=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
                for prediction,record in zip(predictions,returned['records']):
                    x1,y1,x2,y2=[int(round(x)) for x in prediction['box_xyxy_pixel']]
                    cv2.rectangle(canvas,(x1,y1),(x2,y2),(0,220,100),2)
                    label=NAMES[prediction['class_id']]+' '+record['branch_state']
                    cv2.putText(canvas,label,(x1,max(12,y1-4)),cv2.FONT_HERSHEY_SIMPLEX,.35,(0,220,100),1)
                save_png(out/('frame_%03d.png'%index),gray)
                save_png(out/('result_%03d.png'%index),canvas)
                if not args.no_window:
                    cv2.imshow('Windows YOLO + PYNQ v10',canvas);cv2.waitKey(1)
                ended=time.perf_counter()
                report['frames'].append({'index':index,'original_shape':list(frame.shape),'predictions':predictions,'board_response':returned,
                     'timing_ms':{'capture_read':(read_end-start)*1000,'preprocess':(prepared-read_end)*1000,'yolo':(inferred-prepared)*1000,
                     'pack_http_decode':(received-inferred)*1000,'http_roundtrip':http_ms,'render_save':(ended-received)*1000,'application_frame_wall':(ended-start)*1000}})
                persist();print('CAMERA_FRAME',index+1,'/',args.frames,'ROIS=',len(predictions),flush=True)
        report['completed']=True
    except BaseException as exc:
        report['errors'].append(type(exc).__name__+': '+str(exc))
        raise
    finally:
        if capture is not None:capture.release()
        cv2.destroyAllWindows();persist();print('PC_REPORT_FILE='+str(path),flush=True)
    print('CAMERA_APPLICATION_COMPLETED=True')


if __name__=='__main__':main()
