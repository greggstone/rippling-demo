import React from "react";
import { render, screen } from "@testing-library/react";
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

describe("ProductsPage", () => {
  let products: StoredProduct[];
  let fetchMock: jest.Mock;

  beforeEach(() => {
    products = [PRODUCT];
    fetchMock = jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/products/categories/")) {
        const categories = products
          .map((p) => p.category)
          .filter((value, index, all) => all.indexOf(value) === index);
        return jsonResponse({ results: categories });
      }
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (!body.name) {
          return jsonResponse({ errors: { name: "must not be empty" } }, 400);
        }
        products = [{ ...body, id: String(products.length + 1) }, ...products];
        return jsonResponse(products[0], 201);
      }
      if (init?.method === "DELETE") {
        products = [];
        return jsonResponse(undefined, 204);
      }
      return jsonResponse({
        results: products,
        total: products.length,
        page: 1,
        page_size: 5,
      });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  test("lists products returned by the API", async () => {
    render(<ProductsPage />);

    expect(await screen.findByText("Standing Desk")).toBeInTheDocument();
    expect(screen.getByText("$499.99")).toBeInTheDocument();
    expect(screen.getByText("1 products")).toBeInTheDocument();
  });

  test("creates a product and refreshes the table", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.type(screen.getByLabelText("Name"), "Monitor");
    await userEvent.type(screen.getByLabelText("Category"), "Devices");
    await userEvent.clear(screen.getByLabelText("Price"));
    await userEvent.type(screen.getByLabelText("Price"), "250");
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));

    expect(await screen.findByText("Monitor")).toBeInTheDocument();
  });

  test("surfaces server side validation errors", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Add product" }));

    expect(await screen.findByText("must not be empty")).toBeInTheDocument();
  });

  test("deletes a product", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await screen.findByText("No products yet. Add one below.");
  });
});
