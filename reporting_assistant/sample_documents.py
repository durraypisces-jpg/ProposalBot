"""Sample purchase documents for demos and tests, made from the sample ERP's
own suppliers and items, laid out the way DemoExtractor reads them."""

from __future__ import annotations

import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _products(reporting_db: Path, n: int = 3) -> list[tuple]:
    con = sqlite3.connect(f"file:{reporting_db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT sku, product_name, unit_cost, supplier, category FROM src_products "
                           "ORDER BY sku LIMIT ?", (n,)).fetchall()
    finally:
        con.close()


def sample_lines(doc_type: str, reporting_db: Path, number: str = "PO-2026-0412", mistake: bool = False) -> list[str]:
    products = _products(reporting_db)
    today = date.today()
    if doc_type == "purchase_order":
        lines = [f"PO Number: {number}", f"PO Date: {today.isoformat()}", f"Supplier: {products[0][3]}",
                 "Currency: USD", f"Delivery Date: {(today + timedelta(days=14)).isoformat()}",
                 "Payment Terms: Net 30", "", "Item Code | Description | Qty | Unit | Unit Price | Tax | Line Total"]
        total = 0.0
        for i, (sku, name, cost, _supplier, _group) in enumerate(products, 1):
            qty = 10 * i
            tax = round(qty * cost * 0.05, 2)
            line_total = round(qty * cost + tax, 2)
            total += line_total
            lines.append(f"{sku} | {name} | {qty} | EA | {cost:.2f} | {tax:.2f} | {line_total:.2f}")
        if mistake:
            total += 100  # the order total does not add up
        lines += ["", f"Total: {total:.2f}"]
        return lines
    if doc_type == "purchase_request":
        lines = ["Requester: Omar Farooq", "Department: Operations", f"Request Date: {today.isoformat()}",
                 f"Required By: {(today + timedelta(days=30)).isoformat()}",
                 "Justification: Restock for the next quarter", "", "Item Code | Description | Qty | Unit"]
        lines += [f"{sku} | {name} | {5 * i} | EA" for i, (sku, name, *_rest) in enumerate(products, 1)]
        return lines
    if doc_type == "item_creation":
        group = products[0][4]
        return ["Item Name: Industrial Shelf Unit 2m", "Description: Steel shelving, 5 levels",
                "Unit of Measure: EA", f"Item Group: {group}", "Standard Cost: 145.00",
                f"Supplier: {products[0][3]}"]
    raise ValueError(f"Unknown document type {doc_type!r}")


def sample_pdf(lines: list[str], title: str = "Document") -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    y = A4[1] - 60
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, title)
    pdf.setFont("Helvetica", 9)
    for line in lines:
        y -= 16
        pdf.drawString(50, y, line)
    pdf.save()
    return buffer.getvalue()
