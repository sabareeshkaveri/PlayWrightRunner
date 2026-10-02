import { test, expect } from '@playwright/test';

const url = '/loginpagePractise/';

test.describe('TC001 Login Page Tests', { tag: ['@smoke', '@regression'] }, () => {
    test('TC_001 Navigation Login test', async ({ page }) => {
        await page.goto(url);
        const title = await page.title();
        const expectedTitle = 'LoginPage Practise | Rahul Shetty Academy';
        await test.step('Page Title', async () => {
        expect(title, 'Page Title').toBe(expectedTitle);
    }, { params: { expected: expectedTitle, actual: title } });
});
});