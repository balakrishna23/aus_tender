"""Strict parsers. Database payloads exclude directory people/contact records."""
import csv
from pathlib import Path
from dataclasses import dataclass, field
import re
import unicodedata
from urllib.parse import urlsplit

from defusedxml import ElementTree as ET


class SourceError(ValueError):
    pass


EMAIL_PATTERN=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+')


def website(value,issues):
    value=(value or '').strip()
    if not value:return ''
    if EMAIL_PATTERN.search(value):
        issues.append('website_contact_data_omitted')
        return ''
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
    except ValueError:
        issues.append('invalid_website')
        return ''
    return value


def name_key(value):
    value = unicodedata.normalize('NFKC', value or '').casefold()
    return ' '.join(re.findall(r'\w+', value, flags=re.UNICODE))


LEGAL_SUFFIXES = frozenset('pty ltd limited inc incorporated co company corp corporation plc llc llp'.split())


def legal_key(value):
    """name_key without an initial 'the' or terminal legal-form tokens ('The Acme Pty Ltd' -> 'acme')."""
    tokens = name_key(value).split()
    if tokens[:1] == ['the']:
        tokens.pop(0)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return ' '.join(tokens)


def valid_abn(value):
    if value is None:
        return None
    value = str(value)
    if not value.strip():
        return None
    number = re.sub(r'\s', '', value)
    if not re.fullmatch(r'[0-9]{11}', number):
        raise SourceError('ABN must contain eleven digits')
    digits = list(map(int, number))
    digits[0] -= 1
    if sum(a*b for a, b in zip(digits, [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19])) % 89:
        raise SourceError('ABN checksum failed')
    return number


@dataclass
class Observation:
    publisher_id: str
    name: str
    kind: str
    payload: dict
    abn: str | None = None
    issues: list = field(default_factory=list)


def checked_abn(value, issues):
    try:
        return valid_abn(value)
    except SourceError:
        issues.append('invalid_abn')
        return None


def parse_agor(path):
    records, seen = [], set()
    allowed = ['Id', 'Title', 'Portfolio', 'Classification', 'Type of Body', 'Creation Date',
               'ABN', 'Parent Organisation', 'Head Office State', 'Head Office Postcode', 'Website Address']
    # Finance's published snapshots can use Windows-1252 punctuation. Decode
    # strictly; never replace undecodable bytes or change the retained raw file.
    try:
        Path(path).read_bytes().decode('utf-8-sig')
        encoding = 'utf-8-sig'
    except UnicodeDecodeError:
        Path(path).read_bytes().decode('cp1252')
        encoding = 'cp1252'
    with open(path, encoding=encoding, newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'Id', 'Title', 'Portfolio', 'ABN', 'Type of Body'}.issubset(reader.fieldnames or []):
            raise SourceError('AGOR required columns missing')
        for row in reader:
            if None in row:
                raise SourceError('AGOR row has unexpected columns')
            number, title = row['Id'].strip(), row['Title'].strip()
            if not number or not title or not re.fullmatch(r'[A-Z]-[0-9]+', number):
                raise SourceError('AGOR row lacks valid publisher identity/title')
            if number in seen:
                raise SourceError('Duplicate AGOR publisher ID')
            seen.add(number)
            issues = []
            abn = checked_abn(row.get('ABN'), issues)
            payload={k:row.get(k,'').strip() for k in allowed}
            payload['Website Address']=website(payload['Website Address'],issues)
            records.append(Observation(number,title,'body',payload,abn,issues))
    if not records:
        raise SourceError('Empty AGOR snapshot')
    return records, {'source_rows': len(records), 'excluded_rows': 0, 'encoding': encoding}


def parse_directory(path):
    allowed = ['content_id', 'unique_record_id', 'title', 'type', 'portfolio_id', 'parent_organisation',
               'parent_directory_structure', 'abn', 'website', 'type_of_body', 'classification']
    kinds = {'organisation': 'body', 'other_entities': 'body', 'board': 'body',
             'portfolio': 'portfolio', 'directory_sub_structure': 'unit'}
    records, seen, excluded, total = [], set(), 0, 0
    with open(path, 'rb') as stream:
        parser = ET.iterparse(stream, events=('start', 'end'))
        depth = 0
        for event, item in parser:
            if event == 'start':
                depth += 1
                if depth == 1 and item.tag != 'response':
                    raise SourceError('Directory root structure changed')
                if item.tag == 'item' and depth != 2:
                    raise SourceError('Nested Directory item is not supported')
                continue
            depth -= 1
            if item.tag != 'item':
                continue
            total += 1
            source_kind = item.findtext('type', '').strip()
            if source_kind not in kinds:
                excluded += 1
                item.clear()
                continue
            payload = {k: item.findtext(k, '').strip() for k in allowed}
            number, title = payload['unique_record_id'], payload['title']
            if not number or not title or not re.fullmatch(r'[A-Z]-[0-9]+', number):
                raise SourceError('Directory row lacks valid publisher identity/title')
            if number in seen:
                raise SourceError('Duplicate Directory publisher ID')
            seen.add(number)
            issues = []
            # This published dummy record is retained as evidence, never promoted to a buyer.
            if 'dummy organisation' in title.casefold():
                issues.append('publisher_placeholder')
            abn = checked_abn(payload['abn'], issues)
            payload['website']=website(payload['website'],issues)
            records.append(Observation(number, title, kinds[source_kind], payload, abn, issues))
            item.clear()
    if not records:
        raise SourceError('Empty Directory snapshot')
    return records, {'source_rows': total, 'excluded_rows': excluded}
