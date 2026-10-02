import { test, expect } from '@playwright/test';

test("test123", async ({ page }) => {
  await page.goto('https://rahulshettyacademy.com/');
  await expect(page.getByRole('button', { name: 'Career Accelerator (Job/Skill' })).toBeVisible();
  await page.getByRole('link', { name: 'Practice Apps' }).click();
  await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();
  await page.getByRole('button', { name: 'Browse Practice sites &' }).click();
  await page.getByRole('tab', { name: 'Web Automation' }).click();
  await page.locator('.absolute.inset-x-0').first().click();
  await page.getByRole('button', { name: 'Start Practicing' }).first().click();
  await expect(page.getByRole('dialog', { name: 'Verify Access' })).toBeVisible();
  await page.getByRole('button', { name: 'Close' }).click();
});