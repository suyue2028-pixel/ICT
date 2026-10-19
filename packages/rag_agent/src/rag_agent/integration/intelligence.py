"""Import external answers through backend revision checks before retrieval.

The Agent cannot cite a remote blob that bypasses the backend's source registry.
Registered tools only, query entity validation, real/demo isolation, exact hashes.
"""
import asyncio
from .backend import BackendClient, BackendSettings, BackendError
from ..retrieval.exact import matches


class IntelligenceExecutor:
    def __init__(self, service, index, settings=None):
        self.service,self.index=service,index
        self.client=BackendClient(settings or BackendSettings.from_env())

    async def __call__(self,name,args,state):
        if name not in {'nvd_lookup','kev_lookup','osv_lookup','knowledge_lookup','web_search'}:
            raise BackendError('intelligence_tool_unavailable')
        if name in {'nvd_lookup','kev_lookup'} and args.get('identifier') not in state['identifiers']:
            raise BackendError('intelligence_entity_mismatch')
        fields={'nvd_lookup':{'identifier'},'kev_lookup':{'identifier'},
            'osv_lookup':{'ecosystem','package','version'},'knowledge_lookup':{'topic'},'web_search':{'query'}}
        if set(args)!=fields[name]:raise BackendError('intelligence_invalid_arguments')
        # Body has only typed registered arguments; no URL, code or credential.
        async with asyncio.timeout(min(12,state['budget'].remaining())):
            result=await self.client._request('POST','/api/v1/internal/intelligence/lookup',
                write=True,body={**args,'tool':name})
        if result.get('schema_version')!='1' or result.get('tool')!=name or result.get('status') not in {'completed','empty'}:
            raise BackendError('intelligence_invalid_response')
        docs=result.get('documents',[])
        if not isinstance(docs,list) or len(docs)>8:
            raise BackendError('intelligence_invalid_response')
        imported=[]
        for data in docs:
            if not isinstance(data,dict):raise BackendError('intelligence_invalid_response')
            if data.get('is_demo') is not False:
                raise BackendError('intelligence_scope_mismatch')
            if name in {'nvd_lookup','kev_lookup'}:
                revision=data.get('revision',{})
                if (not isinstance(revision,dict) or args['identifier'] not in
                        [revision.get('vulnerability_id'),revision.get('external_id'),*revision.get('aliases',[])]):
                    raise BackendError('intelligence_entity_mismatch')
            await self.index.upsert(data)
            imported.append(data['document_id'])
        state['external_checked'][name]={'checked_at':result.get('checked_at'),'cache_hit':bool(result.get('cache_hit'))}
        state.setdefault('external_checks',{})[(name,args.get('identifier'))]=state['external_checked'][name]
        if result.get('limitation'):state['limitations'].append(result['limitation'])
        mappings=await asyncio.to_thread(self.index.mappings,False)
        snapshot=await asyncio.to_thread(self.service.store.snapshot,
            {i for i,_ in mappings},{i:v for i,v in mappings})
        state['snapshot']=snapshot
        wanted={rag_id for (rag_id,_),data in mappings.items() if data['document_id'] in imported}
        evidence=[]
        if wanted and name in {'knowledge_lookup','osv_lookup','web_search'}:
            # After ingestion, retrieve the relevant passages from the imported
            # records; the first two paragraphs need not answer the question.
            scoped=await asyncio.to_thread(self.service.store.snapshot,wanted,
                {i:v for i,v in mappings if i in wanted})
            missing=[r.label for r in state.get('requirement_coverage',[]) if r.status!='supported']
            query=state['resolved_question']+' '+' '.join(missing)
            if 'audit_logging' in {r.requirement_id for r in state.get('requirement_coverage',[]) if r.status!='supported'}:
                query+=' audit logs monitoring logging'
            diagnostics={}
            evidence=await self.service.retriever.search(scoped,query[:4000],state['explicit_filters'],4,diagnostics=diagnostics)
        else:
            for row in snapshot.chunks:
                doc=snapshot.documents.get(row['document_id'])
                if row['document_id'] in wanted and matches(row['document_id'],doc,state['explicit_filters'],snapshot):
                    item=snapshot.evidence[row['chunk_id']]
                    if sum(e.document_id==item.document_id for e in evidence)<2:evidence.append(item)
        state['retrieval_counts']={'source':name,'cache_hit':bool(result.get('cache_hit')),'checked_at':result.get('checked_at'),'imported_documents':len(imported)}
        event=state['budget'].events[-1]
        event['input_summary']='; '.join(f'{key}={value}' for key,value in state['retrieval_counts'].items())[:500]
        return evidence[:8]
