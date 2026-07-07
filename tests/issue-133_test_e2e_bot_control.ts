import { test, expect } from '@playwright/test';

test.describe('Bot Control E2E Tests', () => {
  const baseUrl = process.env.BASE_URL || 'http://localhost:3000';
  
  test.beforeEach(async ({ page }) => {
    // Navigate to meetings page
    await page.goto(`${baseUrl}/meetings`);
    
    // Wait for page to load
    await page.waitForSelector('h1:has-text("Meeting Capture")');
  });

  test('should complete full user journey with bot control', async ({ page }) => {
    // Schedule a new meeting first
    await page.fill('input#meeting-url', 'https://meet.google.com/test-meeting-123');
    await page.fill('input#bot-name', 'E2E Test Bot');
    await page.click('button:has-text("Schedule Recording")');
    
    // Wait for meeting to appear in list
    await page.waitForSelector('text=E2E Test Bot', { timeout: 10000 });
    
    // Find the control button for the active meeting
    const meetingCard = page.locator('div:has-text("E2E Test Bot")').first();
    const controlButton = meetingCard.locator('button:has-text("Control")');
    
    // Control button should only appear for active meetings
    await expect(controlButton).toBeVisible();
    
    // Click control button to open modal
    await controlButton.click();
    
    // Wait for modal to open
    await page.waitForSelector('text=Bot Control Panel');
    
    // Verify all display modes are shown
    await expect(page.locator('text=Meeting Intelligence')).toBeVisible();
    await expect(page.locator('text=Meeting State')).toBeVisible();
    await expect(page.locator('text=Tasks Monitor')).toBeVisible();
    await expect(page.locator('text=Agenda Tracker')).toBeVisible();
    
    // Current mode should be highlighted
    const intelligenceMode = page.locator('[data-testid="mode-selector-intelligence"]');
    await expect(intelligenceMode).toHaveAttribute('data-selected', 'true');
    
    // Select a different mode
    const stateMode = page.locator('[data-testid="mode-selector-state"]');
    await stateMode.click();
    
    // Apply button should be enabled
    const applyButton = page.locator('button:has-text("Apply")');
    await expect(applyButton).not.toBeDisabled();
    
    // Apply the change
    await applyButton.click();
    
    // Modal should close
    await expect(page.locator('text=Bot Control Panel')).not.toBeVisible();
    
    // Success toast should appear
    await expect(page.locator('text=Display mode updated')).toBeVisible();
  });

  test('should show real-time updates across browser tabs', async ({ browser }) => {
    // Create two browser contexts (simulating two users)
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();
    
    // Both users navigate to meetings page
    await page1.goto(`${baseUrl}/meetings`);
    await page2.goto(`${baseUrl}/meetings`);
    
    // Assume there's already an active meeting
    await page1.waitForSelector('button:has-text("Control")');
    await page2.waitForSelector('button:has-text("Control")');
    
    // User 1 opens control modal
    await page1.click('button:has-text("Control")').first();
    await page1.waitForSelector('text=Bot Control Panel');
    
    // User 2 also opens control modal
    await page2.click('button:has-text("Control")').first();
    await page2.waitForSelector('text=Bot Control Panel');
    
    // User 1 changes mode to State
    await page1.click('[data-testid="mode-selector-state"]');
    await page1.click('button:has-text("Apply")');
    
    // User 2 should see the update in real-time
    await expect(page2.locator('[data-testid="mode-selector-state"]')).toHaveAttribute(
      'data-selected', 
      'true',
      { timeout: 5000 }
    );
    
    // Clean up
    await context1.close();
    await context2.close();
  });

  test('should handle mobile responsive design', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    
    // Navigate to meetings page
    await page.goto(`${baseUrl}/meetings`);
    await page.waitForSelector('h1:has-text("Meeting Capture")');
    
    // Control button should be visible on mobile
    const controlButton = page.locator('button:has-text("Control")').first();
    await expect(controlButton).toBeVisible();
    
    // Click to open modal
    await controlButton.click();
    
    // Modal should be full screen on mobile
    const modal = page.locator('[role="dialog"]');
    await expect(modal).toBeVisible();
    
    // Mode selector should stack vertically on mobile
    const modeSelector = page.locator('[data-testid^="mode-selector-"]');
    const count = await modeSelector.count();
    expect(count).toBe(4);
    
    // All modes should be visible without horizontal scrolling
    for (let i = 0; i < count; i++) {
      await expect(modeSelector.nth(i)).toBeInViewport();
    }
    
    // Buttons should be full width on mobile
    const applyButton = page.locator('button:has-text("Apply")');
    const buttonBox = await applyButton.boundingBox();
    expect(buttonBox?.width).toBeGreaterThan(300);
  });

  test('should recover from network interruption', async ({ page, context }) => {
    // Navigate to meetings page
    await page.goto(`${baseUrl}/meetings`);
    await page.waitForSelector('button:has-text("Control")');
    
    // Open control modal
    await page.click('button:has-text("Control")').first();
    await page.waitForSelector('text=Bot Control Panel');
    
    // Simulate network interruption
    await context.setOffline(true);
    
    // Try to change mode
    await page.click('[data-testid="mode-selector-tasks"]');
    await page.click('button:has-text("Apply")');
    
    // Should show error message
    await expect(page.locator('text=Connection error')).toBeVisible({ timeout: 5000 });
    
    // Restore network
    await context.setOffline(false);
    
    // Retry button should appear
    const retryButton = page.locator('button:has-text("Retry")');
    await expect(retryButton).toBeVisible();
    
    // Click retry
    await retryButton.click();
    
    // Should successfully update
    await expect(page.locator('text=Display mode updated')).toBeVisible({ timeout: 5000 });
  });

  test('should not show control button for completed meetings', async ({ page }) => {
    // Navigate to meetings page
    await page.goto(`${baseUrl}/meetings`);
    
    // Wait for meetings to load
    await page.waitForSelector('text=Recorded Meetings');
    
    // Find completed meetings (status: done)
    const completedMeetings = page.locator('div:has-text("done")');
    const count = await completedMeetings.count();
    
    if (count > 0) {
      // Check that none of them have control buttons
      for (let i = 0; i < count; i++) {
        const meeting = completedMeetings.nth(i);
        const controlButton = meeting.locator('button:has-text("Control")');
        await expect(controlButton).not.toBeVisible();
      }
    }
  });

  test('should handle rapid mode changes', async ({ page }) => {
    // Navigate and open control modal
    await page.goto(`${baseUrl}/meetings`);
    await page.click('button:has-text("Control")').first();
    await page.waitForSelector('text=Bot Control Panel');
    
    // Rapidly click through all modes
    const modes = ['state', 'tasks', 'agenda', 'intelligence'];
    
    for (const mode of modes) {
      await page.click(`[data-testid="mode-selector-${mode}"]`);
      // Don't wait between clicks
    }
    
    // Apply the last selection
    await page.click('button:has-text("Apply")');
    
    // Should apply the last selected mode without errors
    await expect(page.locator('text=Display mode updated')).toBeVisible();
    
    // Verify the correct mode was applied
    await page.click('button:has-text("Control")').first();
    await expect(
      page.locator('[data-testid="mode-selector-intelligence"][data-selected="true"]')
    ).toBeVisible();
  });

  test('should maintain state after page refresh', async ({ page }) => {
    // Navigate and change mode
    await page.goto(`${baseUrl}/meetings`);
    await page.click('button:has-text("Control")').first();
    await page.waitForSelector('text=Bot Control Panel');
    
    // Change to tasks mode
    await page.click('[data-testid="mode-selector-tasks"]');
    await page.click('button:has-text("Apply")');
    
    // Wait for modal to close
    await expect(page.locator('text=Bot Control Panel')).not.toBeVisible();
    
    // Refresh the page
    await page.reload();
    
    // Open modal again
    await page.waitForSelector('button:has-text("Control")');
    await page.click('button:has-text("Control")').first();
    await page.waitForSelector('text=Bot Control Panel');
    
    // Tasks mode should still be selected
    await expect(
      page.locator('[data-testid="mode-selector-tasks"][data-selected="true"]')
    ).toBeVisible();
  });
});