import { test, expect } from '@playwright/test';

test('test', async ({ page }) => {
  await page.goto('https://test5.squad1.tech/login?RetUrl=/home');
});