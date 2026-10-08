"""Local lexical/semantic fusion over freshly SQL-filtered evidence.

The private SQLite cache contains text vectors, never authoritative contract facts.
No model call is made until every eligible text is present in the cache.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

MODEL = 'all-minilm:22m'
INDEX = Path(os.environ.get('ILLOURA_HYBRID_INDEX', str(Path(__file__).parent/'runtime'/'hybrid.db')))
ENDPOINT = 'http://127.0.0.1:11434'
MIN_COSINE = 0.45  # Relevance gate, not a probability or factual-confidence score.
STOP = set('a an the of for to in on and or with by from about find show which what contracts contract renewals renewal opportunities opportunity work services service'.split())


def chunks(text):
    words=str(text or '').split()
    return [' '.join(words[i:i+140]) for i in range(0,len(words),120) if words[i:]]


def key(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def tokens(text):
    return [w for w in re.findall(r'[a-z0-9]+',str(text).lower()) if w not in STOP]


def model_digest():
    with urlopen(ENDPOINT+'/api/tags',timeout=5) as response:
        models=json.load(response)['models']
    for model in models:
        if model['name']==MODEL:return model['digest']
    raise ValueError('Local embedding model is not installed')


def embed(texts, timeout=30):
    if not texts:return []
    payload={'model':MODEL,'input':texts,'truncate':False,'keep_alive':'5m',
             'options':{'num_gpu':0,'num_thread':4}}
    request=Request(ENDPOINT+'/api/embed',data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=timeout) as response:result=json.load(response)
    vectors=result.get('embeddings',[])
    if len(vectors)!=len(texts):raise ValueError('Embedding response count mismatch')
    normalized=[]
    for vector in vectors:
        if len(vector)!=384 or any(not math.isfinite(x) for x in vector):
            raise ValueError('Invalid embedding dimensions or values')
        norm=math.sqrt(sum(x*x for x in vector))
        if norm==0:raise ValueError('Zero embedding vector')
        normalized.append([x/norm for x in vector])
    return normalized


def pack(vector):return struct.pack('<384f',*vector)
def unpack(blob):
    if len(blob)!=384*4:raise ValueError('Invalid cached embedding')
    return struct.unpack('<384f',blob)


def lexical_scores(query, texts):
    """BM25 with a coverage gate to avoid a single generic token admitting records."""
    query_words=set(tokens(query)); documents=[Counter(tokens(t)) for t in texts]
    if not documents or not query_words:return [0.0]*len(texts)
    average=sum(sum(d.values()) for d in documents)/len(documents) or 1
    df={w:sum(w in d for d in documents) for w in query_words}
    scores=[]
    for document in documents:
        coverage=len(query_words.intersection(document))/len(query_words)
        score=0.0
        if coverage>=0.6:
            length=sum(document.values())
            for word in query_words:
                frequency=document[word]
                inverse=math.log(1+(len(documents)-df[word]+0.5)/(df[word]+0.5))
                score+=inverse*frequency*2.2/(frequency+1.2*(0.25+0.75*length/average))
        scores.append(score)
    return scores


def fuse(rows, texts, topic, similarities, minimum=MIN_COSINE):
    lexical=lexical_scores(topic,texts)
    lex_rank={i:rank for rank,i in enumerate(sorted(range(len(rows)),key=lambda i:(-lexical[i],i)),1) if lexical[i]>0}
    # A one-word generic description must not pass on a weak semantic association.
    sem_rank={i:rank for rank,i in enumerate(sorted(range(len(rows)),key=lambda i:(-similarities[i],i)),1)
              if similarities[i]>=(minimum if len(tokens(texts[i]))>=3 else max(minimum,0.60))}
    selected=[]
    for i,row in enumerate(rows):
        if i not in lex_rank and i not in sem_rank:continue
        fused=(1/(60+lex_rank[i]) if i in lex_rank else 0)+(1/(60+sem_rank[i]) if i in sem_rank else 0)
        selected.append({**row,'retrieval':{'method':'keyword + semantic' if i in lex_rank and i in sem_rank else 'keyword' if i in lex_rank else 'semantic',
            'cosine_similarity':round(float(similarities[i]),4),'bm25':round(lexical[i],4),
            'fusion_score':round(fused,6),'topic':topic,'limit':'Relevance only; not hiring, suitability or factual confidence.'}})
    selected.sort(key=lambda r:-r['retrieval']['fusion_score'])
    return selected


def rank(rows, topic, text_field='description', index=INDEX, embedder=embed, digest_provider=model_digest):
    """Returns fresh rows and an explicit status. Incomplete indexes never broaden search."""
    status={'mode':'hybrid','topic':topic,'eligible_records':len(rows),'embedding_model':MODEL,
            'minimum_cosine':MIN_COSINE,'scope':'Relevance search within exact SQL constraints'}
    if not rows:return [],{**status,'matched_records':0}
    if not index.is_file():raise ValueError('Hybrid index is missing; run build_hybrid_index.py')
    parts=[chunks(row.get(text_field,'')) for row in rows]
    hashes=list(dict.fromkeys(key(part) for group in parts for part in group))
    vectors={}
    with closing(sqlite3.connect(index.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as connection:
        metadata=dict(connection.execute('SELECT name,value FROM metadata'))
        if metadata.get('model')!=MODEL or metadata.get('model_digest')!=digest_provider():
            raise ValueError('Embedding model differs from the index; rebuild the index')
        for offset in range(0,len(hashes),400):
            batch=hashes[offset:offset+400]
            for hashed,blob in connection.execute('SELECT hash,vector FROM embeddings WHERE hash IN ('+','.join('?' for _ in batch)+')',batch):
                vectors[hashed]=unpack(blob)
    absent=set(hashes)-vectors.keys()
    if absent:raise ValueError(f'Hybrid index lacks {len(absent)} new/changed text chunks; run build_hybrid_index.py')
    query_parts=chunks(topic)
    if len(query_parts)!=1:raise ValueError('Use a short search topic (up to 140 words)')
    query_vector=embedder(query_parts)[0]
    # NumPy comes with the existing pandas stack; no vector service or GPU needed.
    import numpy as np
    matrix=np.asarray([vectors[h] for h in hashes],dtype=np.float32)
    scores=matrix@np.asarray(query_vector,dtype=np.float32) if hashes else []
    score_by_hash=dict(zip(hashes,scores))
    similarities=[max((score_by_hash[key(p)] for p in group),default=-1.0) for group in parts]
    selected=fuse(rows,[str(r.get(text_field,'') or '') for r in rows],topic,similarities)
    return selected,{**status,'matched_records':len(selected),'indexed_build':metadata.get('source_build'),
                     'rank_method':'Reciprocal rank fusion (k=60); relevance gate applied',
                     'short_description_cosine_floor':0.60}


def topic_for(question, plan, supplied=None):
    if supplied is not None:
        if not isinstance(supplied,str) or len(supplied)>300:raise ValueError('Search topic must be text of at most 300 characters')
        if supplied.strip():return supplied.strip()
    quoted=re.search(r'\b(?:about|related to|for)\s+["“]([^"”]{1,300})["”]',question,re.I)
    if quoted:return quoted.group(1).strip()
    related=re.search(r'\brelated to\s+(.+?)(?=\s+(?:ending|ends?|expiring|within|above|below|over|under|at least|at most|exclude|excluding|held by|awarded to)\b|[?.;]|$)',question,re.I)
    if related:return related.group(1).strip()[:300]
    # Role terms help broad discovery. Do not embed the question's dates or dollar bounds.
    return ' '.join(plan.get('roles',[])).replace('AI/data','data engineering artificial intelligence')
