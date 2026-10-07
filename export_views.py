#!/usr/bin/env python3
"""The engine views as one Excel workbook for review (exports/, git-ignored: Illoura confidential).

Usage:
  python export_views.py            # exports/illoura_views_YYYYMMDD.xlsx
"""
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from project_paths import PROJECT_DIR, database_directory

TWO_YEARS_AGO = (date.today() - timedelta(days=730)).isoformat()
TABS = [
    ('TODAY', 'Draft TODAY list: rules only; bench and relationships not scored yet',
     'SELECT rank, priority, score, organisation, action_type, why_now, person_and_route, offer, owner, due, '
     'contact_safety, evidence, score_parts, assumptions FROM today ORDER BY rank'),
    ('Renewals 0-90 days', 'ICT and labour-hire contracts ending in the next 90 days',
     'SELECT window, days_left, current_end_date, buyer, supplier, supplier_on_mmr_list, description, category_family, '
     'current_value, contact_name, contact_email, contact_phone, branch, division, page_url FROM renewals '
     'WHERE days_left <= 90 ORDER BY days_left, current_value DESC'),
    ('MMR primes', 'NIAA MMR companies linked to ABNs, with contract counts (match review in inputs/mmr)',
     'SELECT organisation, abn, mmr_contracts, list_date, match, match_review, contracts, active_contracts, '
     'active_it_contracts, active_over_7_5m FROM mmr_primes ORDER BY active_it_contracts DESC'),
    ('MMR candidates (IT)', 'Active IT contracts of $7.5m+ held by NIAA-listed companies: candidates only',
     'SELECT supplier, buyer, cn_id, description, category_family, current_value, start_date, current_end_date, '
     'days_left, status, page_url FROM mmr_candidates WHERE it_contract = 1 ORDER BY current_value DESC'),
    ('Exemption 16 by agency', 'ICT and labour-hire contracts bought directly from Indigenous SMEs, last 2 years',
     f"SELECT buyer, COUNT(*) AS contracts, ROUND(SUM(current_value)) AS total_value, MAX(first_published) AS latest "
     f"FROM exemption_use WHERE category_family IS NOT NULL AND first_published >= '{TWO_YEARS_AGO}' "
     f"GROUP BY buyer ORDER BY contracts DESC"),
    ('Exemption 16 contracts', 'Every Appendix A item 16 contract (all categories)',
     'SELECT buyer, supplier, supplier_abn, cn_id, description, category_family, current_value, first_published, '
     'current_end_date, page_url FROM exemption_use ORDER BY first_published DESC'),
    ('Hot list', 'Planned ICT procurements expected this quarter or the next two, with contacts',
     'SELECT buyer, title, expected_from, expected_to, category, contact_name, contact_email, contact_phone, '
     'relevance_reason, url FROM hot_list ORDER BY expected_from'),
    ('Open ICT opportunities', 'Live and relevant: formal channel only',
     "SELECT source, source_id, kind, title, buyer, closes, role_families, relevance_reason, contact_safety, url "
     "FROM opportunities WHERE status_effective = 'Open' AND is_relevant = 1 ORDER BY closes_at"),
    ('Unclear titles', 'Open items with a single weak keyword: for AI or a person to decide',
     "SELECT source, source_id, title, buyer, category, relevance_reason, url FROM opportunities "
     "WHERE status_effective = 'Open' AND needs_review = 1"),
    ('BuyICT rates', 'BuyICT requests linked to their contracts; day rate is an estimate',
     'SELECT buyict_id, role_title, buyer, supplier, contract_value, start_date, end_date, business_days, '
     'estimated_day_rate, rate_note, page_url FROM buyict_links ORDER BY buyict_id'),
    ('Panel members', 'Current members of the watched panels, with ABNs and contract counts',
     'SELECT panel, son_id, member, abn, state, deed_end, contracts, active_contracts, it_contracts, panel_contracts '
     'FROM panel_members ORDER BY panel, panel_contracts DESC'),
    ('NSW small ICT contracts', 'NSW ICT contract awards of $250,000 or less',
     'SELECT reference, title, agency, value, category, published, url FROM nsw_small_contracts ORDER BY value'),
    ('Organisation review', 'Possible duplicate organisations: never merged without a decision',
     'SELECT raw_name, source, candidate_name, candidate_org, similarity, decision FROM organisation_review '
     'ORDER BY similarity DESC'),
]
HEADER = PatternFill('solid', fgColor='1F3A5F')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    source = database_directory() / 'intelligence.db'
    target = PROJECT_DIR / 'exports' / f'illoura_views_{date.today():%Y%m%d}.xlsx'
    target.parent.mkdir(exist_ok=True)
    book = Workbook()
    book.remove(book.active)
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as conn:
        built = conn.execute('SELECT built_at FROM build_info').fetchone()[0]
        for title, note, sql in TABS:
            sheet = book.create_sheet(title[:31])
            try:
                cursor = conn.execute(sql)
            except sqlite3.OperationalError:
                sheet.append([f'{note}. No rows yet.'])
                print(f'{title:<26} no rows yet')
                continue
            columns = [c[0] for c in cursor.description]
            sheet.append([f'{note}. Built {built}. Source links on every row.'])
            sheet['A1'].font = Font(italic=True, color='555555')
            sheet.append(columns)
            for cell in sheet[2]:
                cell.font, cell.fill = Font(bold=True, color='FFFFFF'), HEADER
            count = 0
            for row in cursor:
                sheet.append(list(row))
                count += 1
            for i, name in enumerate(columns, 1):
                width = 60 if name in ('why_now', 'description', 'title', 'assumptions', 'person_and_route') else (
                    45 if name.endswith('url') or name == 'evidence' else 18)
                sheet.column_dimensions[get_column_letter(i)].width = width
            if title == 'TODAY':
                for row in sheet.iter_rows(min_row=3):
                    for cell in row:
                        cell.alignment = Alignment(wrap_text=True, vertical='top')
            sheet.freeze_panes = 'A3'
            sheet.auto_filter.ref = f'A2:{get_column_letter(len(columns))}{count + 2}'
            print(f'{title:<26} {count} rows')
    book.save(target)
    print(f'Saved {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
