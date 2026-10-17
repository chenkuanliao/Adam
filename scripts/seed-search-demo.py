"""Create an explicitly synthetic PDF and saved chats in a local preview database.

Use mainenv. Required --data-dir must belong to the backend at --api-base.
Safe to rerun: existing demo paper and chat titles are reused, never overwritten.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Search Playground - Synthetic Research Study'
SECTIONS = [
    ('A study of a fictional research assistant', [
        'This document and every associated chat are fabricated for testing Adam search. No measurements or research claims are real. The study follows a fictional assistant named Cedar as it retrieves notes from a small research library.',
        'The study asks three questions: can sparse attention reduce retrieval cost, how does latency change with context length, and which limitations should a reader notice? Search for study to find repeated matches across all four pages.',
        'In this synthetic study, Cedar reads 120 invented notes. Each note has a title, a short summary, and a source tag. The baseline reads every note; the sparse attention variant selects a smaller candidate set.',
    ]),
    ('Method and benchmark design', [
        'The benchmark contains three fictional workloads: short summaries, long context retrieval, and conflicting evidence. Every benchmark uses the same invented corpus, so the numbers below are illustrations only.',
        'The study compares a baseline with a sparse attention variant. Retrieval recall describes how many relevant notes were found. Latency describes elapsed time before the assistant can begin an answer.',
        'Use PDF search for benchmark or sparse attention. Use Enter to advance and Shift+Enter to go backward. The benchmark keyword also appears in multiple saved chats, so the chat history search should find more than one conversation.',
    ]),
    ('Results and tradeoffs', [
        'In the fictional results, sparse attention improves median latency from 240 milliseconds to 150 milliseconds. Retrieval recall changes from 92 percent to 89 percent. This is a synthetic tradeoff, not evidence about a real model.',
        'The study finds that shorter candidate lists can reduce latency but may omit useful evidence. A reader should inspect missed notes before accepting an apparently faster system.',
        'The study repeats the benchmark at three context lengths. The latency advantage grows in this fabricated example, while recall remains sensitive to the candidate selection rule. No confidence interval should be treated as a real statistical result.',
    ]),
    ('Limitations and search checklist', [
        'This study has deliberate limitations: an invented corpus, a small sample, and no independently measured results. It exists to make PDF search and chat search easy to inspect.',
        'Try study in PDF search. Type s, st, stu, stud, and study slowly, then backspace. Only the current query should stay highlighted. Search for latency and move through results on different pages.',
        'Open Chat history and search benchmark to find several conversations. Search pineapple to find a word that appears only in a chat message. Search Moonstone to find a title-only match. Open a result to jump into that chat.',
    ]),
]

CHATS = [
    ('Demo - Start here: study overview', [
        ('What is this study about?', 'This is a **synthetic study** of Cedar, a fictional research assistant. It compares a baseline with sparse attention. All results are invented for search testing.'),
        ('What should I search for first?', 'Try **study** in the PDF with Ctrl/Cmd+F. Try **benchmark** in this chat with Ctrl/Cmd+Shift+F. Open History and use the same chat shortcut to search every saved conversation for this paper.'),
        ('Summarize the benchmark.', 'The benchmark has three invented workloads: short summaries, long context retrieval, and conflicting evidence. Compare recall and latency, then inspect the limitations.'),
        ('Where are the results?', 'Page 3 has the fictional results. The study reports 240 ms baseline latency and 150 ms sparse attention latency. These are demonstration values only.'),
    ]),
    ('Demo - Benchmark and retrieval notes', [
        ('How was the benchmark designed?', 'The benchmark reuses 120 fictional notes. The study compares full-library retrieval with a smaller sparse attention candidate set.'),
        ('Does faster always mean better?', 'No. Lower latency can come with lower recall. In the invented benchmark, recall changes from 92% to 89%. Inspect the missed evidence before choosing a configuration.'),
        ('What should I measure next?', 'For this fictional setup, record median latency, tail latency, retrieval recall, and answer faithfulness. Repeat the benchmark at each context length.'),
        ('Give me a one-line takeaway.', 'The benchmark illustrates a speed-versus-coverage tradeoff; it does not establish a real research finding.'),
    ]),
    ('Demo - Results discussion', [
        ('What changed in the results?', 'In the synthetic study, latency falls from 240 ms to 150 ms. That is a reduction of 90 ms, or 37.5%.'),
        ('Why does recall fall?', 'The sparse attention candidate set excludes some useful notes. This fictional recall loss is a reminder to check coverage as well as latency.'),
        ('Can I cite these numbers?', 'No. This PDF and its chats are fake examples created to test search. Use a real paper and independently measured results for research.'),
        ('What is the benchmark caveat?', 'The benchmark uses an invented corpus and no real repetitions. Its apparent latency gain has no scientific evidential value.'),
    ]),
    ('Demo - Odd-word message test', [
        ('Give me a memorable search marker.', 'The secret search marker is **pineapple**. It appears inside this saved answer, so history search must inspect message content to find it.'),
        ('Repeat that marker in a sentence.', 'A pineapple is our fictional bookmark for this conversation. Search pineapple here to test two separate answer matches.'),
        ('What does this conversation demonstrate?', 'It demonstrates content search across saved messages even when the conversation title does not contain your query.'),
    ]),
    ('Demo - Moonstone planning', [
        ('What should we do next?', 'Read the method carefully, write down assumptions, and compare the fictional results with the limitations.'),
        ('Make a review checklist.', '- Check candidate selection.\n- Compare recall.\n- Inspect missed evidence.\n- Keep fabricated examples separate from real research.'),
        ('How should we organize the review?', 'Keep one conversation for method questions and another for results. Use the history search to switch between them.'),
    ]),
    ('Demo - Long reading-group conversation', [
        (f'Reading group question {i}: {topic}?', answer) for i, (topic, answer) in enumerate([
            ('purpose', 'The study is an invented example for testing reader and conversation search.'),
            ('inputs', 'The fictional corpus contains 120 notes with titles, summaries, and source tags.'),
            ('baseline', 'The baseline searches all notes before forming an answer.'),
            ('sparse attention', 'Sparse attention narrows the candidate set before retrieval.'),
            ('benchmark', 'The benchmark includes summaries, retrieval, and conflicting evidence.'),
            ('latency', 'The invented latency values are 240 ms and 150 ms. Neither is a real measurement.'),
            ('recall', 'The fabricated recall values are 92 percent and 89 percent.'),
            ('limitations', 'The corpus, measurements, and conclusions are all synthetic.'),
            ('follow-up study', 'A real study would need a documented dataset and repeated measurements.'),
            ('tail latency', 'Tail latency would describe slow cases, rather than only the median.'),
            ('review process', 'Each reviewer should note assumptions before discussing conclusions.'),
            ('final decision', 'The final hidden marker is quorum. Search quorum to jump to this last answer after scrolling through a long chat.'),
        ], 1)
    ]),
]


def create_pdf(path):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='DemoTitle', fontName='Helvetica-Bold', fontSize=27, leading=33, textColor=colors.HexColor('#294936'), spaceAfter=24, alignment=TA_LEFT))
    styles['BodyText'].fontSize = 11
    styles['BodyText'].leading = 17
    styles['BodyText'].spaceAfter = 18
    story = []
    for index, (heading, paragraphs) in enumerate(SECTIONS):
        if index:
            story.append(PageBreak())
        story.extend([Paragraph('ADAM / SEARCH PLAYGROUND / FABRICATED EXAMPLE', styles['Heading5']), Spacer(1, 20), Paragraph(heading, styles['DemoTitle'])])
        for paragraph in paragraphs:
            story.append(Paragraph(paragraph, styles['BodyText']))
        if index == 2:
            table = Table([['Fictional variant', 'Latency', 'Recall'], ['Baseline', '240 ms', '92%'], ['Sparse attention', '150 ms', '89%']], colWidths=[230, 100, 100])
            table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e6eee5')), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12), ('LINEBELOW', (0, 0), (-1, -1), .5, colors.HexColor('#d6dfd5'))]))
            story.extend([Spacer(1, 12), table])
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#6d776e'))
        canvas.drawString(54, 32, 'Synthetic demo only. No real research claims.')
        canvas.drawRightString(558, 32, f'{doc.page} / 4')
    SimpleDocTemplate(str(path), pagesize=(612, 792), topMargin=52, bottomMargin=55, leftMargin=54, rightMargin=54, title=TITLE, author='Adam search demo').build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base', required=True)
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    if not args.api_base.startswith(('http://127.0.0.1:', 'http://localhost:')):
        parser.error('Use an isolated local preview backend.')
    os.environ['ADAM_DATA_DIR'] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(ROOT / 'backend'))
    from adam_server.database import SessionLocal
    from adam_server.models import Document, Conversation, Message
    from sqlalchemy import select
    output = ROOT / 'output/pdf/search-playground.pdf'
    output.parent.mkdir(parents=True, exist_ok=True)
    create_pdf(output)
    base = args.api_base.rstrip('/')
    response = requests.get(base + '/api/documents', timeout=20)
    response.raise_for_status()
    paper = next((item for item in response.json() if item['original_name'] == TITLE + '.pdf'), None)
    if not paper:
        with output.open('rb') as file:
            response = requests.post(base + '/api/documents', files={'file': (TITLE + '.pdf', file, 'application/pdf')}, timeout=30)
        response.raise_for_status()
        paper = response.json()
    with SessionLocal() as db:
        if not db.get(Document, paper['id']):
            raise RuntimeError('--data-dir does not match the selected preview API; no chats were written.')
        existing = {item.title: item for item in db.scalars(select(Conversation).where(Conversation.document_id == paper['id']))}
        now = datetime.now(timezone.utc)
        manifest = []
        for index, (title, turns) in enumerate(CHATS):
            if title in existing:
                manifest.append({'id': existing[title].id, 'title': title, 'exchanges': len(turns)})
                continue
            timestamp = now - timedelta(hours=index)
            conversation = Conversation(document_id=paper['id'], title=title, provider='zen', model_id='synthetic-demo', system_prompt='Fabricated fixture for search testing.', created_at=timestamp, updated_at=timestamp)
            db.add(conversation)
            db.flush()
            for turn, (question, answer) in enumerate(turns):
                for role, content, offset in [('user', question, 0), ('assistant', answer, 1)]:
                    db.add(Message(document_id=paper['id'], conversation_id=conversation.id, role=role, content=content, context_json=None, created_at=timestamp + timedelta(seconds=turn * 2 + offset)))
            manifest.append({'id': conversation.id, 'title': title, 'exchanges': len(turns)})
        db.commit()
    print(json.dumps({'paper': paper['id'], 'pdf': str(output), 'chats': manifest}, indent=2))


if __name__ == '__main__':
    main()
