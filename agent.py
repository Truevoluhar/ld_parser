import asyncio
import json
import httpx
from models import PROMPT, strict_schema, validated_result


class AgentError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


async def call(client, method, base, path, guid=None, **kwargs):
    try:
        response=await client.request(method,base+path,params={'guid':guid} if guid else {},**kwargs)
    except httpx.RequestError:
        raise AgentError(503,'Povezava z agentom je prekinjena; stanje še preverjam.') from None
    if response.status_code>=400:
        status=response.status_code
        raise AgentError(status,'Prijava pri agentu je potekla.' if status in (401,403) else 'Agent zahteve trenutno ne more obdelati.')
    return response.json()


async def transcript(client, base, guid, item):
    messages=await call(client,'GET',base,'/v1/threads/'+item['thread_id']+'/messages',guid)
    answer=next((m['content'] for m in reversed(messages) if m['role']=='assistant'),None)
    if answer is None: return None
    try:
        return validated_result(json.loads(answer))
    except Exception:
        raise AgentError(502,'Agentov odgovor ne ustreza zahtevani shemi; rezultat ni potrjen.') from None


async def process_pair(client, base, workspace, guid, item, save):
    # Every stage is persisted before a possibly ambiguous side effect.
    if not item.get('upload_path'):
        with open(item['local_path'],'rb') as handle:
            result=await call(client,'POST',workspace,'/v1/files',guid,
                files={'file':('ld.pdf',handle,'application/pdf')},
                data={'destination':'uploads/ld_parser/'+item['id']})
        item['upload_path']=result['path'];save()
    if not item.get('signature_upload_path'):
        with open(item['signature_local_path'],'rb') as handle:
            result=await call(client,'POST',workspace,'/v1/files',guid,
                files={'file':('signature.png',handle,'image/png')},
                data={'destination':'uploads/ld_parser/'+item['id']})
        item['signature_upload_path']=result['path'];save()
    if not item.get('thread_id'):
        result=await call(client,'POST',base,'/v1/threads',guid)
        item['thread_id']=result['thread_id'];save()
    if item.get('stage') not in ('submitting','recovering','running'):
        while True:
            item.update(stage='submitting',phase='Pošiljam dokument agentu');save()
            try:
                result=await call(client,'POST',base,'/v1/threads/'+item['thread_id']+'/runs',guid,
                    json={'message':PROMPT+'\nGlavni dvostranski PDF: '+item['upload_path']+'\nPNG izrez podpisa druge strani: '+item['signature_upload_path'],
                          'attachments':[item['upload_path'],item['signature_upload_path']],'response_schema':strict_schema()})
                item.update(run_id=result['run_id'],stage='running');save();break
            except AgentError as exc:
                if exc.status==409:
                    item.update(stage='waiting',phase='Čakam, da agent zaključi prejšnje opravilo');save()
                    await asyncio.sleep(3);continue
                if exc.status>=500:
                    item.update(stage='recovering',phase='Preverjam sprejem že poslanega opravila');save();break
                raise
    while True:
        try:
            if item.get('run_id'):
                try:
                    state=await call(client,'GET',base,'/v1/runs/'+item['run_id'],guid)
                except AgentError as exc:
                    if exc.status!=404: raise
                    result=await transcript(client,base,guid,item)
                    if result is not None: return result
                    raise AgentError(410,'Agent se je ponovno zagnal in opravilo ni več na voljo.') from None
                item.update(phase=state.get('phase','Agent bere obe strani'),stage='running');save()
                if state['status']=='completed':
                    result=await transcript(client,base,guid,item)
                    if result is not None: return result
                if state['status'] in ('failed','cancelled'):
                    raise AgentError(422,'Agent dokumenta ni mogel obdelati.')
            else:
                active=await call(client,'GET',base,'/v1/runs/active',guid)
                if active.get('thread_id')==item['thread_id'] and active.get('run_id'):
                    item.update(run_id=active['run_id'],stage='running');save()
                else:
                    result=await transcript(client,base,guid,item)
                    if result is not None: return result
                    item.update(stage='recovering',phase='Sprejem ni potrjen; preverjam thread brez podvojenega zagona');save()
        except AgentError as exc:
            if exc.status in (401,403,410,422): raise
            if exc.status==502 and exc.message.startswith('Agentov odgovor'): raise
            item.update(phase='Povezava je počasna; ponovno preverjam dejansko stanje');save()
        await asyncio.sleep(2)
