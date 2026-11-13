from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import asyncio
import json
import logging
import os
import secrets
import shutil
import sqlite3
import time
import uuid

import httpx
from fastapi import FastAPI, Request, UploadFile, File, Form, Query, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from agent import call, AgentError, process_pair
from imaging import normalize

AGENT=os.getenv('AGENT_API_URL','http://localhost:8081').rstrip('/')
WORKSPACE=os.getenv('WORKSPACE_API_URL','http://localhost:8090').rstrip('/')
DATA=Path(os.getenv('DATA_DIR','./data'))
DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'ld_parser.sqlite3'
COOKIE='ld_parser_session'
TASKS=set()
USER_LOCKS={}
MAX_FILE=40*1024*1024
LOGGER=logging.getLogger("uvicorn.error")


def db():
    conn=sqlite3.connect(DB,timeout=10)
    conn.row_factory=sqlite3.Row
    return conn


def initialize():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,guid TEXT NOT NULL,user_id TEXT NOT NULL,username TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,guid TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,items TEXT NOT NULL,error TEXT NOT NULL DEFAULT '');
        """)
        conn.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
    DB.chmod(0o600)


def spawn(job_id):
    task=asyncio.create_task(worker(job_id));TASKS.add(task);task.add_done_callback(TASKS.discard)


@asynccontextmanager
async def lifespan(app):
    initialize()
    app.state.http=httpx.AsyncClient(timeout=httpx.Timeout(45,connect=8),follow_redirects=False)
    with db() as conn:
        pending=conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchall()
    for row in pending: spawn(row['id'])
    yield
    for task in list(TASKS): task.cancel()
    await asyncio.gather(*list(TASKS),return_exceptions=True)
    await app.state.http.aclose()


app=FastAPI(title='LD parser',version='1.0.0',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')


@app.middleware('http')
async def security(request,call_next):
    origin=request.headers.get('origin')
    expected=os.getenv('PUBLIC_ORIGIN')
    if request.method in {'POST','DELETE'} and origin:
        if (expected and origin.rstrip('/')!=expected.rstrip('/')) or (not expected and urlsplit(origin).netloc!=request.headers.get('host')):
            return JSONResponse({'detail':'Nedovoljen izvor zahteve.'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path in ('/docs','/redoc'):
        del response.headers['Content-Security-Policy']
    return response


def session(request):
    key=sha256(request.cookies.get(COOKIE,'').encode()).hexdigest()
    with db() as conn:
        row=conn.execute('SELECT * FROM sessions WHERE id=? AND expires>?',(key,time.time())).fetchone()
    if row is None: raise HTTPException(401,'Prijavite se za nadaljevanje.')
    return dict(row)


async def upstream(request,method,base,path,guid=None,**kwargs):
    try:
        # Login needs expires_at; this adapter preserves the existing auth route contract.
        response=await request.app.state.http.request(method,base+path,params={'guid':guid} if guid else {},**kwargs)
    except httpx.RequestError:
        raise HTTPException(503,'Agent trenutno ni dosegljiv.') from None
    if response.status_code>=400:
        raise HTTPException(401 if response.status_code in (401,403) else 502,'Prijava ni veljavna.' if response.status_code in (401,403) else 'Agent zahteve ni mogel obdelati.')
    return response


async def authenticated(request):
    authorization=request.headers.get('authorization','')
    if authorization:
        scheme,_,guid=authorization.partition(' ')
        if scheme.lower()!='bearer' or not guid: raise HTTPException(401,'Uporabite Authorization: Bearer GUID.')
        me=(await upstream(request,'GET',AGENT,'/v1/auth/me',guid)).json()
        return {'user_id':me['user_id'],'username':me['username'],'guid':guid}
    return session(request)


@app.get('/')
async def index():
    return FileResponse(Path(__file__).parent / 'static/index.html')


@app.get('/health')
async def health():
    return {'status': 'ok'}


@app.post('/api/login')
async def login(request: Request):
    body = await request.json()
    guid = body.get('guid', '')
    expires = time.time() + 12 * 3600
    if not guid:
        response = await upstream(request, 'POST', AGENT, '/v1/auth/login', json={
            'username': body.get('username', ''), 'password': body.get('password', '')})
        payload = response.json()
        guid = payload['guid']
        expires = min(expires, datetime.fromisoformat(payload['expires_at'].replace('Z', '+00:00')).timestamp())
    me = (await upstream(request, 'GET', AGENT, '/v1/auth/me', guid)).json()
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',
                     (sha256(token.encode()).hexdigest(), guid, me['user_id'], me['username'], expires))
    response = JSONResponse({'username': me['username']})
    response.set_cookie(COOKIE, token, httponly=True, secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
                        samesite='strict', max_age=max(0, int(expires-time.time())), path='/')
    return response


@app.get('/api/me')
async def me(request: Request):
    user = session(request)
    await upstream(request, 'GET', AGENT, '/v1/auth/me', user['guid'])
    return {'username': user['username']}


@app.post('/api/logout')
async def logout(request: Request):
    user = session(request)
    # Remove only this webservice session; other agent clients can keep working.
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE id=?', (user['id'],))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/')
    return response


def job_row(job_id, owner=None):
    with db() as conn:
        row=conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if row is None or (owner is not None and row['user_id']!=owner):
        raise HTTPException(404,'Opravilo ne obstaja.')
    result=dict(row);result['items']=json.loads(result['items']);return result


def save(job):
    with db() as conn:
        conn.execute('UPDATE jobs SET status=?,items=?,error=?,guid=? WHERE id=?',
                     (job['status'],json.dumps(job['items'],ensure_ascii=False),job['error'],job['guid'],job['id']))


def public(job):
    return {'id':job['id'],'status':job['status'],'created':job['created'],'error':job['error'],
            'documents':[{'pages':[item['first_page'],item['first_page']+1],
                          'source_files':item['source_names'],'status':item.get('stage','queued'),
                          'phase':item.get('phase','V čakalni vrsti'),'data':item.get('result')}
                         for item in job['items']]}


async def worker(job_id):
    job=job_row(job_id)
    async with USER_LOCKS.setdefault(job['user_id'],asyncio.Lock()):
        job['status']='running';save(job)
        try:
            for item in job['items']:
                if item.get('result') is not None: continue
                result=await process_pair(app.state.http,AGENT,WORKSPACE,job['guid'],item,lambda:save(job))
                item.update(result=result,stage='completed',phase='Podatki so izluščeni');save(job)
            job.update(status='completed',guid='');save(job)
            LOGGER.info('LD job completed: job=%s documents=%s',job_id,len(job['items']))
        except asyncio.CancelledError:
            # Persisted thread/run lets a new service process resume monitoring without resubmission.
            save(job);raise
        except AgentError as exc:
            LOGGER.warning('LD job failed: job=%s upstream_status=%s',job_id,exc.status)
            job.update(status='failed',error=exc.message,guid='');save(job)
        except Exception as exc:
            LOGGER.error('LD job failed: job=%s error_type=%s',job_id,type(exc).__name__)
            job.update(status='failed',error='Obdelave ni bilo mogoče dokončati; preverite vhodne dokumente.',guid='');save(job)
        finally:
            if job['status'] in ('completed','failed'):
                shutil.rmtree(DATA/'inputs'/job['id'],ignore_errors=True)


async def submit(request,files,first_page):
    user=await authenticated(request)
    if len(files)>40: raise HTTPException(413,'Največ 40 vhodnih datotek.')
    uploaded=[];total=0
    for file in files:
        content=await file.read(MAX_FILE+1);await file.close()
        total+=len(content)
        if len(content)>MAX_FILE or total>100*1024*1024:
            raise HTTPException(413,'Največ 40 MB na datoteko in 100 MB na klic.')
        uploaded.append((Path((file.filename or '').replace('\\','/')).name,content))
    try:
        pairs=await asyncio.to_thread(normalize,uploaded,first_page)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    job_id=str(uuid.uuid4());folder=DATA/'inputs'/job_id
    folder.mkdir(parents=True,mode=0o700)
    items=[]
    try:
        for pair in pairs:
            path=folder/(str(pair.first_page)+'.pdf');path.write_bytes(pair.pdf);path.chmod(0o600)
            signature_path=folder/(str(pair.first_page)+'_signature.png')
            signature_path.write_bytes(pair.signature_png);signature_path.chmod(0o600)
            items.append({'id':str(uuid.uuid4()),'first_page':pair.first_page,'source_names':pair.source_names,
                          'local_path':str(path),'signature_local_path':str(signature_path),'sizes':pair.sizes,'stage':'queued'})
        with db() as conn:
            if conn.execute("SELECT id FROM jobs WHERE user_id=? AND status IN ('queued','running')",(user['user_id'],)).fetchone():
                raise HTTPException(409,'Za ta račun že poteka obdelava LD dokumentov.')
            conn.execute('INSERT INTO jobs(id,user_id,guid,status,created,items) VALUES(?,?,?,?,?,?)',
                         (job_id,user['user_id'],user['guid'],'queued',time.time(),json.dumps(items)))
    except Exception:
        shutil.rmtree(folder,ignore_errors=True);raise
    spawn(job_id)
    return job_id


@app.post('/v1/jobs',status_code=202)
@app.post('/api/jobs',status_code=202,include_in_schema=False)
async def create_job(request:Request,files:list[UploadFile]=File(...),first_page:int|None=Form(None)):
    job_id=await submit(request,files,first_page)
    return JSONResponse(public(job_row(job_id)),status_code=202,headers={'Location':'/v1/jobs/'+job_id})


@app.get('/v1/jobs')
@app.get('/api/jobs',include_in_schema=False)
async def list_jobs(request:Request):
    user=await authenticated(request)
    with db() as conn:
        rows=conn.execute('SELECT id FROM jobs WHERE user_id=? ORDER BY created DESC LIMIT 30',(user['user_id'],)).fetchall()
    return [public(job_row(row['id'])) for row in rows]


@app.get('/v1/jobs/{job_id}')
@app.get('/api/jobs/{job_id}',include_in_schema=False)
async def get_job(job_id:str,request:Request):
    user=await authenticated(request)
    return public(job_row(job_id,user['user_id']))


@app.post('/v1/parse')
async def parse(request:Request,files:list[UploadFile]=File(...),first_page:int|None=Form(None),
                wait_seconds:int=Query(120,ge=0,le=300)):
    """Return flat extraction JSON for one pair; a documents list for a batch. 202 means keep polling Location."""
    deadline=time.monotonic()+wait_seconds
    job_id=await submit(request,files,first_page)
    while True:
        job=job_row(job_id)
        headers={'X-Job-ID':job_id,'Location':'/v1/jobs/'+job_id}
        if job['status']=='completed':
            documents=public(job)['documents']
            body=documents[0]['data'] if len(documents)==1 else {'documents':documents}
            return JSONResponse(body,headers=headers)
        if job['status']=='failed':
            return JSONResponse(public(job),status_code=502,headers=headers)
        if time.monotonic()>=deadline:
            return JSONResponse(public(job),status_code=202,headers=headers)
        await asyncio.sleep(.5)


@app.get('/v1/schema')
async def extraction_schema():
    from models import strict_schema
    return strict_schema()


def documented_openapi():
    from fastapi.openapi.utils import get_openapi
    from models import strict_schema
    if app.openapi_schema:
        return app.openapi_schema
    schema=get_openapi(title=app.title,version=app.version,routes=app.routes)
    schema.setdefault('components',{}).setdefault('securitySchemes',{})['AgentGuid']={
        'type':'http','scheme':'bearer','description':'Veljaven guid iz agentove prijave; ni modelni API ključ.'}
    schema['components'].setdefault('schemas',{})['LDResult']=strict_schema()
    for path,operations in schema['paths'].items():
        if path.startswith('/v1/') and path!='/v1/schema':
            for operation in operations.values():
                if isinstance(operation,dict):operation['security']=[{'AgentGuid':[]}]
    schema['paths']['/v1/parse']['post']['responses']['200']={
        'description':'En par: ploščati LDResult. Paket: documents z pages, source_files, status in data.',
        'content':{'application/json':{'schema':{'anyOf':[
            {'$ref':'#/components/schemas/LDResult'},
            {'type':'object','properties':{'documents':{'type':'array','items':{
                'type':'object','properties':{'pages':{'type':'array','items':{'type':'integer'}},
                'data':{'$ref':'#/components/schemas/LDResult'}}}}}}]}}}}
    schema['paths']['/v1/parse']['post']['responses']['202']={
        'description':'Agent še dela; uporabite Location ali X-Job-ID za spremljanje istega opravila.'}
    app.openapi_schema=schema
    return schema


app.openapi=documented_openapi
