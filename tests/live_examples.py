"""Explicit live acceptance check; reads an existing agent guid from stdin, never logs it."""
import json
from pathlib import Path
import sys
import time
import httpx

guid=sys.stdin.read().strip()
if not guid:raise SystemExit('No authenticated agent session supplied')
base='http://host.docker.internal:8086'
expected=json.loads(Path('/tests/expected_examples.json').read_text())
selected=int(sys.argv[sys.argv.index('--pair')+1]) if '--pair' in sys.argv else None
starts=[selected] if selected else [3,5,7,187,189]

with httpx.Client(timeout=330,headers={'Authorization':'Bearer '+guid}) as client:
    files=[('files',(p.name,p.read_bytes(),'image/tiff')) for p in sorted(Path('/examples').glob('*.tif')) if not selected or int(p.stem) in (selected,selected+1)]
    response=None
    if '--resume' not in sys.argv:
        try:
            response=client.post(base+'/v1/parse?wait_seconds=120',files=files)
        except httpx.RequestError:
            print('Initial connection interrupted; recovering existing job without resubmission',flush=True)
    if response is None:
        candidates=client.get(base+'/v1/jobs').json()
        payload=next((job for job in candidates if [d['pages'][0] for d in job['documents']]==starts),None)
        if payload is None:raise SystemExit('No existing sample job found; no duplicate submitted')
    else:
        if response.status_code not in (200,202):raise SystemExit('Live request failed: HTTP '+str(response.status_code))
        payload=response.json()
    if 'status' in payload:
        job_id=payload['id'];deadline=time.monotonic()+900
        while payload['status'] in ('running','queued') and time.monotonic()<deadline:
            time.sleep(3)
            try:
                payload=client.get(base+'/v1/jobs/'+job_id,timeout=30).json()
            except httpx.RequestError:
                continue
        if payload['status']!='completed':raise SystemExit('Live job status: '+payload['status'])
    if 'documents' not in payload and selected:
        payload={'documents':[{'pages':[selected,selected+1],'data':payload}]}
    if 'documents' not in payload:raise SystemExit('Expected batch documents')
    failed=0
    for doc in payload['documents']:
        first=str(doc['pages'][0]);data=doc['data']
        mismatches=[field for field,value in expected[first].items() if data.get(field)!=value]
        print('Pair',first,'PASS' if not mismatches else 'MISMATCH: '+','.join(mismatches),flush=True)
        failed+=bool(mismatches)
    raise SystemExit(1 if failed else 0)
