# Installed ABBYY OCR on Windows

Forma can use an existing ABBYY FineReader PDF installation to prepare a scanned PDF as DOCX. A lightweight Windows build can omit Forma's local OCR/model bundle when users already have a suitable OCR workflow. ABBYY is installed and licensed separately; detecting its executable does not identify the license's automation features.

## Choose the appropriate workflow

| Installation | Forma workflow |
|---|---|
| Ordinary FineReader PDF 15/16 | Launch the OCR Editor, open the PDF, review and save DOCX, then import that DOCX explicitly |
| FineReader with a confirmed Extended CLI-enabled license | Ask ABBYY to save DOCX through its documented `/out` command, validate the returned file, then review it |
| No compatible ABBYY installation | Use Forma's OCR worker from a complete package or a separately installed worker |

ABBYY documents regular CLI conversion and opening the result in an external application. Automatic saving to a specified file requires a separate **Extended CLI-enabled license**. The Standard/Corporate name or presence of `FineReaderOCR.exe` is insufficient to enable that feature. See [regular CLI](https://help.abbyy.com/en-us/finereader/16/user_guide/commandline_convert/) and [ABBYY's explanation of the license restriction](https://support.abbyy.com/hc/en-us/articles/4409323261331--This-feature-is-not-available-for-your-license-message-appears-when-converting-in-FineReader-PDF-via-command-line).

ABBYY's current [CLI availability page](https://pdf.abbyy.com/how-to/convert-compare-pdfs-with-cli/) describes separately licensed Extended CLI access, page limits and the requirement for a logged-in local user. Check the user's actual license rather than treating a detected installation as a server OCR service.

## Ordinary installation: review and import

1. Select the source PDF and its language in Forma, choose the installed ABBYY workflow and click **Launch ABBYY**.
2. In the ABBYY OCR Editor, open the selected PDF and set the appropriate recognition language. Forma only starts the editor; it does not claim that a conversion has completed or change ABBYY's remembered settings.
3. Check text, tables, reading order and every source page. Save the result as **DOCX**.
4. Return to Forma, select the saved DOCX and confirm that you checked it against the PDF. Forma validates and copies it into the results folder, then it can be translated through the ordinary DOCX workflow.

This path does not depend on Microsoft Word. The alternative regular CLI `/send MSWord` documented by ABBYY opens an external Word application, so Forma does not make it the default. The manual editor workflow follows ABBYY's documented [OCR Editor controls](https://help.abbyy.com/en-us/finereader/16/user_guide/run_ocr/) and [editable-document export](https://help.abbyy.com/en-us/finereader/16/user_guide/savetoedit/).

Starting ABBYY leaves the PDF in Forma's queue. Importing a DOCX is a separate user decision. Neither action approves translation-memory entries or starts a translation/API request.

## Extended CLI: explicit setup

Select Extended CLI only after confirming that the installed ABBYY license supports automatic file saving. Forma uses this documented argument structure with separate arguments, never a shell command:

```text
FineReaderOCR.exe INPUT.pdf /lang Ukrainian /out TEMP_RESULT.docx /report TEMP_REPORT.txt
```

The destination is a new private temporary file. Forma checks process completion, the source PDF hash and the exact returned DOCX bytes before creating a uniquely named result. A missing result, nonzero exit, invalid/empty DOCX, cancellation or timeout prevents publication. Provider logs are not shown as document text or copied into release packages. See ABBYY's [saving command](https://help.abbyy.com/en-us/finereader/16/user_guide/commandline_save/) and [FineReader 15 equivalent](https://help.abbyy.com/en-us/finereader/15/user_guide/commandline_save/).

The automatic export runs with its own writable temporary working directory and an XML report path in that directory. It does not use the application installation directory or the source PDF's folder for job files, and Forma does not elevate its whole interface merely because ABBYY is installed. The original PDF is passed by its absolute path and remains unchanged.

Do not use this operation alongside unsaved work in an existing ABBYY session. If ABBYY forwards a command to an existing editor and returns before producing the file, Forma fails rather than guessing that processing is complete. Cancel/timeout terminates only the process Forma launched; it never kills every ABBYY process or an unrelated user's editor. A session that ABBYY independently reused may need to be stopped in ABBYY itself. Use the manual workflow if this installation cannot provide a completed CLI result.

## Output checks and limits

The original PDF and the selected DOCX are retained unchanged. The results folder receives a new `*.abbyy.docx` and an accompanying `*.ocr.json` with the source/output SHA256, source language, provider and workflow. Existing files are never overwritten.

ABBYY does not supply Forma's page-by-page OCR manifest. The sidecar therefore records `coverage: "not_verified"` and the **source** PDF page count as `source_page_count`; it does not invent processed pages or an OCR confidence score. A valid DOCX can still omit pages or contain recognition mistakes. The user must compare all pages before translation. A manually selected PDF/DOCX pair is a user-selected association, not proof that ABBYY generated that DOCX from that PDF.

Automatic conversion/import currently accepts the same four source languages as Forma OCR: English, Ukrainian, Polish and German. Interface-language choices are separate. Inputs are bounded to 200 MB and 200 source pages; output DOCX files are bounded to 200 MB. Validation preserves the original DOCX bytes, including tables and layout, instead of reconstructing them a second time.

Automatic discovery is independent of whether Windows uses `C:` for Program Files. For example, `E:\Program Files\ABBYY FineReader 16\FineReaderOCR.exe` can be detected without entering its path. Forma checks, in order:

1. The OCR Editor's Windows App Paths registration, plus the editor next to known FineReader/PDF launcher registrations.
2. ABBYY FineReader installation metadata (`InstallLocation` or a known launcher `DisplayIcon`) in both user/machine and 32/64-bit registry views.
3. The environment's Program Files directories, then only the known FineReader 15/16 folder names under Program Files/Program Files (x86) on fixed local drives.

Registry enumeration is bounded. No recursive disk search runs, and fallback discovery excludes removable and mapped network drives. Registry command strings are not executed. This uses the documented Windows [App Paths](https://learn.microsoft.com/en-us/windows/win32/shell/app-registration), [installer metadata](https://learn.microsoft.com/en-us/windows/win32/msi/uninstall-registry-key) and [drive-type APIs](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew).

A custom installation can also be selected explicitly. Discovery belongs in a background task; reading a QML property does not rescan the registry or disk. A stale configured ABBYY path does not make discovery pick a different ABBYY installation. Explicit ABBYY modes report the missing program; Automatic mode follows the provider order shown in settings. Detecting the executable does not prove Extended CLI licensing.

## Other ABBYY products

Corporate Hot Folder can run user-configured watched-folder tasks, but those tasks have their own input/output, scheduling, page and deletion settings. Forma does not silently create or modify them. See [ABBYY Hot Folder](https://help.abbyy.com/en-us/finereader/16/user_guide/hotfolder/).

FineReader Engine is a separate OCR SDK with its own development/runtime licensing; it is not an automation API granted by a consumer FineReader installation. No SDK/COM integration or ABBYY redistribution is included here. See [FineReader Engine licensing](https://help.abbyy.com/en-us/finereaderengine/12/user_guide/licensing_licensing/).

The adapter is verified offline with a fake local process that exercises the documented arguments, cancellation, output validation, hashes and atomic publication. Native integration still needs testing with the user's actual FineReader version and eligible license; fake-provider tests do not establish ABBYY recognition quality.

Product documentation checked on 2026-11-11.
