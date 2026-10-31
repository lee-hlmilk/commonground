// Ad-hoc visual check: node scripts/shot.cjs [preset] ??opens the recovery room and captures camera presets.
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const out = path.resolve(__dirname, '../../tmp/screenshots');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
    ...(process.env.CHROME_EXECUTABLE ? {executablePath: process.env.CHROME_EXECUTABLE} : {})});
  const page = await browser.newPage({viewport: {width: 1600, height: 960}});
  page.on('pageerror', e => console.log('pageerror', e.message, '\n', (e.stack || '').split('\n').slice(0, 12).join('\n')));
  await page.goto(process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8000', {waitUntil: 'networkidle'});
  await page.getByRole('button', {name: 'Recovery room', exact: true}).click();
  await page.locator('.timeline').waitFor();
  if (process.env.REPLAY) {
    await page.getByRole('button', {name: 'Illustrative replay', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('article.msg').length === 10, undefined, {timeout: 30000});
  }
  const presets = (process.argv[2] || 'overview').split(',');
  const at = Number(process.env.AT ?? -1);
  for (const p of presets) {
    await page.locator('.camera-bar button', {hasText: p}).click();
    await page.waitForTimeout(2600);
    if (at >= 0) {
      const box = await page.locator('.tl-track').boundingBox();
      await page.mouse.click(box.x + box.width * at, box.y + 40);
      await page.waitForTimeout(600);
    }
    await page.screenshot({path: path.join(out, `shot-${p}.png`)});
  }
  await browser.close();
})();
