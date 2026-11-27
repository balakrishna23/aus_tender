"""Validate an optional private market package before adding it to the existing newsletter.

Missing/stale/malformed research never cancels a live procurement brief. It is recorded as unavailable.
"""
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA='illoura.market_brief.v1'


def read_market_context(path,edition_day,now=None):
    path=Path(path)
    if not path.is_file():return [],dict(status='unavailable',reason='no market research package')
    try:
        if path.stat().st_size>1_000_000:raise ValueError('research package exceeds size limit')
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema')!=SCHEMA:raise ValueError('unsupported market package schema')
        as_of=date.fromisoformat(data['as_of']);generated=datetime.fromisoformat(data['generated_at'])
        now=now or datetime.now(timezone.utc)
        if generated.tzinfo is None or generated>now+timedelta(minutes=5):raise ValueError('invalid generation timestamp')
        if not 0<=(edition_day-as_of).days<=7:raise ValueError('research package outside seven-day window')
        if (now-generated).total_seconds()>8*86400:raise ValueError('research package generation is stale')
        sections=data['sections']
        if not isinstance(sections,list) or len(sections)>6:raise ValueError('unexpected section count')
        cleaned=[]
        for section in sections:
            if set(section)!={'title','items'} or not isinstance(section['title'],str) or not isinstance(section['items'],list):raise ValueError('invalid section')
            if len(section['items'])>5:raise ValueError('too many research items')
            items=[]
            for item in section['items']:
                if set(item)!={'heading','paragraphs','links'}:raise ValueError('invalid research item')
                if not isinstance(item['heading'],str) or not isinstance(item['paragraphs'],list) or not all(isinstance(p,str) for p in item['paragraphs']):raise ValueError('invalid research text')
                if len(item['heading'])>300 or len(item['paragraphs'])>8 or any(len(p)>2500 for p in item['paragraphs']):raise ValueError('research text too long')
                if not isinstance(item['links'],list) or len(item['links'])>5:raise ValueError('invalid research links')
                for link in item['links']:
                    if set(link)!={'label','url'} or not isinstance(link['label'],str):raise ValueError('invalid source link')
                    url=urlsplit(link['url'])
                    if url.scheme!='https' or not url.hostname or url.username or url.password:raise ValueError('unsafe source URL')
                items.append(item)
            cleaned.append(dict(title=section['title']+' · research as of '+str(as_of),items=items))
        age_hours=max(0,(now-generated).total_seconds()/3600)
        if age_hours>32:
            cleaned.insert(0,dict(title='Research freshness',items=[dict(
                heading='Older research supplement',
                paragraphs=[f'This research package was generated {age_hours:.0f} hours ago '
                            f'and is dated {as_of}. Its publisher editions may be older. '
                            'Check current evidence before acting; RFQ checks are recorded separately.'],links=[])]))
        return cleaned,dict(status='included',as_of=str(as_of),generated_at=generated.isoformat(),
                            age_hours=round(age_hours,1),degraded=age_hours>32,sections=len(cleaned))
    except (ValueError,KeyError,TypeError,OSError,OverflowError):
        return [],dict(status='unavailable',reason='market research package failed validation')
