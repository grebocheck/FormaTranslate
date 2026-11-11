from __future__ import annotations

import hashlib
import json
import ntpath
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from docx import Document
from pypdf import PdfWriter

from forma.core.models import Cancelled, FormaError
from forma.documents import abbyy
from forma.providers.client import JobControl


@pytest.fixture
def source_pdf(tmp_path):
    source = tmp_path / "Community project.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    writer.write(source)
    return source


@pytest.fixture
def result_docx(tmp_path):
    result = tmp_path / "Reviewed recognition.docx"
    document = Document()
    document.add_paragraph("Громада 12345 · загальна вартість 250000")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Спільний заголовок"
    table.cell(1, 0).text = "12345"
    table.cell(1, 1).text = "250000"
    document.save(result)
    return result


def test_detection_respects_explicit_path_without_guessing_or_falling_back(tmp_path, monkeypatch):
    executable = tmp_path / "FineReaderOCR.exe"
    executable.touch()
    monkeypatch.setattr(abbyy, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(abbyy, "_registered_paths", lambda: pytest.fail("Explicit paths must not fall back"))
    assert abbyy.find_abbyy(str(executable)) == str(executable.resolve())
    assert abbyy.find_abbyy(str(tmp_path / "missing/FineReaderOCR.exe")) == ""
    wrong = tmp_path / "FineReader.exe"
    wrong.touch()
    assert abbyy.find_abbyy(str(wrong)) == ""


def test_discovery_checks_only_fixed_registry_locations_and_program_folders(tmp_path, monkeypatch):
    calls = []
    registered = tmp_path / "Custom ABBYY/FineReaderOCR.exe"
    registered.parent.mkdir()
    registered.touch()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def open_key(hive, path, reserved, access):
        calls.append((hive, path, reserved, access))
        if path.endswith("Uninstall"):
            raise FileNotFoundError(path)
        return Key()

    fake_registry = SimpleNamespace(
        HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2,
        KEY_WOW64_64KEY=16, KEY_WOW64_32KEY=32, KEY_READ=64,
        REG_SZ=1, REG_EXPAND_SZ=2, OpenKey=open_key,
        QueryValueEx=lambda *_: (f'"{registered}"', 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_registry)
    monkeypatch.setattr(abbyy, "sys", SimpleNamespace(platform="win32"))
    assert abbyy.find_abbyy() == str(registered.resolve())
    assert len(calls) == 12
    assert {call[1].rsplit("\\", 1)[-1] for call in calls} == {
        "FineReaderOCR.exe", "FineReader.exe", "FineReaderPDF.exe",
    }
    registered.unlink()
    standard = tmp_path / "Programs/ABBYY FineReader 16/FineReaderOCR.exe"
    standard.parent.mkdir(parents=True)
    standard.touch()
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Programs"))
    assert abbyy.find_abbyy() == str(standard.resolve())


class FakeRegistry:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 1, 256, 512
    REG_SZ, REG_EXPAND_SZ = 1, 2

    class Key:
        def __init__(self, hive, view, path):
            self.hive, self.view, self.path = hive, view, path

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def __init__(self, entries):
        self.entries, self.opened, self.enumerated = entries, [], []

    def OpenKey(self, root, path, reserved, access):
        hive = root.hive if isinstance(root, self.Key) else root
        full = root.path + "\\" + path if isinstance(root, self.Key) else path
        view = access & (self.KEY_WOW64_64KEY | self.KEY_WOW64_32KEY)
        self.opened.append((hive, view, full))
        if (hive, view, full) not in self.entries and not any(
            h == hive and v == view and p.startswith(full + "\\") for h, v, p in self.entries
        ):
            raise FileNotFoundError(full)
        return self.Key(hive, view, full)

    def QueryValueEx(self, key, name):
        try:
            return self.entries[(key.hive, key.view, key.path)][name]
        except KeyError:
            raise FileNotFoundError(name) from None

    def EnumKey(self, key, index):
        self.enumerated.append((key.hive, key.view, key.path, index))
        children = list(dict.fromkeys(
            p[len(key.path) + 1:].split("\\")[0]
            for h, v, p in self.entries
            if h == key.hive and v == key.view and p.startswith(key.path + "\\")
        ))
        if index >= len(children):
            raise OSError("No more keys")
        return children[index]


def windows_discovery(monkeypatch, registry, expected):
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(abbyy, "sys", SimpleNamespace(platform="win32"))
    probes = []

    def existing_editor(value):
        probes.append(value)
        return expected if ntpath.normcase(value) == ntpath.normcase(expected) else ""

    monkeypatch.setattr(abbyy, "_executable_at", existing_editor)
    return probes


def test_app_paths_launcher_finds_ocr_editor_on_another_drive(monkeypatch):
    expected = r"E:\Program Files\ABBYY FineReader 16\FineReaderOCR.exe"
    launcher = r"E:\Program Files\ABBYY FineReader 16\FineReader.exe"
    registry = FakeRegistry({
        (2, 512, abbyy._APP_PATHS + r"\FineReader.exe"): {"": (f'"{launcher}"', 1)},
    })
    probes = windows_discovery(monkeypatch, registry, expected)
    monkeypatch.setattr(abbyy, "_uninstall_paths", lambda: pytest.fail("App Paths already resolves the editor"))
    assert abbyy.find_abbyy() == expected
    assert probes == [expected]


@pytest.mark.parametrize(("hive", "view"), [(1, 256), (1, 512), (2, 256), (2, 512)])
@pytest.mark.parametrize("location_kind", ["InstallLocation", "DisplayIcon"])
def test_uninstall_metadata_finds_custom_unicode_install_in_every_registry_view(
    monkeypatch, hive, view, location_kind,
):
    folder = r"E:\Програми громади\ABBYY FineReader 16"
    expected = folder + r"\FineReaderOCR.exe"
    data = {"DisplayName": ("FineReader PDF 16", 1), "Publisher": ("ABBYY Europe GmbH", 1)}
    if location_kind == "InstallLocation":
        monkeypatch.setenv("ABBY_TEST_INSTALL", folder)
        data[location_kind] = ("$ABBY_TEST_INSTALL", 2)
    else:
        data[location_kind] = (f'"{folder}\\FineReaderPDF.exe",-1', 1)
    registry = FakeRegistry({(hive, view, abbyy._UNINSTALL_PATH + r"\{ABBYY-PRODUCT}"): data})
    probes = windows_discovery(monkeypatch, registry, expected)
    monkeypatch.setattr(abbyy, "_program_paths", lambda: pytest.fail("Registered custom location must win"))
    assert abbyy.find_abbyy() == expected
    assert probes == [expected]
    assert (hive, view, abbyy._UNINSTALL_PATH + r"\{ABBYY-PRODUCT}") in registry.opened


def test_known_folder_on_fixed_e_drive_is_found_when_programfiles_points_to_c(monkeypatch):
    expected = r"E:\Program Files\ABBYY FineReader 16\FineReaderOCR.exe"
    registry = FakeRegistry({})
    probes = windows_discovery(monkeypatch, registry, expected)
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setattr(abbyy, "_fixed_drive_roots", lambda: ["C:\\", "E:\\"])
    assert abbyy.find_abbyy() == expected
    assert len(probes) <= 16
    assert len({ntpath.normcase(p) for p in probes}) == len(probes)
    assert all("ABBYY FineReader" in p and p.endswith("FineReaderOCR.exe") for p in probes)


def test_drive_enumeration_excludes_network_and_removable_volumes(monkeypatch):
    import ctypes

    checked = []

    def drive_type(root):
        checked.append(root)
        return {"C:\\": 3, "E:\\": 3, "F:\\": 2, "Z:\\": 4}[root]

    kernel = SimpleNamespace(
        GetLogicalDrives=lambda: sum(1 << (ord(letter) - ord("A")) for letter in "CEFZ"),
        GetDriveTypeW=drive_type,
    )
    monkeypatch.setattr(abbyy, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    assert abbyy._fixed_drive_roots() == ["C:\\", "E:\\"]
    assert checked == ["C:\\", "E:\\", "F:\\", "Z:\\"]


def test_unrelated_uninstall_records_and_registry_commands_are_not_candidates(monkeypatch):
    folder = r"E:\Not ABBYY"
    records = {
        (2, 256, abbyy._UNINSTALL_PATH + r"\Other"): {
            "DisplayName": ("FineReader lookalike", 1), "Publisher": ("Other vendor", 1),
            "InstallLocation": (folder, 1),
        },
        (2, 256, abbyy._UNINSTALL_PATH + r"\ABBYY"): {
            "DisplayName": ("ABBYY FineReader PDF 16", 1),
            "DisplayIcon": ('"E:\\ABBYY\\FineReader.exe" /out result.docx', 1),
        },
    }
    registry = FakeRegistry(records)
    windows_discovery(monkeypatch, registry, folder + r"\FineReaderOCR.exe")
    assert abbyy._uninstall_paths() == []
    assert abbyy._registered_editor(r"E:\ABBYY\Setup.exe,0") == ""
    assert abbyy._registered_editor(r"FineReader.exe") == ""


def test_uninstall_enumeration_is_bounded(monkeypatch):
    records = {
        (2, 256, abbyy._UNINSTALL_PATH + f"\\Other{index}"): {"DisplayName": ("Unrelated", 1)}
        for index in range(5)
    }
    registry = FakeRegistry(records)
    windows_discovery(monkeypatch, registry, r"E:\Missing\FineReaderOCR.exe")
    monkeypatch.setattr(abbyy, "_MAX_UNINSTALL_ENTRIES", 2)
    assert abbyy._uninstall_paths() == []
    assert [row[-1] for row in registry.enumerated] == [0, 1]


def test_non_windows_automatic_detection_does_not_probe_registry_or_drives(monkeypatch):
    monkeypatch.setattr(abbyy, "sys", SimpleNamespace(platform="linux"))
    for name in ("_registered_paths", "_uninstall_paths", "_program_paths", "_fixed_drive_paths"):
        monkeypatch.setattr(abbyy, name, lambda: pytest.fail("Non-Windows must not probe"))
    assert abbyy.find_abbyy() == ""


def test_manual_launch_is_editor_only_and_never_claims_automatic_export(source_pdf, tmp_path, monkeypatch):
    executable = tmp_path / "FineReaderOCR.exe"
    executable.touch()
    waited = threading.Event()
    calls = []

    def launch(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(wait=waited.set)

    monkeypatch.setattr(abbyy, "start_worker", launch)
    monkeypatch.setenv("OPENAI_API_KEY", "private-fake-key")
    abbyy.launch_abbyy_handoff(source_pdf, "uk", str(executable))
    assert waited.wait(1)
    arguments, options = calls[0]
    assert arguments == [str(executable.resolve())]
    assert "/out" not in arguments and "/send" not in arguments
    assert "OPENAI_API_KEY" not in options["env"]
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL


def test_manual_import_preserves_exact_reviewed_bytes_and_marks_unverified_coverage(source_pdf, result_docx, tmp_path):
    source_before = source_pdf.read_bytes()
    original = result_docx.read_bytes()
    output = abbyy.import_abbyy_result(source_pdf, result_docx, tmp_path / "out", "uk", JobControl())
    second = abbyy.import_abbyy_result(source_pdf, result_docx, tmp_path / "out", "uk", JobControl())
    assert output != second and output.read_bytes() == second.read_bytes() == original
    assert source_pdf.read_bytes() == source_before and result_docx.read_bytes() == original
    metadata = json.loads(output.with_suffix(".ocr.json").read_text())
    assert metadata["provider"] == "abbyy" and metadata["workflow"] == "manual"
    assert metadata["coverage"] == "not_verified" and metadata["source_page_count"] == 2
    assert "pages" not in metadata and "page_count" not in metadata
    assert metadata["source_sha256"] == hashlib.sha256(source_before).hexdigest()
    assert metadata["output_sha256"] == hashlib.sha256(original).hexdigest()
    table = Document(output).tables[0]
    assert table.cell(0, 0)._tc is table.cell(0, 1)._tc


@pytest.mark.parametrize("mode", ["empty", "invalid", "wrong_extension", "symlink"])
def test_manual_import_rejects_unusable_or_indirect_documents(source_pdf, result_docx, tmp_path, mode):
    document = tmp_path / "result.docx"
    if mode == "empty":
        Document().save(document)
    elif mode == "invalid":
        document.write_bytes(b"Not a ZIP document")
    elif mode == "wrong_extension":
        document = result_docx.with_suffix(".pdf")
        document.write_bytes(result_docx.read_bytes())
    else:
        try:
            document.symlink_to(result_docx)
        except OSError:
            pytest.skip("Creating symbolic links requires OS permission")
    with pytest.raises(FormaError, match="DOCX з ABBYY"):
        abbyy.import_abbyy_result(source_pdf, document, tmp_path / "out", "uk", JobControl())
    assert not list((tmp_path / "out").glob("*"))


def test_changed_pdf_never_leaves_a_result(source_pdf, result_docx, tmp_path, monkeypatch):
    recognized = abbyy._recognized_bytes

    def changing_document(document):
        data = recognized(document)
        source_pdf.write_bytes(b"changed PDF")
        return data

    monkeypatch.setattr(abbyy, "_recognized_bytes", changing_document)
    with pytest.raises(FormaError, match="PDF змінився"):
        abbyy.import_abbyy_result(source_pdf, result_docx, tmp_path / "out", "uk", JobControl())
    assert not list((tmp_path / "out").glob("*"))


def test_cancellation_after_sidecar_creation_rolls_back_pair(source_pdf, result_docx, tmp_path, monkeypatch):
    control = JobControl()
    write = abbyy.atomic_bytes

    def cancel_after_metadata(path, data, **kwargs):
        write(path, data, **kwargs)
        if str(path).endswith(".ocr.json"):
            control.cancelled.set()

    monkeypatch.setattr(abbyy, "atomic_bytes", cancel_after_metadata)
    with pytest.raises(Cancelled):
        abbyy.import_abbyy_result(source_pdf, result_docx, tmp_path / "out", "uk", control)
    assert not list((tmp_path / "out").glob("*"))


@pytest.fixture
def fake_abbyy(tmp_path, monkeypatch):
    script = tmp_path / "offline_abbyy.py"
    script.write_text('''import os, sys, time
from pathlib import Path
from docx import Document
a = sys.argv
assert a[2] == '/lang' and a[3] == 'Ukrainian'
assert not any(os.environ.get(k) for k in ('OPENAI_API_KEY','GEMINI_API_KEY','GOOGLE_API_KEY','ANTHROPIC_API_KEY'))
source = Path(a[1]); output = Path(a[a.index('/out')+1]); report = Path(a[a.index('/report')+1])
assert Path.cwd().resolve() == output.parent.resolve() == report.parent.resolve()
assert report.suffix == '.xml'
mode = os.environ.get('FORMA_TEST_ABBYY_MODE', '')
if mode == 'wait': time.sleep(30)
if mode == 'fail':
 report.write_text('Private recognized text and licensing diagnostics')
 sys.exit(7)
if mode == 'missing': sys.exit(0)
d = Document()
if mode != 'empty': d.add_paragraph('Громада 12345 / загальна вартість 250000')
d.save(output)
if mode == 'invalid': output.write_bytes(b'invalid DOCX')
if mode == 'change': source.write_bytes(b'changed source PDF')
''', encoding="utf-8")
    processes = []

    def launch(arguments, **kwargs):
        assert arguments[0] == "fake-abbyy"
        assert kwargs.get("shell", False) is False
        process = subprocess.Popen([sys.executable, str(script), *arguments[1:]], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(abbyy, "find_abbyy", lambda _: "fake-abbyy")
    monkeypatch.setattr(abbyy, "start_worker", launch)
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "private-fake-key")
    return processes


def convert(source, folder, control=None, progress=lambda *_: None, **options):
    return abbyy.convert_with_abbyy(source, folder, "uk", "", control or JobControl(), progress,
                                    extended_cli_confirmed=True, **options)


def test_extended_cli_requires_explicit_license_confirmation_before_launch(source_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(abbyy, "start_worker", lambda *_a, **_kw: pytest.fail("No license confirmation"))
    with pytest.raises(FormaError, match="Extended CLI"):
        abbyy.convert_with_abbyy(source_pdf, tmp_path / "out", "uk", "", JobControl(), lambda *_: None)


def test_extended_cli_runs_documented_protocol_and_still_marks_unknown_coverage(source_pdf, tmp_path, fake_abbyy):
    progress = []
    output = convert(source_pdf, tmp_path / "out", progress=lambda *event: progress.append(event))
    assert "250000" in Document(output).paragraphs[0].text
    metadata = json.loads(output.with_suffix(".ocr.json").read_text())
    assert metadata["workflow"] == "extended_cli" and metadata["coverage"] == "not_verified"
    assert progress[-1][0] == 100
    assert all(process.poll() == 0 for process in fake_abbyy)


def test_extended_cli_owns_a_writable_job_directory_and_xml_report(
    source_pdf, tmp_path, fake_abbyy, monkeypatch,
):
    original = source_pdf.read_bytes()
    launch = abbyy.start_worker
    jobs = []

    def inspect_job(arguments, **options):
        from pathlib import Path

        output = Path(arguments[arguments.index("/out") + 1])
        report = Path(arguments[arguments.index("/report") + 1])
        cwd = Path(options["cwd"])
        assert cwd.is_dir() and cwd == output.parent == report.parent
        assert report.name == "conversion-report.xml"
        assert cwd != source_pdf.parent and cwd != tmp_path / "out"
        assert arguments[1] == str(source_pdf.resolve())
        jobs.append(cwd)
        return launch(arguments, **options)

    monkeypatch.setattr(abbyy, "start_worker", inspect_job)
    output = convert(source_pdf, tmp_path / "out")
    assert output.is_file() and source_pdf.read_bytes() == original
    assert len(jobs) == 1 and not jobs[0].exists()


@pytest.mark.parametrize("mode", ["fail", "missing", "empty", "invalid", "change"])
def test_failed_or_unusable_extended_conversion_never_publishes_or_leaks_diagnostics(
    source_pdf, tmp_path, fake_abbyy, monkeypatch, mode,
):
    monkeypatch.setenv("FORMA_TEST_ABBYY_MODE", mode)
    with pytest.raises(FormaError) as error:
        convert(source_pdf, tmp_path / "out")
    assert "Private recognized" not in str(error.value) and "private-fake-key" not in str(error.value)
    assert not list((tmp_path / "out").glob("*"))


@pytest.mark.parametrize("cancel", [False, True])
def test_cancel_and_timeout_stop_only_owned_process_and_publish_nothing(
    source_pdf, tmp_path, fake_abbyy, monkeypatch, cancel,
):
    monkeypatch.setenv("FORMA_TEST_ABBYY_MODE", "wait")
    control = JobControl()
    timer = threading.Timer(0.15, control.cancelled.set) if cancel else None
    if timer:
        timer.start()
    try:
        with pytest.raises(Cancelled if cancel else FormaError):
            convert(source_pdf, tmp_path / "out", control, timeout_seconds=5 if cancel else 0.15)
    finally:
        if timer:
            timer.join()
    assert fake_abbyy and all(process.poll() is not None for process in fake_abbyy)
    assert not list((tmp_path / "out").glob("*"))


@pytest.mark.parametrize("timeout", [False, 0, -1, float("inf"), float("nan"), "30"])
def test_invalid_deadlines_do_not_start_any_application(source_pdf, tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        convert(source_pdf, tmp_path / "out", timeout_seconds=timeout)
