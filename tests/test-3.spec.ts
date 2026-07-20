import { test, expect } from '@playwright/test';

test.skip(!!process.env.CI, 'Flaky in CI due to external login/captcha dependency.');

test('test', async ({ page }) => {
  await page.goto('https://test5.squad1.tech/');
  await page.getByRole('link', { name: 'Log in' }).click();
  await page.getByRole('textbox', { name: 'Email ID' }).click();
  await page.getByRole('textbox', { name: 'Email ID' }).fill('admin@talakunchi.com');
  await page.getByRole('textbox', { name: 'Password' }).click();
  await page.getByRole('textbox', { name: 'Password' }).fill('Test#123');
  await page.getByRole('link', { name: 'Log in' }).click();
  await page.getByRole('link', { name: 'Settings ' }).click();
  await page.getByRole('link', { name: 'General ' }).click();
  await page.getByRole('link', { name: 'Company' }).click();
  await page.getByRole('textbox', { name: 'Company code' }).click();
  await page.getByRole('textbox', { name: 'Company Name' }).click();
  await page.getByRole('textbox', { name: 'License No' }).click();
  await page.getByRole('textbox', { name: 'Website' }).click();
  await page.getByRole('textbox', { name: 'Domain' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).click();
  await page.getByText('Email ID * tatasteel.com').first().click();
  await page.getByRole('textbox', { name: 'Email ID' }).first().click();
  await page.locator('#txtCP1_Contact').click();
  await page.getByRole('textbox', { name: 'Name of the Person' }).click();
  await page.getByRole('textbox', { name: 'Email ID' }).nth(1).click();
  await page.locator('#txtCP2_Contact').click();
  await page.getByRole('button', { name: 'Submit' }).click();
});