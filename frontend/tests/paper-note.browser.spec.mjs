import { test, expect } from '@playwright/test';

test.use({ launchOptions: { channel: 'chrome' }, viewport: { width: 1440, height: 1000 } });
const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
const base = process.env.ADAM_TEST_BASE_URL;
test.skip(!base, 'Set ADAM_TEST_BASE_URL to an isolated, seeded review container.');

for (const [platform, modifierKey] of [['MacIntel', 'Meta'], ['Win32', 'Control']]) {
  test(`${platform}: zoom shortcuts follow the clicked or keyboard-focused panel`, async ({ page, request }) => {
    await page.addInitScript(platform => Object.defineProperty(navigator, 'platform', { value: platform }), platform);
    const papers = await (await request.get(`${base}/api/documents`)).json();
    const paper = papers.find(p => p.original_name === 'Understanding Protein Structure Ensembles.pdf');
    await page.goto(base);
    await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
    await page.locator('.react-pdf__Page__textContent span').first().waitFor();
    await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
    const editor = page.getByRole('textbox', { name: 'Paper note', exact: true });
    await expect(editor).toHaveAttribute('contenteditable', 'true');
    await editor.locator('p').first().click();
    const html = await editor.innerHTML();
    await page.keyboard.press(`${modifierKey}+Minus`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 90%/ })).toBeVisible();
    await page.keyboard.press(`${modifierKey}+Equal`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 100%/ })).toBeVisible();
    await page.keyboard.press(`${modifierKey}+Shift+Equal`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 110%/ })).toBeVisible();
    expect(await editor.innerHTML()).toBe(html);
    const pdfZoom = page.getByRole('button', { name: /^Paper zoom percentage:/ });
    const initialPdfZoom = await pdfZoom.getAttribute('aria-label');
    // Clicking non-focusable PDF content must override a retained editor caret.
    await page.locator('.pdf-pane').click({ position: { x: 10, y: 100 } });
    await page.keyboard.press(`${modifierKey}+Equal`);
    await expect(pdfZoom).not.toHaveAttribute('aria-label', initialPdfZoom);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 110%/ })).toBeVisible();
    const enlargedPdfZoom = await pdfZoom.getAttribute('aria-label');
    await page.keyboard.press(`${modifierKey}+Minus`);
    await expect(pdfZoom).not.toHaveAttribute('aria-label', enlargedPdfZoom);
    await page.locator('.mode-tabs').getByRole('button', { name: 'Chat', exact: true }).click();
    await page.locator('.chat-heading strong').click();
    await page.keyboard.press(`${modifierKey}+Equal`);
    await expect(page.getByRole('button', { name: /^Text zoom percentage: 135%/ })).toBeVisible();
    await page.keyboard.press(`${modifierKey}+Minus`);
    await expect(page.getByRole('button', { name: /^Text zoom percentage: 125%/ })).toBeVisible();
    const draft = page.locator('.composer textarea');
    await expect(draft).toBeEnabled();
    await draft.focus();
    const draftText = await draft.inputValue();
    await page.keyboard.press(`${modifierKey}+Equal`);
    await expect(page.getByRole('button', { name: /^Text zoom percentage: 135%/ })).toBeVisible();
    await expect(draft).toHaveValue(draftText);
    // Programmatic focus represents keyboard navigation into pane controls.
    await page.getByRole('button', { name: 'Notes', exact: true }).focus();
    await page.keyboard.press('Enter');
    await editor.focus();
    await page.keyboard.press(`${modifierKey}+Minus`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 100%/ })).toBeVisible();
    // Both zoom limits consume the shortcut without falling through to page zoom.
    await page.evaluate(() => {
      window.zoomKeys = [];
      window.addEventListener('keydown', event => {
        if (event.key === '-' || event.key === '=' || event.key === '+') window.zoomKeys.push(event.defaultPrevented);
      }, true);
    });
    await page.getByRole('button', { name: /^Note zoom percentage:/ }).click();
    await page.getByRole('textbox', { name: 'Note zoom percentage', exact: true }).fill('50');
    await page.keyboard.press('Enter');
    await page.keyboard.press(`${modifierKey}+Minus`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 50%/ })).toBeVisible();
    await page.getByRole('button', { name: /^Note zoom percentage:/ }).click();
    await page.getByRole('textbox', { name: 'Note zoom percentage', exact: true }).fill('200');
    await page.keyboard.press('Enter');
    await page.keyboard.press(`${modifierKey}+Equal`);
    await expect(page.getByRole('button', { name: /^Note zoom percentage: 200%/ })).toBeVisible();
    expect(await page.evaluate(() => window.zoomKeys)).toEqual([true, true]);
  });
}

test('sidebar controls remain visible at narrow pane widths and on mobile', async ({ page, request }, testInfo) => {
  const papers = await (await request.get(`${base}/api/documents`)).json();
  const paper = papers.find(p => p.original_name === 'Understanding Protein Structure Ensembles.pdf');
  await page.goto(base);
  await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
  await page.locator('.mode-tabs').getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Paper note', exact: true })).toHaveAttribute('contenteditable', 'true');
  async function assertControlsFit() {
    const overflow = await page.locator('.side-pane').evaluate(pane => {
      const bounds = pane.getBoundingClientRect();
      const buttons = [...pane.querySelectorAll('.mode-tabs button, .paper-note-toolbar button')];
      return buttons.filter(button => {
        const rect = button.getBoundingClientRect();
        return rect.width && (rect.left < bounds.left || rect.right > bounds.right || rect.top < bounds.top || rect.bottom > bounds.bottom);
      }).map(button => button.getAttribute('aria-label') || button.title || button.textContent);
    });
    expect(overflow).toEqual([]);
    await expect(page.getByRole('button', { name: 'Increase note text size', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Redo', exact: true })).toBeVisible();
    const overlaps = await page.locator('.paper-note-toolbar').evaluate(toolbar => {
      const rects = [...toolbar.querySelectorAll('button')].map(button => button.getBoundingClientRect());
      return rects.some((a, i) => rects.slice(i + 1).some(b => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom));
    });
    expect(overlaps).toBe(false);
  }
  for (const width of [340, 400, 440, 480]) {
    await page.locator('.reader-workspace').evaluate((workspace, width) => { workspace.style.gridTemplateColumns = `minmax(0, 1fr) ${width}px`; }, width);
    await assertControlsFit();
  }
  await page.screenshot({ path: testInfo.outputPath('notes-narrow-sidebar.png'), fullPage: true });
  await page.setViewportSize({ width: 375, height: 900 });
  await assertControlsFit();
  await page.screenshot({ path: testInfo.outputPath('notes-mobile.png'), fullPage: true });
});

test('note zoom scales all content, validates percentages, and stays independent of chat and PDF', async ({ page, request }, testInfo) => {
  const papers = await (await request.get(`${base}/api/documents`)).json();
  const paper = papers.find(p => p.original_name === 'Understanding Protein Structure Ensembles.pdf');
  const endpoint = `${base}/api/documents/${paper.id}/paper-note`;
  const previous = await (await request.get(endpoint)).json();
  await request.put(endpoint, { data: { content_html: '<h1>Notes</h1><p>Ideas to explore at a comfortable reading size.</p><blockquote data-source-quote="true" data-page="1"><p>A synthetic source quote.</p><cite>Page 1</cite></blockquote><p>Next steps</p>', plain_text: 'Research notes', revision: previous.revision } });
  await page.goto(base);
  await page.getByRole('button', { name: `Open ${paper.original_name}`, exact: true }).click();
  const tabs = page.locator('.mode-tabs');
  await tabs.getByRole('button', { name: 'Notes', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Paper note', exact: true });
  await expect(editor).toHaveAttribute('contenteditable', 'true');
  const saved = await (await request.get(endpoint)).json();
  const html = await editor.innerHTML();
  const paperZoom = await page.getByRole('button', { name: /^Paper zoom percentage:/ }).getAttribute('aria-label');
  const headingHeight = await editor.locator('h1').evaluate(el => el.getBoundingClientRect().height);
  await page.getByRole('button', { name: 'Decrease note text size', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Note zoom percentage: 90%/ })).toBeVisible();
  await page.getByRole('button', { name: /^Note zoom percentage:/ }).click();
  const input = page.getByRole('textbox', { name: 'Note zoom percentage', exact: true });
  await input.fill('49'); await input.press('Enter');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await input.fill('50%'); await input.press('Enter');
  await expect(page.getByRole('button', { name: 'Decrease note text size', exact: true })).toBeDisabled();
  expect(await editor.locator('h1').evaluate(el => el.getBoundingClientRect().height)).toBeCloseTo(headingHeight * .5, 0);
  await page.getByRole('button', { name: 'Increase note text size', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Note zoom percentage: 60%/ })).toBeVisible();
  await page.getByRole('button', { name: /^Note zoom percentage:/ }).click();
  await input.fill('201'); await input.press('Enter');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await input.fill('200%'); await input.press('Enter');
  await expect(page.getByRole('button', { name: 'Increase note text size', exact: true })).toBeDisabled();
  expect(await editor.locator('h1').evaluate(el => el.getBoundingClientRect().height)).toBeCloseTo(headingHeight * 2, 0);
  expect(await editor.innerHTML()).toBe(html);
  expect((await (await request.get(endpoint)).json()).revision).toBe(saved.revision);
  await expect(page.getByRole('button', { name: /^Paper zoom percentage:/ })).toHaveAttribute('aria-label', paperZoom);
  await tabs.getByRole('button', { name: 'Chat', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Text zoom percentage: 125%/ })).toBeVisible();
  await tabs.getByRole('button', { name: 'Notes', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Note zoom percentage: 200%/ })).toBeVisible();
  await page.getByRole('button', { name: 'Decrease note text size', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Note zoom percentage: 190%/ })).toBeVisible();
  await page.getByRole('button', { name: /^Note zoom percentage:/ }).click();
  await input.fill('150'); await input.press('Escape');
  await expect(page.getByRole('button', { name: /^Note zoom percentage: 190%/ })).toBeVisible();
  await editor.locator(':scope > p').last().click();
  await page.keyboard.press('End'); await page.keyboard.type(': check the evidence.');
  await expect(page.locator('.paper-note-status')).toHaveText('Saved');
  await expect.poll(async () => (await (await request.get(endpoint)).json()).plain_text).toContain('check the evidence');
  await page.locator('.paper-note-canvas').evaluate(el => { el.scrollTop = 0; });
  await page.screenshot({ path: testInfo.outputPath('notes-zoom.png'), fullPage: true });
});

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
