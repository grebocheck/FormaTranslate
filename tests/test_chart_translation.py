"""Chart caches and embedded source cells must stay consistent after translation."""

from __future__ import annotations

import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from lxml import etree
from openpyxl import Workbook, load_workbook
from support import local_settings

from forma.core.models import FormaError, Options
from forma.documents.charts import UnsupportedChart, reference_cell
from forma.documents.comparison import compare_files
from forma.documents.editor import edit_translation_markup, export_reviewed_copy
from forma.documents.ooxml import C, OfficeDocument, R, S
from forma.documents.review import load_report
from forma.translation.engine import TranslationEngine


def chart_document(tmp_path, *, shared=False, stale=False, external=False, formula=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Chart data"
    sheet.append(["Electricity consumption", "Unrelated label", 31.5])
    sheet.append(["January", "Electricity consumption", "=C1*2"])
    if formula:
        sheet['A1'] = '=B1'
    book = io.BytesIO()
    workbook.save(book)
    book_data = book.getvalue()
    if shared:
        with ZipFile(io.BytesIO(book_data)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        root = etree.fromstring(parts['xl/worksheets/sheet1.xml'])
        for address in ('A1', 'B2'):
            cell = root.find(f'.//{{{S}}}c[@r="{address}"]')
            cell.remove(cell.find(f'{{{S}}}is'))
            cell.set('t', 's')
            etree.SubElement(cell, f'{{{S}}}v').text = '0'
        parts['xl/worksheets/sheet1.xml'] = etree.tostring(root)
        parts['xl/sharedStrings.xml'] = f'<sst xmlns="{S}" count="2" uniqueCount="1"><si><t>Electricity consumption</t></si></sst>'.encode()
        content = etree.fromstring(parts['[Content_Types].xml'])
        etree.SubElement(content, '{http://schemas.openxmlformats.org/package/2006/content-types}Override', PartName='/xl/sharedStrings.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml')
        parts['[Content_Types].xml'] = etree.tostring(content)
        rels = etree.fromstring(parts['xl/_rels/workbook.xml.rels'])
        etree.SubElement(rels, '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship', Id='rShared', Type=R+'/sharedStrings', Target='sharedStrings.xml')
        parts['xl/_rels/workbook.xml.rels'] = etree.tostring(rels)
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
            for name, data in parts.items():
                archive.writestr(name, data)
        book_data = buffer.getvalue()
    source = tmp_path/'chart.docx'
    document = Document()
    document.add_paragraph('Heat meter')
    document.save(source)
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    cache_text = 'Stale label' if stale else 'Electricity consumption'
    chart = f'''<c:chartSpace xmlns:c="{C}" xmlns:r="{R}"><c:chart><c:plotArea><c:barChart><c:ser>
      <c:tx><c:strRef><c:f>'Chart data'!$A$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{cache_text}</c:v></c:pt></c:strCache></c:strRef></c:tx>
      <c:cat><c:strRef><c:f>'Chart data'!$A$2</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>January</c:v></c:pt></c:strCache></c:strRef></c:cat>
      <c:val><c:numLit><c:ptCount val="1"/><c:pt idx="0"><c:v>31.5</c:v></c:pt></c:numLit></c:val>
      </c:ser><c:ser><c:tx><c:v>Annual total</c:v></c:tx></c:ser></c:barChart></c:plotArea></c:chart><c:externalData r:id="rBook"/></c:chartSpace>'''.encode()
    target = 'https://example.invalid/book.xlsx' if external else '../embeddings/book.xlsx'
    mode = 'External' if external else 'Internal'
    relation = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rBook" Type="{R}/package" Target="{target}" TargetMode="{mode}"/></Relationships>'.encode()
    parts.update({'word/charts/chart1.xml': chart, 'word/charts/chart2.xml': chart,
                  'word/charts/_rels/chart1.xml.rels': relation, 'word/charts/_rels/chart2.xml.rels': relation,
                  'word/embeddings/book.xlsx': book_data})
    with ZipFile(source, 'w', ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return source


WORDS = {'Heat meter': 'Лічильник тепла', 'Electricity consumption': 'Споживання електроенергії',
         'January': 'Січень', 'Annual total': 'Річний підсумок'}


def translations(document):
    return {segment.id: f'<r0>{WORDS[segment.plain]}</r0>' for segment in document.segments}


@pytest.mark.parametrize('shared', [False, True])
def test_chart_translation_updates_all_bound_caches_and_only_the_source_cell(tmp_path, shared):
    source = chart_document(tmp_path, shared=shared)
    original = source.read_bytes()
    document = OfficeDocument(source)
    assert document.extraction_profile == 3
    assert len([segment for segment in document.segments if segment.plain == 'Electricity consumption']) == 1
    assert not any(issue.code == 'chart_labels_preserved' for issue in document.issues)
    output = tmp_path/'translated.docx'
    document.write(translations(document), output)
    with ZipFile(output) as archive:
        for part in ('word/charts/chart1.xml', 'word/charts/chart2.xml'):
            root = etree.fromstring(archive.read(part))
            assert root.xpath('//c:v/text()', namespaces={'c': C}) == ['Споживання електроенергії', 'Січень', '31.5', 'Річний підсумок']
        workbook = load_workbook(io.BytesIO(archive.read('word/embeddings/book.xlsx')))
        sheet = workbook.active
        assert sheet['A1'].value == 'Споживання електроенергії'
        assert sheet['A2'].value == 'Січень'
        assert sheet['B2'].value == 'Electricity consumption'
        assert sheet['B1'].value == 'Unrelated label'
        assert sheet['C1'].value == 31.5 and sheet['C2'].value == '=C1*2'
    assert source.read_bytes() == original
    comparison = compare_files(source, [output])['candidates'][0]
    assert comparison['structure_ok'] and comparison['numbers_preserved']
    second = tmp_path/'second.docx'
    document.write(translations(document), second)
    with ZipFile(output) as a, ZipFile(second) as b:
        assert all(a.read(name) == b.read(name) for name in a.namelist())


@pytest.mark.parametrize('kwargs', [{'stale': True}, {'external': True}, {'formula': True}])
def test_unproved_chart_links_are_preserved_with_warning(tmp_path, kwargs):
    source = chart_document(tmp_path, **kwargs)
    document = OfficeDocument(source)
    assert any(issue.code == 'chart_labels_preserved' for issue in document.issues)
    assert not any(segment.plain in {'Electricity consumption', 'Stale label'} for segment in document.segments)
    assert any(segment.plain == 'Annual total' for segment in document.segments)
    output = tmp_path/'copy.docx'
    document.write({item.id: item.source for item in document.segments}, output)
    with ZipFile(source) as a, ZipFile(output) as b:
        assert all(a.read(name) == b.read(name) for name in a.namelist())


def test_chart_reports_resume_legacy_review_and_bound_manual_edit(tmp_path):
    source = chart_document(tmp_path, shared=True)

    class Provider:
        input_tokens = output_tokens = calls = 0

        def complete(self, instruction, payload, schema, **kwargs):
            self.calls += 1
            return {'segments': [{'id': item['id'], 'text': item['text'].replace('Heat meter', WORDS['Heat meter']).replace('Electricity consumption', WORDS['Electricity consumption']).replace('January', WORDS['January']).replace('Annual total', WORDS['Annual total'])} for item in payload['segments']]}

    options = Options('en', 'uk', review=False, auto_glossary=False)
    provider = Provider()
    engine = TranslationEngine(local_settings(), provider=provider)
    result = engine.translate(source, tmp_path/'out', options)
    assert result.extraction_profile == 3
    assert load_report(result.report)[0].extraction_profile == 3
    calls = provider.calls
    again = engine.translate(source, tmp_path/'out', options)
    assert provider.calls == calls and again.cached_segments == len(result.segments)
    label = next(segment for segment in result.segments if segment.plain == 'Electricity consumption')
    replacement = edit_translation_markup(result.translations[label.id], 'Споживання електричної енергії')
    revised = export_reviewed_copy(source, result.output, result.report, {label.id: replacement})
    with ZipFile(revised.output) as archive:
        workbook = load_workbook(io.BytesIO(archive.read('word/embeddings/book.xlsx')))
        assert workbook.active['A1'].value == 'Споживання електричної енергії'
        for part in ('word/charts/chart1.xml', 'word/charts/chart2.xml'):
            assert 'Споживання електричної енергії' in archive.read(part).decode()
    assert provider.calls == calls
    legacy = OfficeDocument(source, extraction_profile=1)
    assert legacy.extraction_profile == 1 and len(legacy.segments) == 1
    legacy_output = tmp_path/'legacy.docx'
    legacy.write(translations(legacy), legacy_output)
    from forma.core.models import TranslationResult
    from forma.translation.reports import write_translation_report
    legacy_result = TranslationResult(source, legacy_output, legacy.segments, translations(legacy), [])
    write_translation_report(legacy_result, options, 'fake')
    legacy_copy = export_reviewed_copy(source, legacy_output, legacy_result.report, {legacy.segments[0].id: '<r0>Тепловий лічильник</r0>'})
    with ZipFile(source) as a, ZipFile(legacy_copy.output) as b:
        assert a.read('word/charts/chart1.xml') == b.read('word/charts/chart1.xml')
        assert a.read('word/embeddings/book.xlsx') == b.read('word/embeddings/book.xlsx')
    payload = json.loads(result.report.read_text())
    payload['extraction_profile'] = 2
    wrong = tmp_path/'wrong.forma.json'
    wrong.write_text(json.dumps(payload))
    with pytest.raises(FormaError):
        load_report(wrong)


@pytest.mark.parametrize('reference', ['multilevel', 'complex'])
def test_unknown_mirror_reference_preserves_shared_workbook_and_all_bound_caches(tmp_path, reference):
    source = chart_document(tmp_path)
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    tree = etree.fromstring(parts['word/charts/chart2.xml'])
    ref = tree.find(f'.//{{{C}}}strRef')
    if reference == 'complex':
        ref.find(f'{{{C}}}f').text = "'Chart data'!$A$1:$B$2"
    else:
        ref.tag = f'{{{C}}}multiLvlStrRef'
        cache = ref.find(f'{{{C}}}strCache')
        cache.tag = f'{{{C}}}multiLvlStrCache'
        point = cache.find(f'{{{C}}}pt')
        cache.remove(point)
        etree.SubElement(cache, f'{{{C}}}lvl').append(point)
    parts['word/charts/chart2.xml'] = etree.tostring(tree)
    with ZipFile(source, 'w', ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    document = OfficeDocument(source)
    assert any(issue.code == 'chart_labels_preserved' for issue in document.issues)
    assert {segment.plain for segment in document.segments} == {'Heat meter', 'Annual total'}
    output = tmp_path/'translated.docx'
    document.write(translations(document), output)
    with ZipFile(output) as archive:
        assert archive.read('word/embeddings/book.xlsx') == parts['word/embeddings/book.xlsx']
        for part in ('word/charts/chart1.xml', 'word/charts/chart2.xml'):
            values = etree.fromstring(archive.read(part)).xpath('//c:v/text()', namespaces={'c': C})
            assert values == ['Electricity consumption', 'January', '31.5', 'Річний підсумок']


@pytest.mark.parametrize('profile', [1, 3])
def test_reference_import_preserves_saved_chart_profile_and_rejects_rebound_workbook(tmp_path, profile):
    import hashlib

    from forma.core.models import TranslationResult
    from forma.memory.importing import import_reference
    from forma.memory.store import TranslationMemory
    from forma.translation.reports import write_translation_report

    source = chart_document(tmp_path)
    original = OfficeDocument(source, extraction_profile=profile)
    output = tmp_path/'translated.docx'
    values = translations(original)
    original.write(values, output)
    options = Options('en', 'uk', review=False, auto_glossary=False)
    result = TranslationResult(source, output, original.segments, values, [],
                               extraction_profile=profile, effective_glossary={})
    # Historical reports without hashes still use the recorded extraction
    # profile while reproducing the full package relation.
    write_translation_report(result, options, 'offline-test')
    store = TranslationMemory()
    project = store.create_project('Synthetic references', 'en', 'uk')
    imported = import_reference(store, project.id, source, output, result.report)
    assert imported['inserted'] == len(original.segments)
    if profile == 1:
        return
    with ZipFile(output) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(parts['word/charts/chart2.xml'])
    root.find(f'.//{{{C}}}f').text = "'Chart data'!$B$2"
    parts['word/charts/chart2.xml'] = etree.tostring(root)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    # Even a report with refreshed file hashes must prove the structural
    # source/output relationship rather than trusting changed chart links.
    metadata = json.loads(result.report.read_text())
    metadata['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata['output_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    result.report.write_text(json.dumps(metadata))
    with pytest.raises(FormaError):
        import_reference(store, project.id, source, output, result.report)


def test_chart_context_binds_unseen_values_while_preserving_readable_units(tmp_path):
    source = chart_document(tmp_path)
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(parts['word/charts/chart1.xml'])
    values = root.find(f'.//{{{C}}}numLit')
    for index in range(1, 100):
        point = etree.SubElement(values, f'{{{C}}}pt', idx=str(index))
        etree.SubElement(point, f'{{{C}}}v').text = str(index)
    drawing = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    title = etree.SubElement(root, f'{{{C}}}title')
    etree.SubElement(title, f'{{{drawing}}}t').text = 'Energy consumption in kWh'

    def save():
        parts['word/charts/chart1.xml'] = etree.tostring(root)
        with ZipFile(source, 'w', ZIP_DEFLATED) as archive:
            for name, data in parts.items():
                archive.writestr(name, data)

    save()
    before = next(segment.context for segment in OfficeDocument(source).segments
                  if segment.plain == 'Electricity consumption')
    assert 'Energy consumption in kWh' in before and 'January' in before
    assert len(before) < 3000
    values.find(f'{{{C}}}pt[@idx="50"]/{{{C}}}v').text = '999'
    save()
    after = next(segment.context for segment in OfficeDocument(source).segments
                 if segment.plain == 'Electricity consumption')
    assert before.split(' Full chart context SHA256: ')[0] == after.split(' Full chart context SHA256: ')[0]
    assert before != after


@pytest.mark.parametrize('damage', ['missing_shared_link', 'missing_relationship_id', 'doctype'])
def test_unproved_embedded_book_relationships_never_rewrite_chart_source(tmp_path, damage):
    source = chart_document(tmp_path, shared=True)
    with ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    with ZipFile(io.BytesIO(parts['word/embeddings/book.xlsx'])) as archive:
        book = {name: archive.read(name) for name in archive.namelist()}
    if damage == 'missing_shared_link':
        rels = etree.fromstring(book['xl/_rels/workbook.xml.rels'])
        for node in list(rels):
            if node.get('Type') == R + '/sharedStrings':
                rels.remove(node)
        book['xl/_rels/workbook.xml.rels'] = etree.tostring(rels)
    elif damage == 'missing_relationship_id':
        workbook = etree.fromstring(book['xl/workbook.xml'])
        workbook.find(f'.//{{{S}}}sheet').attrib.pop(f'{{{R}}}id')
        book['xl/workbook.xml'] = etree.tostring(workbook)
        rels = etree.fromstring(book['xl/_rels/workbook.xml.rels'])
        for node in rels:
            if node.get('Type') == R + '/worksheet':
                node.attrib.pop('Id')
        book['xl/_rels/workbook.xml.rels'] = etree.tostring(rels)
    else:
        book['xl/sharedStrings.xml'] = f'<!DOCTYPE sst [<!ENTITY label "private">]><sst xmlns="{S}"><si><t>&label;</t></si></sst>'.encode()
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for name, data in book.items():
            archive.writestr(name, data)
    parts['word/embeddings/book.xlsx'] = buffer.getvalue()
    with ZipFile(source, 'w', ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    document = OfficeDocument(source)
    assert any(issue.code == 'chart_labels_preserved' for issue in document.issues)
    assert not any(segment.plain == 'Electricity consumption' for segment in document.segments)
    output = tmp_path/'copy.docx'
    document.write({segment.id: segment.source for segment in document.segments}, output)
    with ZipFile(output) as archive:
        assert archive.read('word/embeddings/book.xlsx') == parts['word/embeddings/book.xlsx']


@pytest.mark.parametrize('formula,index', [('Sheet!A1:B2', 0), ('[Book]Sheet!A1', 0), ('Sheet!A1', 1), ('Sheet!A0', 0), ('Sheet!XFE1', 0)])
def test_chart_binding_rejects_ambiguous_or_out_of_range_references(formula, index):
    with pytest.raises(UnsupportedChart):
        reference_cell(formula, index)
