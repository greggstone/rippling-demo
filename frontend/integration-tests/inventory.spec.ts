import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";

test("creates, edits and deletes a product", async ({ page }) => {
  const name = `Playwright Desk ${Date.now()}`;
  await page.goto(BASE_URL);
  const form = page.locator("form.product-form");

  await expect(
    page.getByRole("heading", { name: "Product Inventory" }),
  ).toBeVisible();

  await form.getByLabel("Name").fill(name);
  await form.getByLabel("Category").fill("Furniture");
  await form.getByLabel("Brand").fill("Rippling");
  await form.getByLabel("Price").fill("499.99");
  await form.getByLabel("Quantity").fill("12");
  await form.getByRole("button", { name: "Add product" }).click();

  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await expect(row.getByText("$499.99")).toBeVisible();

  await row.getByRole("button", { name: "Edit" }).click();
  await form.getByLabel("Quantity").fill("3");
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(row.getByRole("cell", { name: "3", exact: true })).toBeVisible();

  await row.getByRole("button", { name: "Delete" }).click();
  await expect(row).toHaveCount(0);
});

test("rejects a product without a name", async ({ page }) => {
  await page.goto(BASE_URL);
  const form = page.locator("form.product-form");

  await form.getByLabel("Category").fill("Devices");
  await form.getByRole("button", { name: "Add product" }).click();

  await expect(page.getByText("must not be empty")).toBeVisible();
});
