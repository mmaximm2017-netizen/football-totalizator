const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    for (const [width,height] of [[393,740],[360,800]]) {
      execFileSync('python', ['tests/browser/journey_fixture.py','seed']);
      const fixture = JSON.parse(fs.readFileSync('test-results/site-journey/fixture.json'));
      const context = await browser.newContext({viewport:{width,height}});
      const page = await context.newPage();
      const ready = () => page.waitForFunction(() => {
        const splash=document.getElementById('app-splash');
        const hidden=!splash || (() => {
          const style=getComputedStyle(splash);
          return style.display==='none' || style.visibility==='hidden' || Number(style.opacity)===0;
        })();
        // page-transition is the content container, not an overlay.
        const content=document.getElementById('page-transition');
        return hidden && (!content || Number(getComputedStyle(content).opacity)>=0.99);
      });
      const errors=[];
      page.on('pageerror', e=>errors.push(e.message));
      await page.goto('http://localhost:8766/register');
      await page.getByLabel('Логин', {exact:true}).fill(fixture.username);
      await page.getByLabel('Пароль', {exact:true}).fill('browser-test-password');
      await page.getByLabel('Инвайт-код', {exact:true}).fill(process.env.INVITE_CODE);
      await page.locator('button[type=submit]').click();
      await page.waitForURL('**/login');
      await page.getByLabel('Логин', {exact:true}).fill(fixture.username);
      await page.getByLabel('Пароль', {exact:true}).fill('browser-test-password');
      await page.locator('button[type=submit]').click();
      await page.waitForURL(url=>url.pathname==='/');
      await page.goto(`http://localhost:8766/?tid=${fixture.tid}`);
      await ready();
      const day=page.locator('button.day-header-v2').first();
      await day.focus();
      const expanded=await day.getAttribute('aria-expanded');
      await page.keyboard.press('Enter');
      assert.notEqual(await day.getAttribute('aria-expanded'),expanded);
      if(await day.getAttribute('aria-expanded')==='false') await day.press('Enter');
      await page.locator(`[name=home_goals][form=predict-form-${fixture.mid}]`).fill('2');
      await page.locator(`[name=away_goals][form=predict-form-${fixture.mid}]`).fill('1');
      const saved=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname==='/');
      await page.locator(`button[type=submit][form=predict-form-${fixture.mid}]`).click();
      assert.equal((await saved).status(),200);
      await ready();
      await page.screenshot({path:`test-results/site-journey/saved-${width}.png`,fullPage:true});
      execFileSync('python', ['tests/browser/journey_fixture.py','finish']);
      await page.goto(`http://localhost:8766/match/${fixture.mid}/predictions`);
      await ready();
      const result=page.locator('.prediction-row').filter({hasText:fixture.username});
      assert.equal(await result.count(),1);
      assert.match(await result.innerText(),/10/);
      await page.screenshot({path:`test-results/site-journey/result-${width}.png`,fullPage:true});
      const stolen=(await context.cookies()).find(c=>c.name==='session');
      const csrf=await page.locator('form[action="/logout"] input[name=csrf_token]').inputValue();
      assert.equal((await context.request.post('http://localhost:8766/logout',{form:{csrf_token:csrf}})).status(),200);
      const replay=await browser.newContext();
      await replay.addCookies([stolen]);
      const response=await replay.request.get('http://localhost:8766/profile',{maxRedirects:0});
      assert.equal(response.status(),302);
      assert.match(response.headers().location,/login/);
      assert.deepEqual(errors,[]);
      await replay.close();
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
