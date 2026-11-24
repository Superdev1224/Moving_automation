#!/usr/bin/env python3
"""Builds the customer-message drafts (#9 after-hours reply, #11 pre-move confirmation) as .pdf/.docx.

  python3 docs/make-message-drafts.py
The client approves or edits the wording; nothing is sent to a customer until they do.
"""
import html, re, zipfile
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE = '/root/Moving_automation/docs/Express-Haulers-Customer-Message-Drafts'
TITLE = 'Customer message drafts for approval'

# ('h', text) heading · ('p', text) paragraph · ('b', [items]) bullets · ('q', label, text) message box
BLOCKS = [
    ('p', 'Two of the new automations send messages **to customers**. Nothing is switched on until you approve '
          'the wording below. Edit anything you want changed: we use your final words exactly.'),
    ('p', 'Text in **[square brackets]** is filled in automatically for each customer.'),

    ('h', '1. After-hours reply to new leads (#9)'),
    ('b', ['**When:** a new lead arrives between 8 PM and 8 AM, or on a day you choose to treat as closed.',
           '**Who gets it:** website and email leads get an email; leads with a mobile number also get a text.',
           '**Only once per customer**, and never if someone has already answered them.',
           'Yelp and Thumbtack replies cannot be sent by us: their apps do not allow it.']),
    ('q', 'Email — subject', 'Thanks for reaching out to Express Haulers Moving'),
    ('q', 'Email — body', 'Hi [First name],\n\n'
          'Thanks for getting in touch with Express Haulers Moving. We have your request and someone from our team '
          'will call you first thing in the morning, usually before 9:30 AM.\n\n'
          'If it helps, reply with any of these and we can prepare your quote before we call:\n'
          '• Move date\n'
          '• Where you are moving from and to (with apartment or floor, and stairs or elevator)\n'
          '• Roughly what needs moving (bedrooms, big items such as a piano or safe)\n'
          '• Whether you need packing, supplies or storage\n\n'
          'Talk soon,\n'
          'Express Haulers Moving\n'
          '(862) 379-9113 · contact@expresshaulers.com'),
    ('q', 'Text message', 'Express Haulers Moving here - thanks for your message! Our office is closed right now, '
          'but we will call you in the morning. Reply here any time with your move date and addresses and we will '
          'have your quote ready. Reply STOP to opt out.'),

    ('h', '2. Confirmation 48 hours before the move (#11)'),
    ('b', ['**When:** 48 hours before a booked move, using the move details in Movegistics.',
           '**Skipped** for moves booked less than 48 hours out, and when someone has already confirmed by hand.',
           '**For the first week** the automation posts each message in Slack for approval instead of sending it, '
           'so you can see exactly what customers would receive.']),
    ('q', 'Email — subject', 'Your move with Express Haulers Moving is confirmed for [Move date]'),
    ('q', 'Email — body', 'Hi [First name],\n\n'
          'We are all set for your move on **[Move date]**. Our crew of [number] will arrive between '
          '[arrival window].\n\n'
          'Moving from: [origin address]\n'
          'Moving to: [destination address]\n'
          'Estimated time: [hours] · Estimated cost: [amount]\n'
          'Deposit received: [deposit] (applied to your final bill)\n\n'
          'To keep the day running smoothly, please:\n'
          '• Reserve parking in front of both buildings if you can, and book the elevator if your building needs it\n'
          '• Have a certificate of insurance requested from your building if one is required\n'
          '• Finish any packing you are doing yourself, and keep valuables and documents with you\n'
          '• Empty and defrost the fridge, and take apart anything you do not want us to disassemble\n'
          '• Keep a clear path from the door to the truck\n\n'
          'The balance is due on the day, at the end of the move. We accept [payment methods].\n\n'
          'If anything has changed, reply to this email or call us on (862) 379-9113.\n\n'
          'Thank you for choosing us,\n'
          'Express Haulers Moving'),
    ('q', 'Text message', 'Express Haulers Moving: your move is confirmed for [Move date], crew arriving between '
          '[arrival window]. Reply YES to confirm, or call (862) 379-9113 if anything changed. Reply STOP to opt out.'),

    ('h', '3. Please check these before we switch anything on'),
    ('b', ['**Does Movegistics already send** a booking confirmation or reminder? If it does, we should use theirs '
           'rather than send a second message.',
           '**Business texting:** sending texts to customers needs RingCentral business-texting (A2P) registration on '
           'your account. If that is not done, we send the email only.',
           '**Arrival window:** what should the message say when the job has a start time but no window, for example '
           '"between 8 and 9 AM"?',
           '**Payment methods:** which ones should we list for the balance?',
           '**Anything you always tell customers** the day before that is missing from the list above.']),
]


def pdf():
    ss = getSampleStyleSheet()
    ink = colors.HexColor('#1a1a1a')
    S = dict(
        title=ParagraphStyle('t', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=18, textColor=ink, spaceAfter=14),
        h=ParagraphStyle('h', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=12, textColor=ink, spaceBefore=12, spaceAfter=6),
        p=ParagraphStyle('p', parent=ss['Normal'], fontSize=9.8, leading=13.8, textColor=ink, spaceAfter=6),
        lbl=ParagraphStyle('l', parent=ss['Normal'], fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.HexColor('#5b6472'), spaceAfter=3),
        msg=ParagraphStyle('m', parent=ss['Normal'], fontSize=9.5, leading=13.5, textColor=ink),
    )
    md = lambda s: re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(s, quote=False)).replace('\n', '<br/>')
    story = [Paragraph(TITLE, S['title'])]
    for blk in BLOCKS:
        if blk[0] == 'h':
            story.append(Paragraph(md(blk[1]), S['h']))
        elif blk[0] == 'p':
            story.append(Paragraph(md(blk[1]), S['p']))
        elif blk[0] == 'b':
            for it in blk[1]:
                story.append(Paragraph(md(it), S['p'], bulletText='•'))
        elif blk[0] == 'q':
            inner = [[Paragraph(blk[1].upper(), S['lbl'])], [Paragraph(md(blk[2]), S['msg'])]]
            t = Table(inner, colWidths=[6.2 * inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7f8fa')),
                ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#d5dae1')),
                ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
            story += [t, Spacer(1, 8)]
    SimpleDocTemplate(BASE + '.pdf', pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=TITLE).build(story)


def docx():
    esc = lambda s: html.escape(s, quote=False)

    def runs(text, size=20, bold=False):
        out = []
        for i, part in enumerate(re.split(r'\*\*(.+?)\*\*', text)):
            if not part:
                continue
            b = bold or i % 2 == 1
            for j, line in enumerate(part.split('\n')):
                if j:
                    out.append('<w:r><w:br/></w:r>')
                out.append(f'<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>{"<w:b/>" if b else ""}'
                           f'<w:sz w:val="{size}"/></w:rPr><w:t xml:space="preserve">{esc(line)}</w:t></w:r>')
        return ''.join(out)

    def para(text, size=20, bold=False, before=0, after=120, shade=None):
        ppr = f'<w:spacing w:before="{before}" w:after="{after}" w:line="276" w:lineRule="auto"/>'
        if shade:
            ppr += (f'<w:shd w:val="clear" w:fill="{shade}"/><w:ind w:left="200" w:right="200"/>'
                    '<w:pBdr><w:top w:val="single" w:sz="4" w:color="D5DAE1"/><w:left w:val="single" w:sz="4" w:color="D5DAE1"/>'
                    '<w:bottom w:val="single" w:sz="4" w:color="D5DAE1"/><w:right w:val="single" w:sz="4" w:color="D5DAE1"/></w:pBdr>')
        return f'<w:p><w:pPr>{ppr}</w:pPr>{runs(text, size, bold)}</w:p>'

    body = [para(TITLE, 36, True, after=240)]
    for blk in BLOCKS:
        if blk[0] == 'h':
            body.append(para(blk[1], 24, True, before=220, after=100))
        elif blk[0] == 'p':
            body.append(para(blk[1]))
        elif blk[0] == 'b':
            body += [para('•\t' + it, after=80) for it in blk[1]]
        elif blk[0] == 'q':
            body.append(para(blk[1].upper(), 17, True, before=80, after=0, shade='F7F8FA'))
            body.append(para(blk[2], 20, after=160, shade='F7F8FA'))

    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + ''.join(body) +
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1152" w:right="1296" w:bottom="1152" w:left="1296"/></w:sectPr></w:body></w:document>')
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>',
        'word/document.xml': document,
    }
    with zipfile.ZipFile(BASE + '.docx', 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


if __name__ == '__main__':
    pdf(); docx()
    print(BASE + '.pdf'); print(BASE + '.docx')
