"""Publish scraped data without evaluating formulas or clearing live data first."""
import logging
from uuid import uuid4

from gspread_dataframe import set_with_dataframe


def write_literal_dataframe(worksheet, dataframe, **kwargs):
    # Escape every string: this also preserves leading zeros and date-like text.
    set_with_dataframe(worksheet, dataframe, include_index=False,
                       allow_formulas=False, string_escaping='full', **kwargs)


def replace_worksheets(spreadsheet, replacements, chunk_size=5000, before_swap=None):
    """Stage every worksheet, then publish all values in one atomic API batch.

    Target sheet IDs and formatting survive. Failed uploads never clear targets.
    Google documents batch atomicity at:
    https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate
    """
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    staged = []
    requests = []
    try:
        for target, frame in replacements:
            rows, cols = len(frame) + 1, max(len(frame.columns), 1)
            stage = spreadsheet.add_worksheet(
                title='_austender_stage_' + uuid4().hex,
                rows=max(rows, 2), cols=max(cols, 2))
            staged.append(stage)
            for start in range(0, max(len(frame), 1), chunk_size):
                write_literal_dataframe(
                    stage, frame.iloc[start:start + chunk_size],
                    row=1 if start == 0 else start + 2,
                    include_column_header=start == 0)
            requests.extend([
                {'updateCells': {'range': {'sheetId': target.id},
                                 'fields': 'userEnteredValue'}},
                # The grid is sized to the data: a smaller refresh must not leave the old
                # size behind, and the destination below is exactly the source's size,
                # because Google repeats a pasted block to fill a larger destination.
                {'updateSheetProperties': {
                    'properties': {'sheetId': target.id, 'gridProperties': {
                        'rowCount': max(rows, 2), 'columnCount': max(cols, 2)}},
                    'fields': 'gridProperties.rowCount,gridProperties.columnCount'}},
                {'copyPaste': {
                    'source': {'sheetId': stage.id, 'startRowIndex': 0,
                               'endRowIndex': rows, 'startColumnIndex': 0,
                               'endColumnIndex': cols},
                    'destination': {'sheetId': target.id, 'startRowIndex': 0,
                                    'endRowIndex': rows, 'startColumnIndex': 0,
                                    'endColumnIndex': cols},
                    'pasteType': 'PASTE_VALUES'}}])
        if requests:
            if before_swap is not None:
                before_swap()
            spreadsheet.batch_update({'requests': requests})
    finally:
        for stage in staged:
            try:
                spreadsheet.del_worksheet(stage)
            except Exception:
                logging.getLogger(__name__).warning(
                    'Could not remove staging worksheet %s', stage.title, exc_info=True)
