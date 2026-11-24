#!/usr/bin/env python3
"""Renders docs/privacy-policy.md as a PDF the client can read and publish on the website."""
import html, pathlib, re
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

SRC = pathlib.Path('/root/Moving_automation/docs/privacy-policy.md')
OUT = '/root/Moving_automation/docs/Express-Haulers-Privacy-Policy.pdf'

ss = getSampleStyleSheet()
ink = colors.HexColor('#1a1a1a')
S = dict(
    title=ParagraphStyle('t', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=19, textColor=ink, spaceAfter=12),
    h=ParagraphStyle('h', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=11.5, textColor=ink,
                     spaceBefore=13, spaceAfter=5),
    p=ParagraphStyle('p', parent=ss['Normal'], fontSize=9.8, leading=14, textColor=ink, spaceAfter=7),
)
md = lambda s: re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(s, quote=False))

story, buf = [], []
bullet = False          # a wrapped bullet line belongs to the bullet above it


def flush():
    global bullet
    if buf:
        story.append(Paragraph(md(' '.join(buf)), S['p'], bulletText='•' if bullet else None))
        buf.clear()
    bullet = False


for line in SRC.read_text().splitlines():
    line = line.rstrip()
    if line.startswith('# '):
        flush(); story.append(Paragraph(md(line[2:]), S['title']))
    elif line.startswith('## '):
        flush(); story.append(Paragraph(md(line[3:]), S['h']))
    elif line.startswith('- '):
        flush(); bullet = True; buf.append(line[2:])
    elif not line:
        flush()
    else:
        buf.append(line)
flush()

SimpleDocTemplate(OUT, pagesize=LETTER, leftMargin=0.95 * inch, rightMargin=0.95 * inch,
                  topMargin=0.85 * inch, bottomMargin=0.85 * inch,
                  title='Express Haulers Moving - Privacy Policy').build(story)
print(OUT)
