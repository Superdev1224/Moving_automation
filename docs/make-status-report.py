#!/usr/bin/env python3
"""Builds docs/Express-Haulers-Automation-Status.pdf — requested vs delivered, for the client."""
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

OUT = '/root/Moving_automation/docs/Express-Haulers-Automation-Status.pdf'
DATE = 'September 20, 2026'

INK = colors.HexColor('#1a1a1a')
MUTED = colors.HexColor('#6b7280')
RULE = colors.HexColor('#e5e7eb')
BAND = colors.HexColor('#f5f6f8')
GREEN = colors.HexColor('#1f7a4d')
AMBER = colors.HexColor('#a8620a')
RED = colors.HexColor('#a32b2b')
BLUE = colors.HexColor('#1f4e79')

ss = getSampleStyleSheet()
S = dict(
    title=ParagraphStyle('t', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=20,
                         textColor=INK, alignment=TA_LEFT, spaceAfter=2),
    sub=ParagraphStyle('s', parent=ss['Normal'], fontSize=10, textColor=MUTED, spaceAfter=16),
    h2=ParagraphStyle('h2', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=12.5,
                      textColor=INK, spaceBefore=16, spaceAfter=7),
    body=ParagraphStyle('b', parent=ss['Normal'], fontSize=9.5, leading=13.5, textColor=INK, spaceAfter=6),
    cell=ParagraphStyle('c', parent=ss['Normal'], fontSize=8.8, leading=11.6, textColor=INK),
    cellb=ParagraphStyle('cb', parent=ss['Normal'], fontSize=8.8, leading=11.6, textColor=INK,
                         fontName='Helvetica-Bold'),
    head=ParagraphStyle('h', parent=ss['Normal'], fontSize=8.5, leading=11, textColor=colors.white,
                        fontName='Helvetica-Bold'),
    foot=ParagraphStyle('f', parent=ss['Normal'], fontSize=8, textColor=MUTED),
)
P = lambda t, s='cell': Paragraph(t, S[s])


def table(rows, widths, head_color=BLUE):
    t = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), head_color),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, RULE),
        ('LINEAFTER', (0, 0), (-2, -1), 0.4, RULE),
        ('BOX', (0, 0), (-1, -1), 0.5, RULE),
    ]
    for i in range(2, len(rows), 2):
        style.append(('BACKGROUND', (0, i), (-1, i), BAND))
    t.setStyle(TableStyle(style))
    return t


def page_furniture(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(0.9 * inch, 0.72 * inch, LETTER[0] - 0.9 * inch, 0.72 * inch)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.9 * inch, 0.55 * inch, 'Express Haulers Moving — automation status')
    canvas.drawRightString(LETTER[0] - 0.9 * inch, 0.55 * inch, f'Page {canvas.getPageNumber()}')
    canvas.restoreState()


story = []
story.append(Paragraph('Express Haulers Moving', S['title']))
story.append(Paragraph(f'Automation project — what was requested and what is running · {DATE}', S['sub']))

story.append(Paragraph('Summary', S['h2']))
story.append(Paragraph(
    'Of the 14 automations in the original brief, <b>12 are live</b>, one is partly delivered, and one '
    '(the TikTok/Instagram influencer email) is not yet possible in full. Four extra pieces were added '
    'along the way, including a daily digest that replaces the ChatGPT one. Everything runs on the '
    'company\'s own server, and each automation was tested against real company data before going live.', S['body']))

counts = [[P('12', 'cellb'), P('1', 'cellb'), P('1', 'cellb'), P('4', 'cellb')],
          [P('Live'), P('Partly done'), P('Not possible yet'), P('Extras added')]]
t = Table(counts, colWidths=[1.6 * inch] * 4, hAlign='LEFT')
t.setStyle(TableStyle([
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('BOX', (0, 0), (-1, -1), 0.5, RULE),
    ('INNERGRID', (0, 0), (-1, -1), 0.4, RULE),
    ('BACKGROUND', (0, 0), (-1, 0), BAND),
    ('TEXTCOLOR', (0, 0), (0, 0), GREEN),
    ('TEXTCOLOR', (1, 0), (1, 0), AMBER),
    ('TEXTCOLOR', (2, 0), (2, 0), RED),
    ('TEXTCOLOR', (3, 0), (3, 0), BLUE),
    ('FONTSIZE', (0, 0), (-1, 0), 15),
    ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
]))
story.append(t)

LIVE = [
    ('1', 'Rate RingCentral conversations cold/warm/hot with a short description and tips for the sales team',
     'Calls are recorded, transcribed on our own server and rated by AI, with tips, into Slack <b>#lead-scores</b>. Text conversations are rated the same way.'),
    ('3', 'Summarise where we left off with a customer and the next steps, into ClickUp',
     'Written as a comment on that customer\'s ClickUp card, matched by phone number.'),
    ('4', '$52 deposit paid <b>and</b> estimate signed → card to "Moves Booked"',
     'Stripe reports the payment instantly; the signature is confirmed in Movegistics and by email.'),
    ('5', 'Only one of the two → card to "Pending"',
     'After a 30-minute grace period, so a card does not bounce between statuses. If the customer signs later, the card upgrades to Moves Booked by itself.'),
    ('6', 'Upsell message sent → card to "Upsell In Progess"',
     'Reads outgoing texts on all three company lines. AI confirms it is a genuine upsell, so crew job sheets are not mistaken for one.'),
    ('7', 'Watch "Scheduled Follow Ups" and remind the team in Slack',
     '10 minutes before each follow-up, plus an 8:00 AM list of the day (including overdue ones) in <b>#sales</b>.'),
    ('8', 'New leads from Yelp, Thumbtack, Instagram, TikTok and the website → #inbound-leads',
     'Website, Yelp and Thumbtack leads post within a minute, with the customer details. Instagram and TikTok do not send lead emails (see item 2).'),
    ('9', 'Cards added to "Moves Booked" → shift on Connecteam "Pending"',
     'Copies the card name, the move time and the notes, and skips duplicates if a shift already exists.'),
    ('10', 'Check invoice maths and flag discrepancies in Slack',
     'Reads the invoice PDF and checks every line, the discounts, the deposit and the 3.5% card fee. Verified against 8 real invoices.'),
    ('11', 'Send every invoice to Slack for the auditor',
     'Posted to <b>#invoices</b> with the PDF attached and a summary of hours, deposit and balance.'),
    ('12', 'Claim emails saved to ClickUp in the company template + Slack alert',
     'AI fills in the Moving Complaint Intake Form, creates a card in the new "Claims Intake" list and alerts <b>#sales-issues-and-damages</b>. Follow-up emails are added as comments on the same card.'),
    ('13', 'Cards under "Onsite Estimates" → shift on the Connecteam onsite schedule',
     'Uses the ClickUp due date, an "Estimate Date" line in the card, or the date in the card name.'),
]

story.append(Paragraph('Live automations', S['h2']))
rows = [[P('#', 'head'), P('What was asked for', 'head'), P('What runs today', 'head')]]
rows += [[P(n), P(a), P(b)] for n, a, b in LIVE]
story.append(table(rows, [0.35 * inch, 2.75 * inch, 3.6 * inch]))

story.append(Paragraph('Partly delivered', S['h2']))
rows = [[P('#', 'head'), P('Request', 'head'), P('Where it stands', 'head')],
        [P('14'), P('Integrate Movegistics with ClickUp, Connecteam, RingCentral and other platforms'),
         P('The Movegistics API is connected and already used to confirm signed estimates and to match customers '
           'across systems. Its API covers leads, contacts and work requests but <b>not invoices or payments</b>, so those '
           'still come from Stripe and from the invoice emails. Further integration is possible, for example pushing '
           'Yelp and Thumbtack leads straight into Movegistics.')]]
story.append(table(rows, [0.35 * inch, 2.75 * inch, 3.6 * inch], head_color=AMBER))

story.append(Paragraph('Not implemented', S['h2']))
rows = [[P('#', 'head'), P('Request', 'head'), P('Why, and what is possible', 'head')],
        [P('2'), P('When an influencer sends their email on TikTok or Instagram, send them the template email with their name'),
         P('<b>Instagram: ready to build.</b> It needs a Meta app, Instagram messaging permissions and Meta\'s app review, '
           'plus the "Collaboration Details" email template. Once approved, the email would send automatically and the '
           'influencer would be logged in ClickUp.<br/><br/>'
           '<b>TikTok: not possible.</b> TikTok provides no way for outside systems to read direct messages. The practical '
           'alternative is to ask creators for their email through a form or link.')]]
story.append(table(rows, [0.35 * inch, 2.75 * inch, 3.6 * inch], head_color=RED))

story.append(Paragraph('Added beyond the brief', S['h2']))
rows = [[P('Addition', 'head'), P('What it does', 'head')],
        [P('Daily digest, 8:00 AM'), P('A summary of the previous day in <b>#daily-digest-and-updates</b>: cards booked and '
                                       'needing attention, deposits taken, call volume, hot leads, claims and today\'s crew '
                                       'schedule. Built from ClickUp, Stripe, RingCentral and Connecteam directly, so it '
                                       'replaces the ChatGPT digest.')],
        [P('#automation-errors channel'), P('Anything an automation cannot handle is reported for a human: a card with no '
                                            'readable date, a customer with no card, an invoice PDF that cannot be read. '
                                            'Nothing fails silently.')],
        [P('Call transcription on the company server'), P('RingCentral\'s own transcription is not enabled on the account. '
                                                          'Calls are transcribed locally instead: no third-party service, no '
                                                          'per-minute fee, and recordings never leave the server.')],
        [P('Safety net'), P('Nightly backups of all automations and settings, and an automatic check that catches broken '
                            'code before anything is published.')]]
story.append(table(rows, [1.6 * inch, 5.1 * inch], head_color=BLUE))

story.append(Paragraph('Running costs', S['h2']))
rows = [[P('Item', 'head'), P('Cost', 'head'), P('Notes', 'head')],
        [P('AI (Claude)'), P('about $30–40 / month'), P('Lead scoring, call summaries, claim intake and the daily digest. Charged per use.')],
        [P('Server'), P('existing VPS'), P('Runs everything, including call transcription.')],
        [P('Other tools'), P('no change'), P('ClickUp, Slack, Stripe, Gmail, RingCentral, Connecteam and Movegistics all use existing accounts.')]]
story.append(table(rows, [1.4 * inch, 1.5 * inch, 3.8 * inch]))

story.append(Paragraph('What we need from you', S['h2']))
for txt in [
    '<b>Instagram:</b> set up the Meta app and submit it for review, and send the "Collaboration Details" email template.',
    '<b>Movegistics fee setting:</b> the 3.5% card fee is charged on the full amount on some invoices and after the deposit '
    'on others, so customers are treated differently by about $1.75.',
    '<b>One data fix:</b> the card for Maxine Cummings has an email address typed into the "Phone Number" field, so '
    'automations cannot match her by phone.',
    '<b>Optional:</b> let the automations keep the "Customer Stage" field up to date, and let the AI move cards to '
    '"Scheduled Follow Ups" or "Unresponsive Leads" based on what is said on calls.',
]:
    story.append(Paragraph('• ' + txt, S['body']))

story.append(Spacer(1, 10))
story.append(Paragraph('Every automation was tested against real company data before going live, and each one can be paused '
                       'or adjusted at any time.', S['foot']))

doc = BaseDocTemplate(OUT, pagesize=LETTER, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                      topMargin=0.85 * inch, bottomMargin=0.95 * inch,
                      title='Express Haulers Moving — automation status',
                      author='Automation project')
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='body')
doc.addPageTemplates([PageTemplate(id='all', frames=[frame], onPage=page_furniture)])
doc.build(story)
print('wrote', OUT)
