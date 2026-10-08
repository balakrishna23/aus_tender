"""Real embedding contrast checks plus fresh stored-data constraint checks."""
import json
from pathlib import Path
import assistant
import hybrid

CASES=[
    ('moving workloads from on-premises servers to Azure','Azure Migration Project Services'),
    ('investigating security breaches','Cyber Security Incident Response Analyst'),
    ('automated software quality assurance','Software Test Automation Specialist'),
    ('controlling privileged user access','Identity and Access Management Engineer'),
    ('recruit Indigenous suppliers','Social procurement partnerships with First Nations businesses'),
]

def evaluate():
    records=[];negative='Office furniture delivery and installation'
    for query,text in CASES:
        vectors=hybrid.embed([query,text,negative])
        cosine=[sum(a*b for a,b in zip(vectors[0],v)) for v in vectors[1:]]
        rows=[{'description':text},{'description':negative}]
        selected=hybrid.fuse(rows,[text,negative],query,cosine)
        lexical=hybrid.lexical_scores(query,[text,negative])
        records.append({'query':query,'expected_description':text,'positive_cosine':cosine[0],
            'negative_cosine':cosine[1],'keyword_found_expected':lexical[0]>0,
            'hybrid_found_expected':any(r['description']==text for r in selected),
            'unrelated_negative_rejected':not any(r['description']==negative for r in selected)})
    examples=[]
    for topic in ['cloud migration','investigating security breaches']:
        question='Show contracts ending within 90 days above $100k, excluding equipment and assessments'
        result=assistant.answer(question,False,verify=False,hybrid=True,search_topic=topic)
        assert all(r['current_value']>100000 for r in result['facts'])
        assert all(0<=r['days_until_end']<=90 for r in result['facts'])
        assert all(not any(w in r['description'].lower() for w in ['equipment','hardware','assessment','audit']) for r in result['facts'])
        examples.append({'question':question,'topic':topic,'seconds':result['elapsed_seconds'],
            'retrieval_status':result['retrieval_status'],'top_evidence':result['facts'][:3]})
    report={'scope':'Five synthetic contrast pairs and two stored-data constraint checks; not independent search accuracy.',
        'contrast_cases':records,'keyword_recall':sum(r['keyword_found_expected'] for r in records),
        'hybrid_recall':sum(r['hybrid_found_expected'] for r in records),
        'negatives_rejected':sum(r['unrelated_negative_rejected'] for r in records),
        'total_contrast_cases':len(records),'stored_examples':examples}
    path=Path(__file__).parent/'reports'/'hybrid-evaluation.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('contrast_cases','stored_examples')}))
    print('Detailed results:',path)

if __name__=='__main__':evaluate()
