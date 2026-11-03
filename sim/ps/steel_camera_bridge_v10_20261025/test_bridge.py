"""Protocol and real localhost HTTP checks with simulated hardware only."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
import requests
import numpy as np
from protocol import SCHEMA, encode_request, decode_request
from board_service import BridgeServer
from pc_camera import request_frame, predictions_from_result


class FakeRuntime:
    def __init__(self):
        self.calls=[]
        self.path=Path('mock_session.json')
        self.report={'integrity':{'new_bit_sha256':'mock'}}
    def process_frame(self,gray,predictions,case):
        self.calls.append((gray.copy(),predictions,case))
        return {'records':[dict(p,branch_state='statistics_only') for p in predictions],
                'timing':{'end_to_end_static_pipeline_ms':1.25}}


@contextmanager
def running_server():
    runtime=FakeRuntime()
    with BridgeServer(('127.0.0.1',0),runtime) as server:
        worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        worker.start()
        with requests.Session() as client:
            client.trust_env=False
            try:yield server,runtime,client,'http://127.0.0.1:'+str(server.server_port)
            finally:server.shutdown();worker.join(timeout=2)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.gray=np.arange(512*512,dtype=np.uint32).astype(np.uint8).reshape(512,512)
        self.pred=[{'class_id':2,'detection_index':0,'confidence':.8,'box_xyxy_pixel':[0.,1.,512.,511.]}]

    def test_wire_exact_image_and_predictions(self):
        body=encode_request(self.gray.tobytes(),self.pred,'a'*32)
        header,raw=decode_request(body)
        self.assertEqual(raw,self.gray.tobytes());self.assertEqual(header['predictions'],self.pred)
        self.assertEqual(header['image_sha256'],hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError,'hash'):
            decode_request(body[:-1]+bytes([body[-1]^1]))
        with self.assertRaises(ValueError):decode_request(body[:-1])

    def test_invalid_boxes_classes_nan_and_duplicate_ids(self):
        for change in ({'box_xyxy_pixel':[0,0,513,512]},{'box_xyxy_pixel':[1,1,1,2]},
                       {'class_id':True},{'class_id':6},{'confidence':float('nan')}):
            with self.assertRaises(ValueError):encode_request(self.gray.tobytes(),[dict(self.pred[0],**change)],'a'*32)
        with self.assertRaises(ValueError):encode_request(self.gray.tobytes(),self.pred*2,'a'*32)

    def test_real_http_image_roi_and_empty(self):
        with running_server() as (server,runtime,client,url):
            self.assertEqual(client.get(url+'/health').json()['service'],SCHEMA)
            for pred in (self.pred,[]):
                result,http_ms=request_frame(client,url,self.gray,pred)
                self.assertEqual(len(result['records']),len(pred));self.assertGreater(http_ms,0)
            self.assertEqual(server.processed,2)
            np.testing.assert_array_equal(runtime.calls[0][0],self.gray)

    def test_repeat_id_is_not_second_hardware_call(self):
        with running_server() as (server,runtime,client,url):
            body=encode_request(self.gray.tobytes(),self.pred,'a'*32)
            a=client.post(url+'/process',data=body);b=client.post(url+'/process',data=body)
            self.assertEqual(a.json(),b.json());self.assertEqual(len(runtime.calls),1)
            changed=encode_request(bytes(512*512),self.pred,'a'*32)
            self.assertEqual(client.post(url+'/process',data=changed).status_code,409)
            self.assertEqual(len(runtime.calls),1)

    def test_invalid_input_does_not_touch_hardware_and_failure_stops_use(self):
        with running_server() as (server,runtime,client,url):
            self.assertEqual(client.post(url+'/process',data=b'bad').status_code,400)
            self.assertEqual(len(runtime.calls),0)
            def fail(*a,**kw):raise RuntimeError('DMA failure')
            runtime.process_frame=fail
            body=encode_request(self.gray.tobytes(),self.pred,'a'*32)
            self.assertEqual(client.post(url+'/process',data=body).status_code,500)
            self.assertIsNotNone(server.failure)
            self.assertEqual(client.post(url+'/process',data=body).status_code,503)

    def test_yolo_adapter_keeps_coordinates(self):
        class Tensor:
            def __init__(self,value):self.value=value
            def cpu(self):return self
            def tolist(self):return self.value
        result=SimpleNamespace(boxes=SimpleNamespace(xyxy=Tensor([[0.,1.,512.,511.]]),cls=Tensor([2.]),conf=Tensor([.8])))
        self.assertEqual(predictions_from_result(result),self.pred)


if __name__=='__main__':unittest.main(verbosity=2)
