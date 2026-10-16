"""Seed synthetic papers into an explicitly selected local review backend.

Run with mainenv: python scripts/seed-review-demo.py --api-base http://127.0.0.1:8414
No model credentials, external research papers, or user data are used.
"""
import argparse
import io
import json
import fitz
import requests

PAPERS = [
    ('Sparse Attention for Long-Context Retrieval', 'Machine learning', 7),
    ('Reliable Tool Use in Research Assistants', 'Machine learning', 6),
    ('Understanding Protein Structure Ensembles', 'Biology & science', 5),
    ('A Field Guide to Diffusion Samplers', 'Reading group', 8),
    ('Low-Rank Adaptation Under Distribution Shift', None, 6),
    ('Measuring Faithfulness in Scientific Summaries', 'Reading group', 5),
]
COLORS = {'Machine learning': '#5c8fca', 'Biology & science': '#4fa49a', 'Reading group': '#d77d59'}
SECTIONS = ['Introduction', 'Method and assumptions', 'Experimental design', 'Results and analysis', 'Ablation study', 'Limitations', 'Discussion', 'Appendix']
PARAGRAPH = (
    'We study how a small set of explicit assumptions can make a complex system easier to inspect. '
    'Our controlled examples separate the retrieval of relevant evidence from the generation of a final answer. '
    'Each experimental condition uses the same synthetic inputs and an independently varied evaluation setting. '
    'The central observation is that simple, well-calibrated decisions can improve consistency without increasing model size. '
    'These examples are fabricated for interface review; they are not scientific findings. '
)


def make_pdf(title, pages):
    pdf = fitz.open()
    for number in range(1, pages + 1):
        page = pdf.new_page(width=612, height=792)
        page.insert_text((48, 30), 'ADAM READING LAB  /  SYNTHETIC DEMO PAPER', fontsize=8, color=(.35,.39,.46))
        page.draw_line((48, 40), (564, 40), color=(.8,.8,.8), width=.5)
        if number == 1:
            page.insert_textbox(fitz.Rect(48, 68, 564, 170), title, fontname='tiro', fontsize=25, align=1)
            page.insert_textbox(fitz.Rect(48, 175, 564, 215), 'M. Chen, A. Rivera, and F. Mensah\nResearch Interface Workshop · October 2026', fontname='tiro', fontsize=11, align=1)
            page.insert_text((48, 244), 'Abstract', fontname='tibo', fontsize=12)
            page.insert_textbox(fitz.Rect(48, 254, 564, 350), PARAGRAPH, fontname='tiro', fontsize=10.5, lineheight=1.3)
            y = 360
        else:
            page.insert_textbox(fitz.Rect(48, 60, 564, 100), f'{number}. {SECTIONS[(number-2) % len(SECTIONS)]}', fontname='tibo', fontsize=19)
            y = 115
        for x in (48, 312):
            page.insert_textbox(fitz.Rect(x, y, x+246, y + (185 if number == 1 else 340)), PARAGRAPH * (1 if number == 1 else 2), fontname='tiro', fontsize=10.5, lineheight=1.3)
        fy = 560 if number == 1 else 475
        page.draw_rect(fitz.Rect(48, fy, 564, fy+155), color=(.84,.86,.89), fill=(.97,.97,.98))
        for i, height in enumerate((40,67,83,100,113)):
            left = 100+i*84
            page.draw_rect(fitz.Rect(left, fy+130-height, left+40, fy+130), fill=(.35+i*.035,.45+i*.035,.68+i*.025), color=None)
        page.insert_text((60, fy+148), 'Figure 1. Illustrative measurements only; all values and findings are fabricated.', fontsize=8)
        page.insert_text((48, 754), 'Synthetic content for Adam UI review. No real research claims.', fontsize=8, color=(.5,.5,.5))
        page.insert_text((548, 754), str(number), fontsize=10)
    return pdf.tobytes()


def seed(base):
    session = requests.Session()
    def api(method, path, **kwargs):
        result = session.request(method, base + '/api/' + path, timeout=60, **kwargs)
        result.raise_for_status()
        return result.json() if result.content else None
    existing = {item['original_name']: item for item in api('GET', 'documents')}
    folders = {item['name']: item for item in api('GET', 'folders')}
    for name, color in COLORS.items():
        if name not in folders:
            folders[name] = api('POST', 'folders', json={'name': name, 'color': color})
    manifest = []
    for title, folder, count in PAPERS:
        filename = title + '.pdf'
        paper = existing.get(filename)
        if not paper:
            paper = api('POST', 'documents', files={'file': (filename, io.BytesIO(make_pdf(title, count)), 'application/pdf')})
            if folder:
                api('POST', 'document-moves', json={'document_ids': [paper['id']], 'folder_id': folders[folder]['id']})
        manifest.append({'id': paper['id'], 'title': title, 'pages': count})
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base', required=True)
    args = parser.parse_args()
    if not args.api_base.startswith(('http://127.0.0.1:', 'http://localhost:')):
        parser.error('Use a local isolated review server.')
    seed(args.api_base.rstrip('/'))
