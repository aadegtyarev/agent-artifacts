// Synthetic response overlays on the loopback PNG fixture; no server data is changed.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME,args:['--no-sandbox']});
 try {
  const p=await browser.newPage({viewport:{width:1366,height:800}});
  await p.addInitScript(()=>localStorage.setItem('agent-artifacts:name','Alex'));
  await p.route('**/comments',async route=>{
   const response=await route.fetch(), comments=await response.json();
   comments[0].agent='done'; comments[0].replies=[];
   comments[1].mine=true; comments[1].agent='waiting';
   comments[1].replies=[{author:'Olga',text:'Unread peer reply',mine:false,agent:false,created_at:comments[1].created_at}];
   comments[2].agent='waiting'; comments[2].replies=[];
   await route.fulfill({response,json:comments});
  });
  await p.route('**/drafts',async route=>{
   if(route.request().method()==='GET') return route.fulfill({json:[]});
   const d=route.request().postDataJSON(); return route.fulfill({json:{...d,id:'synthetic-decision',created_at:new Date().toISOString()}});
  });
  await p.goto(`${process.env.BASE_URL}/a/${process.env.ARTIFACT_ID}`);
  const toggle=p.locator('#comments-toggle'), mode=p.locator('[data-act=comment]');
  await toggle.locator('visible=true').waitFor(); await p.waitForFunction(()=>document.querySelector('#strip-text')?.textContent.includes('2 need you'));
  assert(await toggle.evaluate(e=>e.classList.contains('needs-attention')));
  assert.equal(await mode.getAttribute('aria-describedby'),'comment-attention');
  await toggle.focus(); await p.keyboard.press('Enter');
  await p.getByText('Could we put the date range next to the download?',{exact:true}).click();
  await p.waitForFunction(()=>document.querySelector('#strip-text')?.textContent.includes('1 need you'));
  assert(await toggle.evaluate(e=>e.classList.contains('needs-attention')));
  await p.getByRole('button',{name:'‹ All'}).click();
  await p.getByText('Can we include the timezone in the CSV?',{exact:true}).click();
  await p.getByRole('button',{name:'✓ Accept',exact:true}).click();
  await p.waitForFunction(()=>!document.querySelector('#comments-toggle').classList.contains('needs-attention'));
  assert(!(await mode.evaluate(e=>e.classList.contains('needs-attention'))));
  assert(!(await toggle.getAttribute('aria-label')).includes('attention'));
  assert.equal(await p.locator('#pins .pin').count(),3);
  await p.locator('#full-button').click(); const tip=p.locator('#float-tip'); if(await tip.isVisible()) await tip.getByRole('button',{name:'OK'}).click();
  await p.locator('#float-button').focus(); await p.keyboard.press('Enter'); await mode.focus();
  assert.equal(await mode.getAttribute('aria-checked'),'true'); assert(await mode.locator('.comment-check').isVisible());
  assert.equal(await mode.getAttribute('aria-describedby'),null);
  await p.keyboard.press('Enter'); assert.equal(await toggle.getAttribute('aria-pressed'),'false');
  console.log('Attention 2 → 1 after read → 0 after local decision; keyboard full-screen mode passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
