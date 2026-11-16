import hashlib
import json
import runpy
from pathlib import Path
from uuid import uuid4

from forma.accounting.context import usage_context
from forma.accounting.usage import UsageLedger
from forma.settings.config import Settings


def test_cost_comparison_reads_existing_operations_without_mutation_or_private_report_content(tmp_path):
    compare = runpy.run_path(str(Path('scripts/benchmark_costs.py')))['compare_recorded_costs']
    settings, ledger = Settings(model='gpt-4.1-mini', api_key='private-key'), UsageLedger()
    reports = []
    for index in range(2):
        run_id, document_id = uuid4().hex, uuid4().hex
        if index == 0:
            with usage_context(run_id=run_id, document_id=document_id, stage='draft'):
                ledger.record(settings, {'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})
            with usage_context(run_id=run_id, document_id=document_id, stage='review'):
                ledger.record(settings, None, status=0)
        report = tmp_path / f'case-{index}.forma.json'
        report.write_text(json.dumps({'usage_run_id': run_id, 'usage_document_id': document_id,
                                     'input_tokens': 100 if index == 0 else 0,
                                     'source': 'Private document name', 'segments': ['PRIVATE CONTENT'],
                                     'options': {'instructions': 'PRIVATE INSTRUCTIONS'},
                                     'translation_profile': {'model': 'private-key'}}))
        reports.append(report)
    before = hashlib.sha256(ledger.path.read_bytes()).digest()
    result = compare(reports, ledger.path)
    assert result['paid_api_calls'] == 0 and result['database_read_only']
    first, second = result['runs']
    assert first['recorded_totals']['requests'] == 2
    assert first['recorded_totals']['unpriced'] == 1
    assert first['recorded_totals']['cost_status'] == 'incomplete'
    assert second['recorded_totals']['cost_status'] == 'no_recorded_requests'
    assert hashlib.sha256(ledger.path.read_bytes()).digest() == before
    serialized = json.dumps(result)
    assert 'PRIVATE' not in serialized and 'Private document' not in serialized and 'private-key' not in serialized
