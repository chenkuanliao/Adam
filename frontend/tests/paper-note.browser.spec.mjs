import { test, expect } from '@playwright/test';

test.use({ launchOptions: { channel: 'chrome' }, viewport: { width: 1440, height: 1000 } });
const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
const base = process.env.ADAM_TEST_BASE_URL;
test.skip(!base, 'Set ADAM_TEST_BASE_URL to an isolated, seeded review container.');

test('PDF quote shortcut, cursor placement, protected text, dragging, clipboard and reload', async ({ page, request, context }, testInfo) => {
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  const papers = await (await request.get(`${base}/api/documents`)).json();
  const paper = papers.find(p => p.original_name === 'Sparse Attention for Long-Context Retrieval.pdf');
  expect(paper).toBeTruthy();
  const endpoint = `${base}/api/documents/${paper.id}/paper-note`;
  const note = await (await request.get(endpoint)).json();
  await request.put(endpoint, { data: { content_html: '<p>Before after</p><p>Last paragraph</p>', plain_text: 'Before after\nLast paragraph', revision: note.revision } });
  await page.goto(base);
  await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
  await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Paper note', exact: true });
  await expect(editor).toHaveAttribute('contenteditable', 'true');
  await editor.click();
  await editor.evaluate(el => {
    const range = document.createRange(); range.setStart(el.querySelector('p').firstChild, 7); range.collapse(true);
    window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
  });
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowLeft');
  await page.locator('.react-pdf__Page__textContent span').first().waitFor();
  await page.locator('.pdf-pane').click({ position: { x: 15, y: 100 } });
  const excerpt = await page.evaluate(() => {
    const el = [...document.querySelectorAll('.react-pdf__Page__textContent span')].find(el => el.textContent.includes('We study'));
    const range = document.createRange(); range.selectNodeContents(el);
    window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    return el.textContent;
  });
  await expect(page.getByRole('toolbar', { name: 'Text selection actions' })).toBeVisible();
  await page.keyboard.press('q');
  const quote = editor.locator('blockquote[data-source-quote]');
  await expect(quote).toHaveCount(1);
  await expect(quote.locator('p')).toHaveText(excerpt);
  await expect(quote).toHaveAttribute('contenteditable', 'false');
  expect(await editor.locator(':scope > p').allTextContents()).toEqual(['Before ', 'after', 'Last paragraph']);
  await page.getByRole('button', { name: 'Write above quote', exact: true }).click();
  await page.keyboard.type('My introduction');
  await page.getByRole('button', { name: 'Write below quote', exact: true }).click();
  await page.keyboard.type('My explanation');
  await expect(quote.locator('p')).toHaveText(excerpt);
  // Native HTML dragging uses ProseMirror's move transaction rather than copying.
  await quote.locator('.source-quote-handle').dragTo(editor.locator(':scope > p').last());
  await expect(quote).toHaveCount(1);
  await expect(quote.locator('p')).toHaveText(excerpt);
  expect(await quote.evaluate(el => el.previousElementSibling?.textContent)).not.toBe('My introduction');
  await page.getByTitle('Undo', { exact: true }).click();
  await expect(quote).toHaveCount(1);
  expect(await quote.evaluate(el => el.previousElementSibling?.textContent)).toBe('My introduction');
  // Real system clipboard carries the atomic quote and adjacent paragraphs.
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await editor.click(); await page.keyboard.press(`${modifier}+a`); await page.keyboard.press(`${modifier}+c`);
  const html = await page.evaluate(async () => {
    const items = await navigator.clipboard.read();
    return (await items[0].getType('text/html')).text();
  });
  expect(html).toContain('data-source-quote');
  expect(html).not.toContain('source-quote-tools');
  await page.keyboard.press('Backspace'); await page.keyboard.press(`${modifier}+v`);
  await expect(quote).toHaveCount(1); await expect(quote.locator('p')).toHaveText(excerpt);
  await expect(editor).toContainText('My introduction'); await expect(editor).toContainText('My explanation');
  await expect(page.locator('.paper-note-status')).toHaveText('Saved');
  await page.reload();
  await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(quote).toHaveCount(1); await expect(quote.locator('p')).toHaveText(excerpt);
  await expect(quote).toHaveAttribute('contenteditable', 'false');
  await expect(editor).toContainText('My introduction');
  // The saved quote retains its geometry across clipboard and reload.
  const stored = await (await request.get(endpoint)).json();
  expect(stored.content_html).toContain('data-source-rects');
  // Node views expose only UI, so inspect the serialized note for source geometry.
  expect(stored.content_html).toMatch(/data-source-rects="[^"]*left/);
  await page.getByRole('button', { name: /^Page number:/ }).click();
  await page.getByRole('textbox', { name: 'Page number', exact: true }).fill('7');
  await page.getByRole('textbox', { name: 'Page number', exact: true }).press('Enter');
  await quote.getByRole('button', { name: 'Show quote in PDF', exact: true }).click();
  await expect(page.locator('.linked-passage-toast')).toHaveCount(0);
  await expect(page.locator('.linked-passage-flash').first()).toBeVisible();
  await expect(page.locator('.linked-passage-flash')).toHaveCount(0, { timeout: 6000 });
  await quote.getByRole('button', { name: 'Show quote in PDF', exact: true }).click();
  await expect(page.locator('.linked-passage-flash').first()).toBeVisible();
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('paper-note-quotes.png'), fullPage: true });
});

test('first quote in an empty note allows typing and newlines above it and saves on pane switch', async ({ page, request }) => {
  const papers = await (await request.get(`${base}/api/documents`)).json();
  const paper = papers.find(p => p.original_name === 'Reliable Tool Use in Research Assistants.pdf');
  const endpoint = `${base}/api/documents/${paper.id}/paper-note`;
  const note = await (await request.get(endpoint)).json();
  await request.put(endpoint, { data: { content_html: '', plain_text: '', revision: note.revision } });
  await page.goto(base);
  await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
  await page.locator('.react-pdf__Page__textContent span').first().waitFor();
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('.react-pdf__Page__textContent span')].find(el => el.textContent.includes('We study'));
    const range = document.createRange(); range.selectNodeContents(el);
    window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  });
  await page.keyboard.press('q');
  const editor = page.getByRole('textbox', { name: 'Paper note', exact: true });
  const quote = editor.locator('blockquote[data-source-quote]');
  await expect(quote).toHaveCount(1);
  await editor.locator(':scope > p').first().click();
  await page.keyboard.type('Text above the first quote');
  await page.keyboard.press('Enter'); await page.keyboard.type('Another line above');
  await editor.locator(':scope > p').last().click(); await page.keyboard.type('Text below the quote');
  await page.locator('.mode-tabs').getByRole('button', { name: 'Chat', exact: true }).click();
  await expect.poll(async () => (await (await request.get(endpoint)).json()).plain_text).toContain('Text below the quote');
  await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(editor).toContainText('Text above the first quote');
  await expect(editor).toContainText('Another line above');
  await expect(editor).toContainText('Text below the quote');
  await expect(quote).toHaveCount(1);
  await expect(quote).toHaveAttribute('contenteditable', 'false');
});


test('legacy quote locates text on a distant page and fades without changing the note', async ({ page, request }) => {
  const papers = await (await request.get(`${base}/api/documents`)).json();
  const paper = papers.find(p => p.original_name === 'A Field Guide to Diffusion Samplers.pdf');
  const endpoint = `${base}/api/documents/${paper.id}/paper-note`;
  const note = await (await request.get(endpoint)).json();
  const html = '<p>Legacy note</p><blockquote><p>We study how a small set of explicit assumptions</p><cite>Quoted from paper · Page 8</cite></blockquote><p>Keep this writing</p>';
  await request.put(endpoint, { data: { content_html: html, plain_text: 'Legacy note', revision: note.revision } });
  await page.goto(base);
  await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
  await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Paper note', exact: true });
  await expect(editor).toContainText('Keep this writing');
  await page.locator('.react-pdf__Page__textContent span').first().waitFor();
  await editor.getByRole('button', { name: 'Show quote in PDF', exact: true }).click();
  await expect(page.locator('[data-page-number="8"] .linked-passage-flash').first()).toBeVisible();
  await expect(page.locator('.linked-passage-toast')).toHaveCount(0);
  await expect(page.locator('.linked-passage-flash').first()).toBeVisible();
  const visible = await page.locator('.linked-passage-flash').first().evaluate(el => {
    const rect = el.getBoundingClientRect(), pane = document.querySelector('.pdf-pane').getBoundingClientRect();
    return rect.top >= pane.top && rect.top < pane.bottom;
  });
  expect(visible).toBe(true);
  await expect(page.locator('.linked-passage-flash')).toHaveCount(0, { timeout: 6000 });
  expect((await (await request.get(endpoint)).json()).content_html).toBe(html);
});
