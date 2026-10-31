// Browser smoke test against the locally running backend, which serves dist/.
// PLAYWRIGHT_MODULE defaults to playwright-core; CHROME_EXECUTABLE points at an installed Chrome/Edge.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const screenshotDir = path.resolve(__dirname, '../../tmp/screenshots');
fs.mkdirSync(screenshotDir, {recursive: true});
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const shot = (page, name) => page.screenshot({path: path.join(screenshotDir, `${name}.png`)});

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
    ...(process.env.CHROME_EXECUTABLE ? {executablePath: process.env.CHROME_EXECUTABLE} : {})});
  const page = await browser.newPage({viewport: {width: 1600, height: 960}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.type() === 'error') errors.push(`console: ${msg.text()}`); });
  try {
    await page.goto(process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8000', {waitUntil: 'networkidle'});
    await page.getByRole('heading', {name: /Every delay has a/}).waitFor();
    await page.locator('canvas').first().waitFor();
    await page.locator('.top-list button').first().waitFor({timeout: 30000});
    await page.waitForTimeout(2500);
    await shot(page, '01-globe');

    await page.locator('.top-list button', {hasText: 'ATL'}).click();
    await page.getByRole('complementary', {name: 'Airport ATL'}).waitFor();
    await page.locator('.scenario-card').first().waitFor();
    await page.locator('.date-chips button').first().waitFor({timeout: 20000});
    await page.locator('.date-chips button').first().click();
    await page.locator('.flight-pick label').first().waitFor({timeout: 20000});
    await page.locator('.flight-pick input').first().check();
    await page.locator('.build-bar').waitFor();
    await page.waitForTimeout(1500);
    await shot(page, '02-globe-airport');

    await page.locator('.scenario-card', {hasText: 'Three arrivals, one recovery plan'}).click();
    await page.locator('.timeline').waitFor();
    await page.getByText('No coordination run yet', {exact: false}).waitFor();
    await page.waitForTimeout(2000);
    await shot(page, '03-ops-empty');

    await page.getByRole('button', {name: 'Illustrative replay', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('article.msg').length === 10, undefined, {timeout: 30000});
    await page.getByText('Your decision needed', {exact: true}).waitFor();
    await page.waitForTimeout(3500);
    await shot(page, '04-ops-replay');

    const track = await page.locator('.tl-track').boundingBox();
    await page.mouse.click(track.x + track.width * 0.22, track.y + 40);
    await page.waitForTimeout(800);
    await shot(page, '05-ops-t22');
    await page.mouse.click(track.x + track.width * 0.38, track.y + 40);
    await page.locator('.tl-chip').first().click();
    await page.locator('.flight-card').waitFor();
    await page.waitForTimeout(1800);
    await shot(page, '06-ops-follow');
    await page.getByRole('button', {name: 'Close flight'}).click();

    await page.getByRole('button', {name: 'Fixed rule (FCFS)'}).click();
    await page.getByText('Previewing fixed-rule baseline', {exact: false}).waitFor();
    await page.getByRole('button', {name: 'Recommended', exact: true}).click();
    await page.getByRole('button', {name: 'Options'}).click();
    assert.ok(await page.locator('.cand').count() >= 2, 'candidate options listed');
    await page.getByRole('button', {name: /^Checks/}).click();
    await page.getByText(/independent checks pass/).first().waitFor();

    await page.getByRole('button', {name: 'Approve plan', exact: true}).click();
    await page.getByText('Plan approved', {exact: true}).waitFor();
    await page.getByRole('button', {name: /Block stand/}).click();
    await page.getByText('Episode 2', {exact: false}).waitFor();
    await page.waitForFunction(() => !document.querySelector('.run-banner.negotiating'), undefined, {timeout: 30000});

    await page.getByRole('button', {name: 'Evidence & assumptions', exact: true}).click();
    await page.getByRole('dialog', {name: 'Evidence and assumptions'}).waitFor();
    await page.getByRole('heading', {name: 'Simulation assumptions', exact: true}).waitFor();
    await page.getByRole('button', {name: 'Close panel', exact: true}).click();

    await page.getByRole('combobox', {name: 'Coordination scenario'}).selectOption('missing-approval');
    await page.getByRole('heading', {name: 'Remote stand awaiting approval'}).waitFor();
    await page.getByRole('button', {name: 'Illustrative replay', exact: true}).click();
    await page.waitForFunction(() => !!document.querySelector('.run-banner.unresolved, .run-banner.fallback'), undefined, {timeout: 30000});
    assert.equal(await page.getByRole('button', {name: 'Approve plan', exact: true}).isEnabled().catch(() => false), false);
    await page.waitForTimeout(1500);
    await shot(page, '07-ops-missing-approval');
    await page.getByRole('button', {name: /Confirm Airport operations approval/}).click();
    await page.waitForFunction(() => !!document.querySelector('.run-banner.awaiting_approval'), undefined, {timeout: 30000});

    await page.getByRole('combobox', {name: 'Replay source'}).selectOption({index: 1});
    await page.getByRole('button', {name: 'Recorded replay', exact: true}).click();
    await page.getByText('Recorded replay · read only', {exact: true}).waitFor();
    assert.equal(await page.getByRole('button', {name: 'Approve plan', exact: true}).isEnabled().catch(() => false), false);
    assert.equal(await page.getByRole('button', {name: /Block stand/}).isEnabled(), false);

    await page.getByRole('combobox', {name: 'Replay source'}).selectOption('');
    await page.getByRole('combobox', {name: 'Coordination scenario'}).selectOption('infeasible');
    await page.getByRole('button', {name: 'Illustrative replay', exact: true}).click();
    await page.waitForFunction(() => !!document.querySelector('.run-banner.unresolved, .run-banner.fallback'), undefined, {timeout: 30000});
    await page.waitForTimeout(1500);
    await shot(page, '08-ops-infeasible');

    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(800);
    await page.screenshot({path: path.join(screenshotDir, '09-mobile.png'), fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, 'Mobile page overflows horizontally');
    assert.deepEqual(errors, [], 'Browser runtime errors');
    console.log('PASS: globe, airport drill-down, recovery room, 10-message replay, timeline scrub, follow camera, FCFS preview, options, checks, approval, disruption, evidence, missing approval, recorded replay, infeasible, mobile; no runtime errors.');
  } catch (e) {
    await shot(page, 'zz-failure').catch(() => {});
    console.error('Errors so far:', errors);
    throw e;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
