"""Check the running loopback service without exposing its browser token."""
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = 'http://127.0.0.1:8765'
with urlopen(base) as response:
    html = response.read().decode()
token = re.search(r"const token='([^']+)'", html).group(1)
body = json.dumps({'question': 'What data do we have?', 'explain': False}).encode()
with urlopen(Request(base+'/api/ask', data=body, headers={
        'Content-Type': 'application/json', 'X-Pilot-Token': token})) as response:
    result = json.load(response)
assert result['facts'] and not result['model_used']
for headers in [{'Content-Type': 'application/json'},
                {'Content-Type': 'application/json', 'X-Pilot-Token': token, 'Host': 'evil.example'}]:
    try:
        urlopen(Request(base+'/api/ask', data=body, headers=headers))
    except HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError('Unauthenticated/foreign-host request accepted')
print(json.dumps({'http_smoke': 'passed', 'database_seconds': result['elapsed_seconds']}))
