#!/usr/bin/env python3
"""Builds the Additional Automation Services Agreement as both .docx (editable) and .pdf.

  python3 docs/make-contract.py
Fill-in blanks are written as a line of underscores so they can be typed in Word or written by hand.
"""
import html, re, zipfile
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

BASE = '/root/Moving_automation/docs/Express-Haulers-Additional-Automation-Agreement'
BLANK = '________________________'
SHORT = '__________'

TITLE = 'Additional Automation Services Agreement'
# Blocks: ('h', text) heading, ('p', text) paragraph, ('b', [items]) bullets, ('t', rows) table, ('sig',)
# **bold** is allowed inside text.
BLOCKS = [
    ('p', 'This Agreement is made on **September 22, 2026** (the "Effective Date") between:'),
    ('p', '**Developer:** **Andrei V** (the "Developer"), an independent automation developer; and'),
    ('p', f'**Client:** {BLANK}{BLANK}, on behalf of **Express Haulers Moving**, '
          '1310 Hamburg Turnpike, Wayne, NJ 07470 (the "Client").'),

    ('h', '1. Background'),
    ('p', 'The Developer has already built and deployed the Client\'s first set of automations (lead notifications, '
          'follow-up reminders, ClickUp to Connecteam scheduling, invoices, claims, booking status, upsell, call and text '
          'summaries, and the daily digest). This Agreement covers a second, separate set of automations described in '
          'Section 2. Work already delivered is not affected by this Agreement.'),

    ('h', '2. Scope of work'),
    ('p', 'The Developer will build, test and deploy the following automations on the Client\'s existing automation '
          'server (n8n). Any item the parties cross out and initial before signing is excluded.'),
    ('t', [
        ['#', 'Automation', 'What it does'],
        ['1', 'Lead template', 'New leads are posted in #inbound-leads and on the ClickUp card using the Client\'s lead template.'],
        ['2', 'Duplicate rule', 'Same customer and same move date = duplicate. A new move date from a returning customer = a new move.'],
        ['3', 'AI card status', 'After calls, the AI suggests the right ClickUp status. It runs in "suggest" mode first and moves cards '
              'only after the Client approves the results. It never overrides deposit/signature (booking) rules.'],
        ['4', 'Estimate to ClickUp', 'When an estimate is sent, opened or signed in Movegistics, the customer\'s ClickUp card is updated with the total and a link.'],
        ['5', 'Stale lead alert', 'A new lead not contacted within the agreed time during business hours triggers a #sales alert.'],
        ['6', 'No response to estimate', 'Estimates with no customer response after 24 hours are listed in the morning follow-up list.'],
        ['7', 'Lost-lead reasons', 'When a card moves to Moves Lost, the AI records the reason. A monthly report goes to Google Sheets.'],
        ['8', 'Missed call rescue', 'A missed call or voicemail with no call-back within 5 minutes triggers one #sales alert with the customer\'s card.'],
        ['9', 'After-hours auto-reply', 'Email/text reply to new leads outside business hours, using wording approved by the Client.'],
        ['10', 'Action items', 'A short "Next:" line of action items under each call summary.'],
        ['11', 'Pre-move confirmation', 'Confirmation to the customer 48 hours before a booked move, using wording approved by the Client.'],
        ['12', 'Claims from calls/texts', 'Claims raised on calls or texts also create a claim card and a #sales-issues-and-damages alert.'],
        ['13', 'Unanswered emails', 'Every 2 hours the AI checks Gmail for customer and influencer emails that need a reply and have '
               'not been answered, and posts them in #sales. Lead notifications (website, Yelp, Thumbtack) and emails that need no reply '
               '(for example "thank you") are left out.'],
    ]),

    ('h', '3. Client responsibilities'),
    ('p', 'To complete the work, the Client will provide in a timely manner:'),
    ('b', ['Access to the systems involved (ClickUp, Movegistics, RingCentral, Slack, Google, Connecteam), and keeps that access active.',
           'Decisions the work depends on, including status rules (#3), business hours and alert recipients (#5, #8), and the Google account for reports (#7).',
           'Written approval of the wording of every message sent to customers (#9, #11) before it goes live.',
           'Confirmation of whether Movegistics already sends similar automatic messages, so customers do not receive duplicates.']),
    ('p', 'Delays in receiving this information extend the timeline accordingly.'),

    ('h', '4. Delivery and acceptance'),
    ('b', ['Each automation is first run in **test mode**, where its output goes to an internal channel only, so the Client can review it.',
           'The Client has **5 business days** after an automation is ready to report problems in writing (a Slack or email message is enough).',
           'An automation is accepted when the Client approves it, or when no problem is reported within that period. It then goes live.',
           'Target completion: **5 days** after the Client\'s first payment and receipt of the information in Section 3.']),

    ('h', '5. Fees and payment'),
    ('b', ['Total fixed fee for the work in Section 2: **$2,000** (USD), paid in two parts.',
           '**$1,000 up front**, on signing. Work starts once this payment is received.',
           '**$1,000 on completion**, after the Developer finishes the work and the Client has checked it and reports no issues '
           '(see Section 4). Any issue reported is fixed first, then the balance is due.',
           'Payment method: **PayPal**.',
           'If items are removed from scope before work on them starts, the fee is reduced by the amount the parties agree in writing.']),
    ('p', '**Third-party costs are not included** and are paid directly by the Client: server hosting, AI usage (Anthropic), '
          'and subscriptions such as RingCentral, Slack, ClickUp, Movegistics, Connecteam and Google.'),

    ('h', '6. Changes'),
    ('p', 'Work not described in Section 2, or significant changes to an item after it has been built, is a change request. '
          'The Developer will quote it in writing before starting, and it is billed only if the Client approves the quote.'),

    ('h', '7. Support after delivery'),
    ('b', ['The Developer fixes defects in the delivered automations free of charge for **30 days** after acceptance.',
           'This does not cover problems caused by changes the Client or a third party makes (for example, changes to a vendor\'s API, '
           'account plans, permissions, or the way the team fills in ClickUp cards).']),

    ('h', '8. Ownership'),
    ('p', 'Once all fees are paid in full, the Client owns the workflows and configuration built for the Client under this Agreement. '
          'The Developer keeps the right to reuse general know-how, tools and code that are not specific to the Client\'s business.'),

    ('h', '9. Confidentiality and data'),
    ('p', 'The Developer will keep confidential all Client business information, customer data, and passwords/API keys, will use them '
          'only to perform this Agreement, and will not share them with anyone else. At the end of the engagement, the Client '
          'should change the passwords and API keys shared with the Developer.'),

    ('h', '10. Limitation of liability'),
    ('p', 'The automations assist the Client\'s team and do not replace its judgment. The Client remains responsible for its '
          'communications with customers and its business decisions. Neither party is liable for indirect or consequential losses, '
          'such as lost profits or lost jobs. The Developer\'s total liability under this Agreement is limited to the fees paid '
          'under it.'),

    ('h', '11. Independent contractor'),
    ('p', 'The Developer is an independent contractor, not an employee, and is responsible for their own taxes.'),

    ('sig',),
]

SIGNERS = [
    {'role': 'Developer', 'note': '', 'name': 'Andrei V', 'date': 'September 22, 2026', 'signed': True},
    {'role': 'Client', 'note': 'on behalf of Express Haulers Moving', 'name': '', 'date': '', 'signed': False},
]
SIGN_FONT_T1 = ('/usr/share/fonts/type1/urw-base35/Z003-MediumItalic.afm',
                '/usr/share/fonts/X11/Type1/Z003-MediumItalic.pfb')      # URW Chancery: a script hand
SIGN_FONT_OTF = '/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf'
SIGN_PNG = '/root/Moving_automation/docs/signature-andrei-v.png'


def signature_png(name=SIGNERS[0]['name'], path=SIGN_PNG):
    """The same script hand as the PDF, as a transparent PNG for the Word file."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(SIGN_FONT_OTF, 150)
    pad = 30
    tmp = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    box = tmp.textbbox((0, 0), name, font=font)
    img = Image.new('RGBA', (box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - box[0], pad - box[1]), name, font=font, fill=(20, 30, 90, 255))
    img = img.rotate(3, expand=True, resample=Image.BICUBIC)
    img.save(path)
    return path, img.size


# ---------------------------------------------------------------- PDF
def pdf():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.pdfmetrics import EmbeddedType1Face, registerTypeFace, Font
    face = EmbeddedType1Face(*SIGN_FONT_T1)
    registerTypeFace(face)
    pdfmetrics.registerFont(Font('Signature', face.name, 'WinAnsiEncoding'))
    pdfmetrics.registerFontFamily('Signature', normal='Signature', bold='Signature',
                                  italic='Signature', boldItalic='Signature')
    ss = getSampleStyleSheet()
    ink = colors.HexColor('#1a1a1a')
    S = dict(
        title=ParagraphStyle('t', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=18, textColor=ink, spaceAfter=14),
        h=ParagraphStyle('h', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=11.5, textColor=ink, spaceBefore=10, spaceAfter=5),
        p=ParagraphStyle('p', parent=ss['Normal'], fontSize=9.8, leading=13.8, textColor=ink, spaceAfter=6),
        cell=ParagraphStyle('c', parent=ss['Normal'], fontSize=8.8, leading=11.5, textColor=ink),
    )
    md = lambda s: re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(s, quote=False))
    story = [Paragraph(TITLE, S['title'])]
    for blk in BLOCKS:
        kind = blk[0]
        if kind == 'h':
            story.append(Paragraph(md(blk[1]), S['h']))
        elif kind == 'p':
            story.append(Paragraph(md(blk[1]), S['p']))
        elif kind == 'b':
            for it in blk[1]:
                story.append(Paragraph(md(it), S['p'], bulletText='•'))
        elif kind == 't':
            rows = [[Paragraph(f'<b>{md(c)}</b>' if i == 0 else md(c), S['cell']) for c in r] for i, r in enumerate(blk[1])]
            t = Table(rows, colWidths=[0.35 * inch, 1.55 * inch, 4.6 * inch], repeatRows=1)
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f2f5')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#c9ced6')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            story += [t, Spacer(1, 6)]
        elif kind == 'sig':
            head = [Paragraph('Signatures', S['h']),
                    Paragraph('By signing below, both parties agree to the terms of this Agreement.', S['p'])]
            cols = []
            for who in SIGNERS:
                title = f'<b>{who["role"]}</b>' + (f' <font size=8>({who["note"]})</font>' if who['note'] else '')
                if who['signed']:
                    lines = [title, '',
                             f'Signature: <font name="Signature" size=19 color="#14205a">{who["name"]}</font>',
                             '<font color="#9aa0aa">_______________________________________</font>', '',
                             f'Name: <b>{who["name"]}</b>', '', f'Date: <b>{who["date"]}</b>']
                else:
                    lines = [title, '', 'Signature: ____________________________', '',
                             'Name: _______________________________', '',
                             'Date: ________________________________']
                cols.append([Paragraph(l or '&nbsp;', S['p']) for l in lines])
            t = Table([[cols[0], cols[1]]], colWidths=[3.25 * inch, 3.25 * inch])
            t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            story.append(KeepTogether(head + [Spacer(1, 8), t]))

    def footer(c, d):
        c.saveState(); c.setFont('Helvetica', 8); c.setFillColor(colors.HexColor('#6b7280'))
        c.drawString(0.9 * inch, 0.55 * inch, f'{TITLE}  ·  Initials: Developer ______   Client ______')
        c.drawRightString(LETTER[0] - 0.9 * inch, 0.55 * inch, f'Page {d.page}')
        c.restoreState()

    doc = SimpleDocTemplate(BASE + '.pdf', pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.85 * inch, title=TITLE)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


# ---------------------------------------------------------------- DOCX (plain Office Open XML, no dependencies)
def docx():
    esc = lambda s: html.escape(s, quote=False)
    png_path, (pw, ph) = signature_png()
    EMU = 9525                                    # English Metric Units per pixel at 96 dpi
    width_px = 200                                 # on-page width; height keeps the aspect ratio
    cx, cy = int(width_px * EMU), int(width_px * ph / pw * EMU)

    def sig_image():
        return ('<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr><w:r><w:drawing>'
                f'<wp:inline distT="0" distB="0" distL="0" distR="0" '
                'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="Signature"/><a:graphic '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData '
                'uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic '
                'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr>'
                '<pic:cNvPr id="1" name="Signature"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
                '<a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'r:embed="rIdSig"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr>'
                f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

    def runs(text, size=20, bold=False):
        out = []
        for i, part in enumerate(re.split(r'\*\*(.+?)\*\*', text)):
            if not part:
                continue
            b = bold or i % 2 == 1
            out.append(f'<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>{"<w:b/>" if b else ""}'
                       f'<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">{esc(part)}</w:t></w:r>')
        return ''.join(out)

    def para(text, size=20, bold=False, before=0, after=120, indent=None, keep=False):
        ppr = f'<w:spacing w:before="{before}" w:after="{after}" w:line="276" w:lineRule="auto"/>'
        if indent:
            ppr += f'<w:ind w:left="{indent}" w:hanging="260"/>'
        if keep:
            ppr += '<w:keepNext/>'
        return f'<w:p><w:pPr>{ppr}</w:pPr>{runs(text, size, bold)}</w:p>'

    def cell(text, width, bold=False, shade=None):
        sh = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ''
        return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{sh}</w:tcPr>'
                f'{para(text, 18, bold, after=40)}</w:tc>')

    def table(rows, widths, borders=True, header=True):
        b = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="C9CED6"/>'
                    for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV')) if borders else ''
        x = f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{b}</w:tblBorders>' \
            f'<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>' \
            f'<w:bottom w:w="60" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'
        x += ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>'
        for i, r in enumerate(rows):
            hdr = header and i == 0
            x += '<w:tr>' + ('<w:trPr><w:tblHeader/></w:trPr>' if hdr else '')
            x += ''.join(cell(c, w, hdr, 'F0F2F5' if hdr else None) for c, w in zip(r, widths)) + '</w:tr>'
        return x + '</w:tbl>' + para('', after=60)

    body = [para(TITLE, 36, True, after=240)]
    for blk in BLOCKS:
        kind = blk[0]
        if kind == 'h':
            body.append(para(blk[1], 23, True, before=200, after=100, keep=True))
        elif kind == 'p':
            body.append(para(blk[1]))
        elif kind == 'b':
            body += [para('•\t' + it, indent=360, after=80) for it in blk[1]]
        elif kind == 't':
            body.append(table(blk[1], [520, 2200, 6640]))
        elif kind == 'sig':
            body.append(para('Signatures', 23, True, before=240, after=100, keep=True))
            body.append(para('By signing below, both parties agree to the terms of this Agreement.'))
            def column(who):
                head = f'**{who["role"]}**' + (f' ({who["note"]})' if who['note'] else '')
                if who['signed']:
                    return (para(head, 20, after=60) + para('Signature:', 20, after=0) + sig_image()
                            + para(f'**{who["name"]}**', 20, after=60)
                            + para(f'Date: **{who["date"]}**', 20, after=60))
                return ''.join(para(l, 20, after=60) for l in
                               [head, '', 'Signature: ______________________', '', 'Name: __________________________',
                                '', 'Date: ___________________________'])
            cells = ''.join(f'<w:tc><w:tcPr><w:tcW w:w="4680" w:type="dxa"/></w:tcPr>{column(w)}</w:tc>'
                            for w in SIGNERS)
            body.append(f'<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/></w:tblPr><w:tblGrid><w:gridCol w:w="4680"/>'
                        f'<w:gridCol w:w="4680"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/></w:trPr>{cells}</w:tr></w:tbl>')

    footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              + para(f'{TITLE}  ·  Initials: Developer ______   Client ______', 16, after=0) + '</w:ftr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
                + ''.join(body) +
                '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
                '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1152" w:right="1296" w:bottom="1152" w:left="1296" '
                'w:header="708" w:footer="500" w:gutter="0"/></w:sectPr></w:body></w:document>')
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '</Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>',
        'word/_rels/document.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
            '<Relationship Id="rIdSig" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/signature.png"/>'
            '</Relationships>',
        'word/document.xml': document,
        'word/footer1.xml': footer,
        'docProps/core.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + TITLE + '</dc:title></cp:coreProperties>',
    }
    with zipfile.ZipFile(BASE + '.docx', 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.write(png_path, 'word/media/signature.png')


if __name__ == '__main__':
    pdf(); docx()
    print(BASE + '.pdf'); print(BASE + '.docx')
