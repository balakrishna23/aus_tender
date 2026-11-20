"""Contact-free report files, with escaped HTML and spreadsheet-safe CSV cells."""
import csv
from datetime import date, datetime
from decimal import Decimal
import html
import json
from pathlib import Path
import uuid


def json_value(value):
    if isinstance(value,Decimal): return str(value)
    if isinstance(value,(date,datetime,uuid.UUID)): return str(value)
    raise TypeError('Unsupported JSON type: '+type(value).__name__)


def csv_cell(value):
    if isinstance(value,(list,dict)): value=json.dumps(value,default=json_value,ensure_ascii=False)
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')): return "'"+value
    return value


def write_report_files(report,directory,name,rows,fields,title):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/(name+'.json')).write_text(json.dumps(report,default=json_value,ensure_ascii=False,indent=2),encoding='utf-8')
    with (directory/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(fields)
        writer.writerows([csv_cell(r.get(f)) for f in fields] for r in rows)
    esc=lambda v:html.escape(str(v if v is not None else 'Unknown'))
    header=''.join('<th scope="col">'+esc(f.replace('_',' '))+'</th>' for f in fields)
    body=''.join('<tr>'+''.join('<td>'+esc(r.get(f))+'</td>' for f in fields)+'</tr>' for r in rows[:100])
    content=('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>'+esc(title)+'</title><style>body{font:16px/1.5 system-ui;margin:32px auto;max-width:1200px;padding:0 20px;'
             'color:#163144;background:#f4f6f8;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;'
             'background:#fff;width:100%;min-width:800px}td,th{min-width:110px;padding:10px;border:1px solid #d9e0e5;text-align:left}h1{line-height:1.2}</style>'
             '<h1>'+esc(title)+'</h1><p>Private internal research · As of '+esc(report.get('as_of'))+'</p><p>'
             +esc(report.get('status','draft_for_review'))+'</p><ul>'+''.join('<li>'+esc(v)+'</li>' for v in report.get('limitations',[]))
             +'</ul><p>'+str(len(rows))+' records; first 100 shown. Complete data in the accompanying CSV and JSON. On smaller screens, scroll the table horizontally.</p>'
             '<div class="table-wrap"><table><thead><tr>'+header+'</tr></thead><tbody>'+body+'</tbody></table></div></html>')
    (directory/(name+'.html')).write_text(content,encoding='utf-8')
    return dict(rows=len(rows),json=str(directory/(name+'.json')),html=str(directory/(name+'.html')))
