// UI-only provider policy smoke. All live-run requests are intercepted; no model calls.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROME_EXECUTABLE?{executablePath:process.env.CHROME_EXECUTABLE}:{})});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(15000);
  const base = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8000';
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  let interceptedCalls=0;
  try {
    const scenario = await (await page.request.get(base+'/api/scenarios/atl-three-flight-clash')).json();
    const participants=[{id:'atc',role:'atc',name:'ATC fixture'},{id:'pilot',role:'pilot',name:'Captain fixture'}];
    const common={recipients:[],type:'PROPOSE',round:1,scenario_version:1,evidence_refs:[]};
    const run={id:'jev-ui-fixture',status:'awaiting_approval',mode:'live',provider:'jev',provider_policy:'jev_then_claude_on_low_confidence',scenario,round:2,episode:1,participants,candidates:[],proposal:null,consensus:false,checks:[],metrics:{},created_at:new Date().toISOString(),messages:[
      {...common,id:'jev-message',sender:'atc',statement:'I can consider the checked alternatives.',provider:'jev',statement_source:'template_from_jev_decision',decision:{selected:'plan-test',confidence:0.9,probabilities:{'plan-test':0.9,REVIEW:0.1}},usage:{provider:'jev',model:'jev-latest'}},
      {...common,id:'claude-message',sender:'pilot',statement:'I need the stand availability confirmed before committing.',provider:'claude',statement_source:'claude_generated',fallback:{from:'jev',to:'claude',reason:'low_confidence',threshold:0.7},primary_decision:{selected:'REVIEW',confidence:0.55},primary_usage:{provider:'jev'},usage:{provider:'claude'}}
    ]};
    await page.route('**/api/health', route=>route.fulfill({json:{provider:'jev',provider_label:'Jev',provider_configured:true,claude_configured:true,fallback_provider:'claude',fallback_configured:true,model:'jev-latest'}}));
    await page.route('**/api/runs', route=>{
      if(route.request().method()==='POST') {interceptedCalls++;return route.fulfill({json:run});}
      return route.fulfill({json:{runs:[]}});
    });
    await page.route('**/api/runs/jev-ui-fixture/events', route=>route.fulfill({status:200,contentType:'text/event-stream',body:': fixture\n\n'}));
    await page.goto(base,{waitUntil:'networkidle'});
    await page.getByText('Jev key configured',{exact:false}).waitFor();
    await page.getByText('Jev first · Claude on low confidence.',{exact:true}).waitFor();
    await page.getByText('Claude fallback configured',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Coordinate live',exact:true}).click();
    await page.getByText('LIVE JEV · TYPED DECISIONS',{exact:true}).waitFor();
    await page.getByText('Jev decision · template-rendered communication',{exact:true}).waitFor();
    await page.getByText('Claude fallback · low-confidence Jev decision',{exact:true}).waitFor();
    await page.getByText('Inspect Jev handoff & model usage',{exact:true}).click();
    await page.getByText('Jev returned an uncertain decision.',{exact:false}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
    assert.equal(interceptedCalls,1);
    assert.deepEqual(errors,[]);
    console.log('PASS: Jev configuration, primary/fallback policy, typed-decision provenance, Claude handoff, mobile layout; zero model calls.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
