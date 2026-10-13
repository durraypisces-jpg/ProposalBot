"""Report generator exports (FR-08): PDF, Word and Excel.

Every export carries the narrative, statistics, chart, data, and the source,
filters, date range and data-as-of details required by FR-10.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

NAVY = "#1F3A5F"
TEAL = "#1A7F8E"
PALETTE = ["#1A7F8E", "#1F3A5F", "#E07A2E", "#7A9E3B", "#8E5BA8", "#C9A227", "#5B6573"]
DOC_ROW_LIMIT = 200


def report_frame(report: dict) -> pd.DataFrame:
    return pd.DataFrame(report["rows"], columns=report["columns"])


ACRONYMS = {"usd": "USD", "pkr": "PKR", "aed": "AED", "sku": "SKU", "id": "ID", "ytd": "YTD", "fx": "FX"}


def _label(name: str) -> str:
    words = [w for w in name.split("_") if w]
    return " ".join(ACRONYMS.get(w.lower(), w.capitalize() if i == 0 else w) for i, w in enumerate(words))


def render_chart_png(report: dict) -> bytes | None:
    chart = report.get("chart") or {}
    frame = report_frame(report)
    x, ys, series = chart.get("x"), [y for y in chart.get("y", []) if y in frame.columns], chart.get("series")
    if chart.get("type") == "table" or frame.empty or x not in frame.columns or not ys:
        return None

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    if series and series in frame.columns:
        data = frame.pivot_table(index=x, columns=series, values=ys[0], aggfunc="sum").sort_index()
        ylabel = _label(ys[0])
    else:
        data = frame.set_index(x)[ys].rename(columns=_label)
        ylabel = _label(ys[0]) if len(ys) == 1 else ""

    kind = chart.get("type")
    if kind == "pie":
        values = frame.set_index(x)[ys[0]]
        ax.pie(values, labels=[str(i) for i in values.index], colors=PALETTE, autopct="%1.0f%%",
               wedgeprops={"linewidth": 1, "edgecolor": "white"}, textprops={"fontsize": 8})
        ax.axis("equal")
    else:
        data.index = [str(i) for i in data.index]
        if kind == "line":
            data.plot(ax=ax, color=PALETTE, linewidth=2, marker="o", markersize=3)
        else:
            data.plot(ax=ax, kind="bar", color=PALETTE, width=0.75)
        ax.set_xlabel(_label(x))
        ax.set_ylabel(ylabel)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y", color="#E3E7EC")
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        if len(data.columns) > 1:
            ax.legend(fontsize=8, frameon=False)
        elif ax.get_legend():
            ax.get_legend().remove()
        plt.setp(ax.get_xticklabels(), rotation=45 if len(data) > 6 else 0, ha="right" if len(data) > 6 else "center", fontsize=8)
    ax.set_title(report["title"], color=NAVY, fontsize=11, loc="left")
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _stat_rows(report: dict) -> list[list[str]]:
    rows = []
    for m in report["statistics"]["measures"]:
        change = "" if m.get("pct_change") is None else f"{m['pct_change']:+.1f}% ({m['previous_period']} to {m['latest_period']})"
        rows.append([_label(m["measure"]), _fmt(m["total"]), _fmt(m["average"]),
                     f"{_fmt(m['minimum'])}{' (' + m['minimum_at'] + ')' if m.get('minimum_at') else ''}",
                     f"{_fmt(m['maximum'])}{' (' + m['maximum_at'] + ')' if m.get('maximum_at') else ''}", change])
    return rows


STAT_HEADER = ["Measure", "Total", "Average", "Minimum", "Maximum", "Period-over-period"]


def _meta_rows(report: dict) -> list[list[str]]:
    source = report["source"]
    dates = " to ".join(d for d in (source.get("date_start"), source.get("date_end")) if d) or "As per filters"
    as_of = ", ".join(f"{d}: {t}" for d, t in source.get("data_as_of", {}).items()) or "n/a"
    rows = [
        ["Request", report["request"]],
        ["Data source", ", ".join(source.get("views", []))],
        ["Filters", source.get("filters", "")],
        ["Date range", dates],
        ["Data as of (UTC)", as_of],
        ["Generated (UTC)", report["created_at"]],
        ["Rows", f"{report['row_count']:,}" + (" (cut to the row limit)" if report.get("truncated") else "")],
    ]
    if report.get("provisional"):
        rows.append(["Status", "Provisional: includes an open fiscal period"])
    return rows


# Excel

def to_excel(report: dict) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        summary = pd.DataFrame(_meta_rows(report) + [["Summary", report.get("narrative", "")]], columns=["Item", "Value"])
        summary.to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(_stat_rows(report), columns=STAT_HEADER).to_excel(writer, sheet_name="Statistics", index=False)
        report_frame(report).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame([[report["sql"]]], columns=["Query"]).to_excel(writer, sheet_name="Query", index=False)
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max(len(str(c.value or "")) for c in column)
                sheet.column_dimensions[column[0].column_letter].width = min(max(12, width + 2), 80)
    return buffer.getvalue()


# Word

def to_word(report: dict) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    title = doc.add_heading(report["title"], level=1)
    for run in title.runs:
        run.font.color.rgb = RGBColor.from_string(NAVY[1:])
    if report.get("provisional"):
        doc.add_paragraph("Provisional: these figures include an open fiscal period and may change.").runs[0].italic = True
    if report.get("narrative"):
        doc.add_paragraph(report["narrative"])

    png = render_chart_png(report)
    if png:
        doc.add_picture(io.BytesIO(png), width=Inches(6.2))

    def table(header: list[str], rows: list[list[str]]):
        t = doc.add_table(rows=1, cols=len(header))
        t.style = "Light Grid Accent 1"
        for cell, text in zip(t.rows[0].cells, header):
            cell.text = text
        for row in rows:
            cells = t.add_row().cells
            for cell, text in zip(cells, row):
                cell.text = text

    if report["statistics"]["measures"]:
        doc.add_heading("Key statistics", level=2)
        table(STAT_HEADER, _stat_rows(report))
    doc.add_heading("Data", level=2)
    frame = report_frame(report)
    table([_label(c) for c in frame.columns], [[_fmt(v) for v in row] for row in frame.head(DOC_ROW_LIMIT).itertuples(index=False)])
    if len(frame) > DOC_ROW_LIMIT:
        doc.add_paragraph(f"First {DOC_ROW_LIMIT} of {len(frame):,} rows shown; the Excel export has all rows.")
    doc.add_heading("Source and filters", level=2)
    table(["Item", "Value"], _meta_rows(report))
    doc.add_heading("Query used", level=2)
    doc.add_paragraph(report["sql"]).runs[0].font.name = "Consolas"

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# PDF

def to_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=colors.HexColor(NAVY), fontSize=16)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor(NAVY), fontSize=12)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5)
    code = ParagraphStyle("code", parent=small, fontName="Courier")

    def table(header, rows, widths=None):
        data = [[Paragraph(f"<b>{escape(h)}</b>", small) for h in header]]
        data += [[Paragraph(escape(str(v)), small) for v in row] for row in rows]
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3F1F3")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D1DA")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    story = [Paragraph(escape(report["title"]), h1)]
    if report.get("provisional"):
        story.append(Paragraph("<i>Provisional: these figures include an open fiscal period and may change.</i>", body))
    if report.get("narrative"):
        story.append(Paragraph(escape(report["narrative"]), body))
    png = render_chart_png(report)
    if png:
        story += [Spacer(1, 4 * mm), Image(io.BytesIO(png), width=170 * mm, height=85 * mm)]
    if report["statistics"]["measures"]:
        story += [Paragraph("Key statistics", h2), table(STAT_HEADER, _stat_rows(report))]
    frame = report_frame(report)
    story += [Paragraph("Data", h2),
              table([_label(c) for c in frame.columns], [[_fmt(v) for v in row] for row in frame.head(DOC_ROW_LIMIT).itertuples(index=False)])]
    if len(frame) > DOC_ROW_LIMIT:
        story.append(Paragraph(f"First {DOC_ROW_LIMIT} of {len(frame):,} rows shown; the Excel export has all rows.", small))
    story += [Paragraph("Source and filters", h2), table(["Item", "Value"], _meta_rows(report), widths=[40 * mm, 130 * mm]),
              Paragraph("Query used", h2), Paragraph(escape(report["sql"]).replace("\n", "<br/>"), code)]

    buffer = io.BytesIO()

    def footer(canvas, doc_):
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#6B7684"))
        canvas.drawString(20 * mm, 10 * mm, "AI Reporting Assistant - every figure comes from the client's own data")
        canvas.drawRightString(190 * mm, 10 * mm, f"Page {doc_.page}")

    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=report["title"]).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


EXPORTERS = {
    "pdf": (to_pdf, "application/pdf"),
    "docx": (to_word, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (to_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}
