from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_docx(path: Path):
    document = Document()
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(11)
    section = document.sections[0]
    section.header.paragraphs[0].text = "NORTHLINE / Engineering documentation"
    document.add_heading("Heat meter commissioning", 0)
    document.add_paragraph("Technical brief · Revision 2.1 · Device HM-200")
    document.add_heading("Operating conditions", 1)
    paragraph = document.add_paragraph("The ")
    paragraph.add_run("maximum operating pressure").bold = True
    paragraph.add_run(" must not exceed 16 bar. Maintain a flow rate of 2.5 m³/h.")
    paragraph = document.add_paragraph("Do not disconnect the sensor ")
    run = paragraph.add_run("while the system is pressurized")
    run.italic = True
    run.font.color.rgb = RGBColor.from_string("9B4933")
    paragraph.add_run(". Refer to section 3 before commissioning.")
    table = document.add_table(rows=1, cols=3)
    table.style = "Light Shading Accent 1"
    for cell, value in zip(table.rows[0].cells, ["Parameter", "Value", "Notes"], strict=True):
        cell.text = value
    for row in [
        ["Nominal flow rate", "2.5 m³/h", "Observe the flow direction"],
        ["Temperature range", "5–95 °C", "Avoid direct sunlight"],
        ["Measurement uncertainty", "±2%", "At nominal operating conditions"],
    ]:
        for cell, value in zip(table.add_row().cells, row, strict=True):
            cell.text = value
    document.add_heading("Before commissioning", 1)
    for instruction in [
        "Check all electrical connections.",
        "Flush the pipework before installation.",
        "Record the serial number and initial reading.",
    ]:
        document.add_paragraph(instruction, style="List Bullet")
    paragraph = document.add_paragraph("Documentation: ")
    link = OxmlElement("w:hyperlink")
    relation = document.part.relate_to(
        "https://example.com/manual/HM-200",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link.set(qn("r:id"), relation)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Installation manual"
    run.append(text)
    link.append(run)
    paragraph._p.append(link)
    paragraph = section.footer.paragraphs[0]
    paragraph.add_run("Page ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    document.save(path)


def make_xlsx(path: Path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Commissioning"
    sheet.append(["Heat meter commissioning checklist"])
    sheet.merge_cells("A1:D1")
    sheet["A1"].font = Font(name="Calibri", size=18, bold=True, color="22694F")
    sheet.append(["Item", "Quantity", "Unit price", "Total"])
    for row in [
        ["Temperature sensor", 2, 125.5, "=B3*C3"],
        ["Flow sensor", 1, 360, "=B4*C4"],
        ["Installation kit", 1, 45, "=B5*C5"],
    ]:
        sheet.append(row)
    sheet.append(["Total cost", None, None, "=SUM(D3:D5)"])
    sheet.append(["Note: do not disconnect the sensor under pressure."])
    sheet.merge_cells("A7:D7")
    for cell in sheet[2]:
        cell.fill = PatternFill("solid", fgColor="E4EFE6")
        cell.font = Font(bold=True, color="18382F")
    for row in sheet.iter_rows(min_row=3, max_row=6, min_col=3, max_col=4):
        for cell in row:
            cell.number_format = '#,##0.00 "EUR"'
    sheet.column_dimensions["A"].width = 44
    for column in "BCD":
        sheet.column_dimensions[column].width = 18
    sheet.freeze_panes = "A3"
    sheet["A7"].alignment = Alignment(wrap_text=True)
    sheet.row_dimensions[7].height = 34
    chart = BarChart()
    chart.title = "Component costs"
    chart.add_data(Reference(sheet, min_col=3, min_row=2, max_row=5), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=3, max_row=5))
    sheet.add_chart(chart, "F2")
    other = workbook.create_sheet("Instructions")
    other.append(["Step", "Instruction"])
    other.append([1, "Check the flow direction before installation."])
    other.append([2, "Record the serial number HM-200."])
    other.column_dimensions["B"].width = 65
    workbook.save(path)


def make_pdf(path: Path):
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    pdf.setFillColor(HexColor("#22694F"))
    pdf.rect(0, height - 135, width, 135, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 25)
    pdf.drawString(45, height - 65, "Heat meter commissioning")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(45, height - 92, "Technical brief / Device HM-200 / Revision 2.1")
    pdf.setFillColor(HexColor("#18382F"))
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(45, height - 180, "Operating conditions")
    pdf.setFont("Helvetica", 11)
    lines = [
        "The maximum operating pressure must not exceed 16 bar.",
        "Maintain a flow rate of 2.5 cubic metres per hour.",
        "Do not disconnect the sensor while the system is pressurized.",
        "Measurement uncertainty: 2% at nominal operating conditions.",
        "Check all electrical connections before commissioning.",
        "Record the serial number and the initial reading.",
    ]
    for i, text in enumerate(lines):
        pdf.drawString(45, height - 215 - i * 27, text)
    pdf.setStrokeColor(HexColor("#CCD8CC"))
    pdf.line(45, 65, width - 45, 65)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(45, 46, "NORTHLINE / Engineering documentation / Page 1")
    pdf.save()


def main():
    folder = Path(__file__).resolve().parents[1] / "examples"
    folder.mkdir(exist_ok=True)
    make_docx(folder / "technical-brief.docx")
    make_xlsx(folder / "commissioning.xlsx")
    make_pdf(folder / "technical-brief.pdf")
    print(f"Created DOCX, XLSX and PDF in {folder}")


if __name__ == "__main__":
    main()
