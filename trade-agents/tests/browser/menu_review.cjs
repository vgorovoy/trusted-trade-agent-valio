/** Real browser regression for one global chef review; all API responses are TEST MOCKS.
 * Start the local web server, then run with Playwright installed:
 * NODE_PATH=/tmp/junction-demo-recording/node_modules node tests/browser/menu_review.cjs
 * Optional TTA_UI_TEST_URL defaults to the dedicated localhost8001 server.
 * No model, supplier or purchasing API is called by this test.
 */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.TTA_UI_TEST_URL || 'http://127.0.0.1:8001';
const url = new URL(base);
if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
  throw new Error('Use an explicitly local test server');
}
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
    const submitted = [];
    let drafts = 0;
    await page.route('**/*', async route => {
      const request = route.request();
      const target = new URL(request.url());
      if (target.origin !== url.origin) return route.abort();
      if (target.pathname === '/ui/overview') return route.fulfill({json: {
        orders: [], choices: [], audit: {entries: [], chain_valid: true}, jwks: {keys: []},
      }});
      if (target.pathname === '/ui/agent/models') return route.fulfill({json: {
        default: {id: '', label: 'Gemini 3.5 Flash'}, options: [],
      }});
      if (target.pathname === '/ui/menu/draft') {
        drafts++;
        return route.fulfill({json: {
          status: 'draft', guests: 1000, requires_review: true,
          assumptions: ['TEST MOCK recipe estimates'], warnings: ['TEST MOCK stock unknown'],
          needs: [
            {ingredient: 'Pasta', amount: 60, unit: 'KG', date: '2026-11-16', constraints: ['milk-free']},
            {ingredient: 'Oat drink', amount: 80, unit: 'L', date: '2026-11-16', constraints: ['milk-free']},
          ],
        }});
      }
      if (target.pathname === '/ui/agent/run') {
        submitted.push(request.postDataJSON());
        return route.fulfill({json: {
          status: 'awaiting_cart_confirmation', order_id: 'TEST-MOCK-CART',
          explanation: 'TEST MOCK: chef still has to confirm actual products and price.',
        }});
      }
      // Only serve the actual page and static assets. No other backend endpoint may mutate state.
      if (request.method() !== 'GET' || target.pathname.startsWith('/ui/') || target.pathname.startsWith('/api/')) {
        throw new Error(`Unexpected backend request in browser test: ${request.method()} ${target.pathname}`);
      }
      return route.continue();
    });
    await page.goto(base);
    await page.selectOption('#request-kind', 'menu');
    await page.fill('#menu-text', 'TEST MOCK casserole for 1000 children');
    await page.click('#menu-draft-button');
    await page.waitForSelector('#menu-reviewed');
    const rows = page.locator('[data-menu-row]');
    const buttons = page.locator('[data-menu-buy]');
    const review = page.getByLabel('I reviewed all ingredients, quantities, dates and requirements', {exact: true});
    assert.equal(await rows.count(), 2);
    assert.equal(await page.locator('#menu-preview input[type="checkbox"]').count(), 1);
    assert.equal(await review.isChecked(), false);
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await buttons.nth(1).isDisabled(), true);
    assert.equal(submitted.length, 0);

    await review.check();
    assert.equal(await buttons.nth(0).isEnabled(), true);
    assert.equal(await buttons.nth(1).isEnabled(), true);
    await rows.nth(0).locator('[data-menu-field="amount"]').fill('65');
    assert.equal(await review.isChecked(), false);
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await buttons.nth(1).isDisabled(), true);

    await review.check();
    await buttons.nth(0).click();
    await page.waitForFunction(() => document.querySelector('[data-menu-row="0"] .menu-row-result')
      .textContent.includes('awaiting_cart_confirmation'));
    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].mode, 'confirm_cart');
    assert.equal(submitted[0].buyer, 'school');
    assert(submitted[0].request.includes('65 KG Pasta'));
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await rows.nth(0).locator('[data-menu-field="amount"]').isDisabled(), true);
    assert.equal(await review.isChecked(), true);
    assert.equal(await buttons.nth(1).isEnabled(), true);

    await rows.nth(1).locator('[data-menu-field="constraints"]').fill('milk-free, gluten-free');
    assert.equal(await review.isChecked(), false);
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await buttons.nth(1).isDisabled(), true);
    await review.check();
    assert.equal(await buttons.nth(0).isDisabled(), true); // Submitted basket cannot be resent.
    assert.equal(await buttons.nth(1).isEnabled(), true);
    assert.equal(submitted.length, 1);

    await page.click('#menu-draft-button');
    await page.waitForFunction(() => document.querySelector('[data-menu-row="0"] [data-menu-field="amount"]').value === '60');
    assert.equal(drafts, 2);
    assert.equal(await page.locator('#menu-preview input[type="checkbox"]').count(), 1);
    assert.equal(await review.isChecked(), false);
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await buttons.nth(1).isDisabled(), true);
    assert.equal(submitted.length, 1); // Draft regeneration never submits a basket.
    console.log('PASS: one global review, edit invalidation, submitted-row lock, explicit confirm-cart, new-draft reset (mock APIs only)');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exit(1); });
