#!/usr/bin/env python3
"""Renders the website-leads setup guide as a PDF for the client."""
import html, re
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Preformatted, KeepTogether, Table, TableStyle

OUT = '/root/Moving_automation/docs/Express-Haulers-Website-Leads-Setup.pdf'
SNIPPET = open('/root/Moving_automation/docs/website-form-copy.html').read().split('-->', 1)[1].strip()
SNIPPET = '\n'.join(l for l in SNIPPET.splitlines() if not l.strip().startswith('//'))   # comments are for the file, not the page

ss = getSampleStyleSheet()
ink, grey, accent = colors.HexColor('#1a1a1a'), colors.HexColor('#5a5a5a'), colors.HexColor('#1f4e79')
S = dict(
    title=ParagraphStyle('t', parent=ss['Title'], fontName='Helvetica-Bold', fontSize=20, textColor=ink, alignment=0, spaceAfter=2),
    sub=ParagraphStyle('s', parent=ss['Normal'], fontSize=9.5, textColor=grey, spaceAfter=14),
    h=ParagraphStyle('h', parent=ss['Heading2'], fontName='Helvetica-Bold', fontSize=13, textColor=accent, spaceBefore=14, spaceAfter=6),
    h3=ParagraphStyle('h3', parent=ss['Normal'], fontName='Helvetica-Bold', fontSize=10.5, textColor=ink, spaceBefore=6, spaceAfter=3),
    p=ParagraphStyle('p', parent=ss['Normal'], fontSize=10, leading=14.5, textColor=ink, spaceAfter=7),
    li=ParagraphStyle('li', parent=ss['Normal'], fontSize=10, leading=14.5, textColor=ink, leftIndent=18, spaceAfter=3),
    code=ParagraphStyle('c', parent=ss['Code'], fontName='Courier', fontSize=6.9, leading=8.4, textColor=ink),
    note=ParagraphStyle('n', parent=ss['Normal'], fontSize=9, leading=13, textColor=grey, spaceAfter=6),
)
def md(s):
    s = html.escape(s, quote=False)
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    return re.sub(r'`(.+?)`', r'<font face="Courier">\1</font>', s)
P = lambda t, st='p': Paragraph(md(t), S[st])
def steps(items):
    return [Paragraph(md(t), S['li'], bulletText=f'{i}.') for i, t in enumerate(items, 1)]

code = Table([[Preformatted(SNIPPET, S['code'])]], colWidths=[7.0 * inch])
code.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f3f5f8')),
                          ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#c9d1dc')),
                          ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                          ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
story = [
    P('Website Leads Setup', 'title'),
    P('Express Haulers Moving · September 30, 2026', 'sub'),

    P('Why website leads are being missed', 'h'),
    P('The website has **two different quote forms**, and neither reaches the automation right now:'),
    *steps([
        '**The Shopify form on the home page.** It only reached us through Shopify\'s notification email. That email was '
        'turned off on Sept 28 and has since been turned back on, so this form works again (a test arrived on Sept 30).',
        '**The Helium form on Get a Quote and Contact** (the "Customer Fields" app). It only saves the person as a Shopify '
        'customer and **sends no email at all**. Every lead from it has gone unseen, at least 11 since Sept 12, and 9 of them '
        'never got a ClickUp card or a call.']),
    Spacer(1, 4),
    P('**Option B below fixes both forms** and is the step that matters. Option A only covers the home-page form.'),
    P('Option A: the Shopify email (home-page form only) - already done', 'h'),
    P('The Shopify email is back on, so the home-page form works again. The Gmail filter below is optional: it keeps these '
      'emails out of the inbox, and the automation still reads them.'),
    P('In Shopify', 'h3'), *steps([
        'Go to **Settings → Notifications**.',
        'Open **Staff notifications** and make sure contact form emails go to **contact@expresshaulers.com**.',
        'Save.']),
    P('In Gmail (signed in as contact@expresshaulers.com)', 'h3'), *steps([
        'Go to **Settings → See all settings → Filters and Blocked Addresses → Create a new filter**.',
        'In **From**, enter `mailer@shopify.com`, then click **Create filter**.',
        'Tick **Skip the Inbox** and **Apply the label** → new label **Website leads**.',
        'Click **Create filter**.']),
    Spacer(1, 4),
    P('Website leads then reach #inbound-leads, ClickUp and Movegistics again within a minute of being sent.'),

    P('Option B: send both forms straight to the automation (required)', 'h'),
    P('Both forms stay exactly as they look. A short script sends a copy of every submission, from either form, to the '
      'automation, including every field of the Helium form. It takes about 2 minutes to paste. Andrei has requested collaborator access with code 9696; once the store owner approves it, he can '
      'install and test it himself.'),
    P('To paste it yourself', 'h3'), *steps([
        'In Shopify, go to **Online Store → Themes**.',
        'On the live theme, click the **three dots (...) → Edit code**.',
        'Open **layout/theme.liquid**.',
        'Find `</body>` near the bottom and paste the code below just before it (it is also attached as a text file, '
        '**website-form-copy.html**, which is easier to copy from).',
        'Click **Save**.']), Spacer(1, 6), code,
]
story += [Spacer(1, 8),
    P('The link in the code works like a password, so keep it private. Once Option B is in, the Shopify email can stay '
      'off. If both are on for a while, a website lead may show twice in #inbound-leads, but the ClickUp card and '
      'Movegistics lead are not duplicated.', 'note'),

    KeepTogether([P('Test it', 'h'),
        P('After Option B, send one test through **each** form: the home page, and the Get a Quote page.'),
        *steps([
            'Fill in the form with a test name such as **Test Website Lead**, a real phone and email you can check, and a move date.',
            'Within a minute it should appear in **#inbound-leads** as a New Website lead, with a ClickUp card in **New Leads**.',
            'Tell Andrei the times you sent the tests. He will confirm every field came through and delete the test cards and '
            'Movegistics leads.'])]),
]

SimpleDocTemplate(OUT, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                  topMargin=0.7 * inch, bottomMargin=0.7 * inch, title='Website Leads Setup',
                  author='Andrei V').build(story)
print(OUT)
