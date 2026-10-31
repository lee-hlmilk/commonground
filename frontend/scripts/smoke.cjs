// Browser smoke test against the locally running backend, which serves dist/.
// Uses the bundled Playwright installation when PLAYWRIGHT_MODULE is provided.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const screenshotDir = path.resolve(__dirname, '../../tmp/screenshots');
fs.mkdirSync(screenshotDir, {recursive:true});
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_EXECUTABLE ? {executablePath: process.env.CHROME_EXECUTABLE} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  try {
    await page.goto(process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8000', {waitUntil:'networkidle'});
    await page.getByRole('heading',{name:'A disruption. Every perspective. A way forward, together.'}).waitFor();
    await page.locator('.participant-card').last().waitFor();
    assert.equal(await page.locator('.participant-card').count(),5);
    await page.screenshot({path:path.join(screenshotDir,'frontend-initial.png'),fullPage:true});
    await page.getByRole('button',{name:'Historical reference',exact:true}).click();
    await page.getByRole('cell',{name:'10:45',exact:true}).waitFor();
    await page.getByRole('button',{name:'Simulated recovery',exact:true}).click();
    await page.getByRole('button',{name:'Illustrative replay',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('article.message').length===10,undefined,{timeout:30000});
    await page.getByText('Your decision needed',{exact:true}).waitFor();
    await page.screenshot({path:path.join(screenshotDir,'frontend-replay.png'),fullPage:true});
    await page.getByRole('button',{name:'Approve plan',exact:true}).click();
    await page.getByText('Plan approved',{exact:true}).waitFor();
    await page.getByRole('button',{name:/Block stand until/}).click();
    await page.getByText('Episode 2',{exact:false}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('.run-banner.negotiating'),undefined,{timeout:30000});
    await page.getByRole('button',{name:'Evidence & assumptions',exact:true}).click();
    await page.getByRole('dialog',{name:'Evidence and assumptions'}).waitFor();
    await page.getByRole('heading',{name:'Simulation assumptions',exact:true}).waitFor();
    await page.getByRole('button',{name:'Close panel',exact:true}).click();
    await page.getByRole('combobox',{name:'Coordination scenario'}).selectOption('missing-approval');
    await page.getByText('Missing approval',{exact:false}).count();
    await page.getByRole('button',{name:'Illustrative replay',exact:true}).click();
    await page.waitForFunction(()=>!!document.querySelector('.run-banner.unresolved, .run-banner.fallback'),undefined,{timeout:30000});
    assert.equal(await page.getByRole('button',{name:'Approve plan',exact:true}).isEnabled().catch(()=>false),false);
    await page.getByRole('button',{name:/Confirm Airport operations approval/}).click();
    await page.waitForFunction(()=>!!document.querySelector('.run-banner.awaiting_approval'),undefined,{timeout:30000});
    await page.getByRole('combobox',{name:'Replay source'}).selectOption({index:1});
    await page.getByRole('button',{name:'Recorded replay',exact:true}).click();
    await page.getByText('RECORDED REPLAY · READ ONLY',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Approve plan',exact:true}).isEnabled(),false);
    assert.equal(await page.getByRole('button',{name:/Block stand until/}).isEnabled(),false);
    assert.ok(await page.getByRole('combobox',{name:'Coordination scenario'}).inputValue());
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(screenshotDir,'frontend-mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,'Mobile page overflows horizontally');
    assert.deepEqual(errors,[],'Browser runtime errors');
    console.log('PASS: scenario, 5 participants, history, 10-message replay, approval, disruption, evidence, missing approval, mobile layout, read-only recorded replay; no browser runtime errors.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
