import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {resolve, extname} from 'node:path';
import {chromium} from '../desktop/node_modules/playwright/index.mjs';

const root = resolve('site');
const server = http.createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    if (!pathname.startsWith('/HoloQA/')) throw Error('Not found');
    const file = resolve(root, pathname.slice(8) || 'index.html');
    if (!file.startsWith(root + '/')) throw Error('Not found');
    const data = await readFile(file);
    res.setHeader('Content-Type', ({'.html':'text/html', '.css':'text/css', '.js':'text/javascript', '.png':'image/png', '.xml':'application/xml'})[extname(file)] || 'application/octet-stream');
    res.end(data);
  } catch { res.writeHead(404); res.end('Not found'); }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}/HoloQA/`;
const browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, args:['--no-sandbox']});
const errors = [];
const fallback = 'https://github.com/Ifarra/HoloQA/releases/download/desktop-v0.3.2/HoloQA-Desktop-0.3.2-windows-x64-setup.exe';
async function open(metadata = [], javascript = true) {
  const context = await browser.newContext({viewport:{width:1440,height:1000}, reducedMotion:'reduce', javaScriptEnabled:javascript, permissions: javascript ? ['clipboard-read','clipboard-write'] : []});
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.route('https://api.github.com/**', route => route.fulfill({status:metadata === null ? 429 : 200, contentType:'application/json', body:JSON.stringify(metadata)}));
  await page.goto(url);
  return page;
}
try {
  const page = await open();
  assert.equal(await page.locator('h1').count(), 1);
  assert.equal(await page.locator('[data-download]').first().getAttribute('href'), fallback);
  await page.locator('#view-paused').click();
  await page.locator('#studio-image').evaluate(img => img.decode());
  assert.match(await page.locator('#studio-image').getAttribute('src'), /paused/);
  await page.locator('#view-paused').press('Home');
  assert.equal(await page.locator('#view-overview').getAttribute('aria-selected'), 'true');
  await page.locator('.expand-view').click();
  assert.equal(await page.locator('dialog').evaluate(d => d.open), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog').evaluate(d => d.open), false);
  for (const step of ['project','plan','agent','evidence']) {
    await page.locator(`#step-${step}`).click();
    assert.equal(await page.locator('.tour-scene:visible').count(), 1);
    assert.equal(await page.locator(`#tour-${step}`).isVisible(), true);
  }
  await page.locator('#step-evidence').press('Home');
  await page.locator('#step-project').press('ArrowDown');
  assert.equal(await page.locator('#step-plan').getAttribute('aria-selected'), 'true');
  await page.locator('.copy-command').click();
  await page.getByRole('status').filter({hasText:'Setup commands copied.'}).waitFor();
  assert.match(await page.evaluate(() => navigator.clipboard.readText()), /agent-browser install/);
  await page.locator('summary').first().click();
  assert.equal(await page.locator('details').first().getAttribute('open'), '');
  for (const width of [360,390,768,1024,1440,1920]) {
    await page.setViewportSize({width, height:1000});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Horizontal overflow at ${width}px`);
    if (width < 800) {
      await page.locator('.menu-toggle').click();
      assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'), 'true');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.menu-toggle').getAttribute('aria-expanded'), 'false');
    }
  }
  for (const image of await page.locator('img').all()) assert.equal(await image.evaluate(img => img.complete && img.naturalWidth > 0), true);
  if (process.env.HOLOQA_PAGES_SCREENSHOTS) {
    await mkdir(process.env.HOLOQA_PAGES_SCREENSHOTS,{recursive:true});
    await page.locator('#step-project').click();
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:resolve(process.env.HOLOQA_PAGES_SCREENSHOTS,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:resolve(process.env.HOLOQA_PAGES_SCREENSHOTS,'mobile.png'),fullPage:true});
  }
  await page.context().close();
  const futureUrl = 'https://github.com/Ifarra/HoloQA/releases/download/desktop-v0.1.9/HoloQA-Desktop-0.1.9-windows-x64-setup.exe';
  const release = {tag_name:'desktop-v0.1.9',prerelease:true,assets:[{name:'HoloQA-Desktop-0.1.9-windows-x64-setup.exe',browser_download_url:futureUrl}]};
  const future = await open([release]);
  await future.waitForFunction(href => document.querySelector('[data-download]').href === href, futureUrl);
  assert.equal(await future.locator('[data-version]').first().textContent(), 'v0.1.9 Preview');
  await future.context().close();
  for (const metadata of [null,[{...release,assets:[{...release.assets[0],browser_download_url:'https://example.com/untrusted.exe'}]}]]) {
    const safe = await open(metadata);
    await safe.waitForLoadState('networkidle');
    assert.equal(await safe.locator('[data-download]').first().getAttribute('href'), fallback);
    await safe.context().close();
  }
  const nojs = await open([], false);
  assert.equal(await nojs.locator('[data-download]').first().getAttribute('href'), fallback);
  await nojs.context().close();
  assert.deepEqual(errors, []);
  console.log('PASS: six viewport sizes, project subpath, images, keyboard tabs, dialog, navigation, clipboard, FAQ, release refresh, rate-limit and untrusted URL fallback, no-JS download.');
} finally {
  await browser.close();
  await new Promise(r => server.close(r));
}
