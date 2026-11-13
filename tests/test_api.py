import io
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from fastapi.testclient import TestClient
from PIL import Image
import app
from models import LDResult


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();app.DATA=Path(self.tmp.name);app.DB=app.DATA/'test.sqlite3'
        self.calls=[];self.lost=False
        self.client=TestClient(app.app);self.client.__enter__()
        self.client.portal.call(app.app.state.http.aclose)
        app.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(self.remote))
    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()
    def remote(self,request):
        self.calls.append(request)
        path=request.url.path
        if path=='/v1/auth/me':return httpx.Response(200,json={'user_id':request.url.params['guid'],'username':'test'})
        if path=='/v1/files':return httpx.Response(200,json={'path':'/uploads/signature.png' if b'filename=\"signature.png\"' in request.content else '/uploads/ld.pdf'})
        if path=='/v1/threads':return httpx.Response(200,json={'thread_id':'thread-one'})
        if path.endswith('/runs'):
            if self.lost:raise httpx.ReadTimeout('accepted but response lost',request=request)
            return httpx.Response(202,json={'run_id':'run-one'})
        if path=='/v1/runs/active':return httpx.Response(200,json={'thread_id':'thread-one','run_id':'run-one','status':'running'})
        if path=='/v1/runs/run-one':return httpx.Response(200,json={'status':'completed'})
        if path.endswith('/messages'):
            data=LDResult(other_country=True,amount1=2000,currency1='EUR',country1='DE',signature_present=False,signature_evidence='blank').model_dump()
            return httpx.Response(200,json=[{'role':'assistant','content':json.dumps(data)}])
        return httpx.Response(404)
    def files(self):
        out=io.BytesIO();Image.new('RGB',(120,180),'white').save(out,format='PNG');data=out.getvalue()
        return [('files',('00000002.png',data,'image/png')),('files',('00000001.png',data,'image/png'))]
    def test_direct_flat_json_and_authorization(self):
        result=self.client.post('/v1/parse?wait_seconds=10',headers={'Authorization':'Bearer test-guid'},files=self.files())
        self.assertEqual(result.status_code,200,result.text)
        data=result.json();self.assertEqual(data['amount1'],2000);self.assertFalse(data['signature_present']);self.assertIsNone(data['amount'])
        self.assertNotIn('guid',data)
        job_id=result.headers['X-Job-ID']
        self.assertEqual(self.client.get('/v1/jobs/'+job_id).status_code,401)
        self.assertEqual(self.client.get('/v1/jobs/'+job_id,headers={'Authorization':'Bearer another-user'}).status_code,404)
    def test_lost_response_does_not_resubmit(self):
        self.lost=True
        result=self.client.post('/v1/parse?wait_seconds=10',headers={'Authorization':'Bearer test-guid'},files=self.files())
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(sum(r.method=='POST' and r.url.path.endswith('/runs') for r in self.calls),1)
    def test_openapi_and_schema_publish_auth_and_fields(self):
        response=self.client.get('/openapi.json')
        self.assertEqual(response.status_code,200)
        spec=response.json()
        self.assertEqual(spec['paths']['/v1/parse']['post']['security'],[{'AgentGuid':[]}])
        fields=self.client.get('/v1/schema').json()['properties']
        self.assertIn('signature_present',fields)
        self.assertIn('document_number',fields)

    def test_invalid_pair_never_calls_model(self):
        result=self.client.post('/v1/parse',headers={'Authorization':'Bearer test-guid'},files=self.files()[:1])
        self.assertEqual(result.status_code,422)
        self.assertFalse(any(r.url.path.endswith('/runs') for r in self.calls))

if __name__=='__main__':unittest.main()
