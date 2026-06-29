import { test, expect } from "@playwright/test";

test("核心旅程（demo auth）：登录入口→选项目→能力→风险→执行→报告", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "企业登录" })).toBeVisible();

  await page.getByRole("link", { name: "进入演示模式" }).click();
  await expect(page.getByRole("heading", { name: "项目列表" })).toBeVisible();

  const firstProjectCard = page.locator('a[href^="/projects/"]').first();
  await firstProjectCard.click();
  await expect(page.getByRole("heading", { name: "项目工作区" })).toBeVisible();

  await page.getByRole("link", { name: "能力中心" }).click();
  await expect(page.getByRole("heading", { name: "覆盖度与缺口" })).toBeVisible();

  await page.getByRole("link", { name: "风险证据" }).click();
  await expect(page.getByRole("heading", { name: "风险证据链" })).toBeVisible();

  await page.getByRole("link", { name: "执行" }).click();
  await expect(page.getByRole("heading", { name: "执行与任务生命周期" })).toBeVisible();

  await page.getByRole("link", { name: "报告" }).click();
  await expect(page.getByRole("heading", { name: "领导层报告" })).toBeVisible();
});
