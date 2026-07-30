import { test, expect } from '@playwright/test';

test.skip(!!process.env.CI, 'Flaky in CI due to external login/captcha dependency.');

test('test', async ({ page }) => {
  await page.goto('https://test5.squad1.tech/');
  await page.getByRole('textbox', { name: 'Email ID' }).click();
  await page.getByRole('textbox', { name: 'Email ID' }).fill('admin@talakunchi.com');
  await page.getByRole('textbox', { name: 'Password' }).click();
  await page.getByRole('textbox', { name: 'Password' }).fill('Test#123');
  await page.getByRole('textbox', { name: 'Password' }).press('Enter');
  await page.getByRole('link', { name: 'Log in' }).click();
});