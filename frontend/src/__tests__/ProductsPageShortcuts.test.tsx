import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ProductsPage from "../inventory/ProductsPage";

interface StoredProduct {
  id: string;
  name: string;
  description: string;
  category: string;
  brand: string;
  price: number;
  quantity: number;
}

const PRODUCT: StoredProduct = {
  id: "1",
  name: "Standing Desk",
  description: "Height adjustable",
  category: "Furniture",
  brand: "Rippling",
  price: 499.99,
  quantity: 12,
};

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

describe("ProductsPage keyboard shortcuts", () => {
  let products: StoredProduct[];
  let listCalls: number;

  beforeEach(() => {
    products = [PRODUCT];
    listCalls = 0;
    global.fetch = jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/products/categories/")) {
        return jsonResponse({
          results: Array.from(new Set(products.map((p) => p.category))),
        });
      }
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        products = [{ ...body, id: String(products.length + 1) }, ...products];
        return jsonResponse(products[0], 201);
      }
      listCalls += 1;
      const page = Number(new URL(url, "http://test").searchParams.get("page"));
      return jsonResponse({
        results: products.slice((page - 1) * 5, page * 5),
        total: products.length,
        page,
        page_size: 5,
      });
    }) as unknown as typeof fetch;
  });

  test("'n' focuses the add product form", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    fireEvent.keyDown(document.body, { key: "n" });

    expect(screen.getByLabelText("Name")).toHaveFocus();
  });

  test("'/' focuses the category filter", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    fireEvent.keyDown(document.body, { key: "/" });

    expect(screen.getByLabelText("Filter by category")).toHaveFocus();
  });

  test("'r' refreshes the product list", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");
    const before = listCalls;

    fireEvent.keyDown(document.body, { key: "r" });

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await screen.findByText("Standing Desk");
    expect(listCalls).toBe(before + 1);
  });

  test("']' and '[' move between pages", async () => {
    products = Array.from({ length: 6 }, (_, index) => ({
      ...PRODUCT,
      id: String(index + 1),
      name: `Product ${index + 1}`,
    }));
    render(<ProductsPage />);
    await screen.findByText("Product 1");

    fireEvent.keyDown(document.body, { key: "]" });
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "]" });
    expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "[" });
    expect(await screen.findByText("Page 1 of 2")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "[" });
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
  });

  test("Ctrl+Enter in the form submits it", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    const name = screen.getByLabelText("Name");
    await userEvent.type(name, "Monitor");
    fireEvent.keyDown(name, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("Monitor")).toBeInTheDocument();
  });

  test("Escape cancels editing and clears the form", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByText("Edit product")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Standing Desk");

    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Escape" });

    expect(
      screen.getByRole("heading", { name: "Add product" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });

  test("'?' toggles the shortcuts help panel", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "?" });
    expect(
      screen.getByRole("dialog", { name: "Keyboard shortcuts" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Focus the add product form")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "?" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("typing 'n' in the name field does not steal focus", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    const brand = screen.getByLabelText("Brand");
    await userEvent.type(brand, "n/r");

    expect(brand).toHaveValue("n/r");
    expect(brand).toHaveFocus();
  });
});
