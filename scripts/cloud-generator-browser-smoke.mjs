#!/usr/bin/env node

import { chromium } from 'playwright';

const siteBase = (process.env.PPT_MASTER_BROWSER_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const apiBase = (process.env.PPT_MASTER_BROWSER_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
const token = process.env.PPT_MASTER_BROWSER_TOKEN || '';
const fixture = process.env.PPT_MASTER_BROWSER_FIXTURE || '';

const query = new URLSearchParams({ api: apiBase, lang: 'en' });
if (token) query.set('token', token);

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${siteBase}/index.html?${query}`, { waitUntil: 'networkidle' });
  if (!(await page.locator('body').innerText()).includes('Best PPT')) throw new Error('landing page did not render');

  await page.goto(`${siteBase}/cloud-generator.html?${query}&guest=1`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  if (await page.locator('#routeSelect').count() !== 1) throw new Error('route selector missing');
  if (!(await page.locator('#routeCapabilityStatus').innerText()).includes('cloud')) throw new Error('capability status did not load');

  let nativeRouteEnabled = false;
  let templatePublished = false;
  let authoringEdited = false;
  if (fixture) {
    await page.locator('#fileInput').setInputFiles(fixture);
    await page.waitForTimeout(100);
    nativeRouteEnabled = await page.locator('#routeSelect option[value="create_template"]').isEnabled();
    if (!nativeRouteEnabled) throw new Error('native route was not enabled after PPTX upload');
    await page.locator('#routeSelect').selectOption('create_template');
    await page.locator('#promptInput').fill('Publish a reusable project template.');
    await page.locator('#generateBtn').click();
    try {
      await page.waitForSelector('#routeSessionModal.show', { timeout: 8000 });
    } catch (_error) {
      await page.waitForFunction(() => typeof pendingGeneration !== 'undefined' && pendingGeneration, null, { timeout: 15000 });
      await page.locator('#promptInput').fill('confirm');
      await page.locator('#generateBtn').click();
      await page.waitForSelector('#routeSessionModal.show', { timeout: 30000 });
    }
  } else {
    await page.evaluate(() => showModal('routeSessionModal'));
  }

  const dialog = page.locator('#routeSessionModal .modal');
  if (await dialog.getAttribute('aria-modal') !== 'true') throw new Error('modal aria-modal missing');
  if (!(await page.locator(':focus').evaluate(node => node.closest('#routeSessionModal') !== null))) throw new Error('modal did not receive focus');
  if (fixture) {
    await page.waitForSelector('#routeAuthoringCanvas > svg', { timeout: 30000 });
    const editable = page.locator('#routeAuthoringCanvas text[id], #routeAuthoringCanvas rect[id], #routeAuthoringCanvas path[id], #routeAuthoringCanvas g[id]').first();
    if (await editable.count()) {
      await editable.evaluate(node => node.dispatchEvent(new MouseEvent('click', { bubbles: true })));
      await page.locator('#routeAuthoringEditor').waitFor({ state: 'visible', timeout: 5000 });
      await page.locator('#routeAuthoringFill').fill('#123456');
      await Promise.all([
        page.waitForResponse(response => response.url().includes('authoring-history') && response.request().method() === 'GET', { timeout: 10000 }),
        page.locator('#routeAuthoringApplyBtn').click(),
      ]);
      await page.waitForTimeout(250);
      await page.waitForFunction(() => {
        const option = document.querySelector('#routeAuthoringHistory option');
        return option && option.value && !document.querySelector('#routeAuthoringRestoreBtn')?.disabled;
      }, null, { timeout: 5000 });
      await Promise.all([
        page.waitForResponse(response => response.url().includes('authoring-file') && response.request().method() === 'GET', { timeout: 10000 }),
        page.locator('#routeAuthoringRestoreBtn').click(),
      ]);
      await editable.evaluate(node => node.dispatchEvent(new MouseEvent('click', { bubbles: true })));
      await page.locator('#routeAuthoringEditor').waitFor({ state: 'visible', timeout: 5000 });
      await page.locator('#routeAuthoringFill').fill('#123456');
      await page.locator('#routeAuthoringApplyBtn').click();
      await Promise.all([
        page.waitForResponse(response => response.url().includes('authoring-file') && response.request().method() === 'GET', { timeout: 10000 }),
        page.locator('#routeAuthoringUndoBtn').click(),
      ]);
      await editable.evaluate(node => node.dispatchEvent(new MouseEvent('click', { bubbles: true })));
      await page.locator('#routeAuthoringEditor').waitFor({ state: 'visible', timeout: 5000 });
      await page.locator('#routeAuthoringFill').fill('#123456');
      await page.locator('#routeAuthoringApplyBtn').click();
      const objects = page.locator('#routeAuthoringCanvas g[id]');
      if (await objects.count() > 1) {
        await objects.nth(1).evaluate(node => node.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
        if (!(await page.locator('#routeAuthoringSelected').innerText()).includes('2 selected')) throw new Error('multi-selection did not register');
        await page.locator('[data-route-nudge="8,0"]').click();
        await page.locator('[data-route-align="left"]').click();
        if (await objects.count() > 2) {
          await objects.nth(2).evaluate(node => node.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
          await page.locator('[data-route-distribute="horizontal"]').click();
          await page.locator('[data-route-equalize="width"]').click();
        }
      }
      authoringEdited = true;
    }
    if (!authoringEdited) throw new Error('authoring SVG had no editable element');
    await page.locator('#templatePublishName').fill('Browser smoke template');
    await page.locator('#routeSessionPublishBtn').click();
    await page.waitForFunction(() => !document.querySelector('#routeSessionModal')?.classList.contains('show'), null, { timeout: 30000 });
    templatePublished = true;
  } else {
    await page.keyboard.press('Escape');
    if (await page.locator('#routeSessionModal').evaluate(node => node.classList.contains('show'))) throw new Error('Escape did not close modal');
  }

  console.log(JSON.stringify({ ok: true, landing: true, workbench: true, native_route_enabled: nativeRouteEnabled, modal_focus: true, authoring_edited: authoringEdited, template_published: templatePublished }));
} finally {
  await browser.close();
}
