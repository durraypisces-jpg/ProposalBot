import sys
from pypdf import PdfReader, PdfWriter
d = sys.argv[1]
body, nohf = PdfReader(f"{d}/body.pdf"), PdfReader(f"{d}/nohf.pdf")
w = PdfWriter()
w.add_page(nohf.pages[0])
for p in body.pages[1:]: w.add_page(p)
w.add_metadata({"/Title": "AI Reporting Assistant – Proposal & Requirements v1.1", "/Author": "Durrayshahwar Zehra"})
w.write(sys.argv[2])
