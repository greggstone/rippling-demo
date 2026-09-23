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
        const id = url.split("/")[2];
        products = products.filter((product) => product.id !== id);
        return jsonResponse(undefined, 204);
      }
      const params = new URL(url, "http://test").searchParams;
      const page = Number(params.get("page"));
      const filter = params.get("category");
      const matching = filter
        ? products.filter((product) => product.category === filter)
        : products;
      return jsonResponse({
        results: matching.slice((page - 1) * 5, page * 5),
        total: matching.length,
        page,
        page_size: 5,
      });
    });
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  test("lists products returned by the API", async () => {
    render(<ProductsPage />);

    expect(await screen.findByText("Standing Desk")).toBeInTheDocument();
    expect(screen.getByText("$499.99")).toBeInTheDocument();
    expect(screen.getByText("1 product")).toBeInTheDocument();
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

  test("falls back to the last page when the current page empties", async () => {
    products = Array.from({ length: 6 }, (_, index) => ({
      ...PRODUCT,
      id: String(index + 1),
      name: `Product ${index + 1}`,
    }));
    render(<ProductsPage />);
    await screen.findByText("Product 1");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Page 1 of 1")).toBeInTheDocument();
    expect(screen.getByText("Product 1")).toBeInTheDocument();
  });

  test("clears a category filter when its last product is deleted", async () => {
    products = [
      PRODUCT,
      { ...PRODUCT, id: "2", name: "Stapler", category: "Office" },
    ];
    render(<ProductsPage />);
    await screen.findByText("Stapler");

    await userEvent.selectOptions(
      screen.getByLabelText("Filter by category"),
      "Office",
    );
    expect(await screen.findByText("1 product")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText("Standing Desk")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter by category")).toHaveValue("");
  });

  test("deletes a product", async () => {
    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await screen.findByText("No products yet. Add one below.");
  });
});
