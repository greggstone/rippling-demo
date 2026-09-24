import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ProductsPage from "../inventory/ProductsPage";

const PRODUCT = {
  id: "1",
  name: "Standing Desk",
  description: "Height adjustable",
  category: "Furniture",
  brand: "Rippling",
  price: 499.99,
  quantity: 12,
};

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function response(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

describe("ProductsPage regressions", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("ignores stale list responses that resolve after a newer request", async () => {
    const pending: Record<string, Deferred<Response>> = {};
    global.fetch = jest.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/products/categories/")) {
        return Promise.resolve(response({ results: ["Furniture"] }));
      }
      const page = new URL(url, "http://test").searchParams.get("page") ?? "";
      pending[page] = deferred<Response>();
      return pending[page].promise;
    }) as unknown as typeof fetch;

    render(<ProductsPage />);

    await act(async () => {
      pending["1"].resolve(
        response({
          results: Array.from({ length: 5 }, (_, i) => ({
            ...PRODUCT,
            id: String(i + 1),
            name: `Page one ${i + 1}`,
          })),
          total: 12,
          page: 1,
          page_size: 5,
        }),
      );
    });
    await screen.findByText("Page one 1");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(pending["2"]).toBeDefined();
    expect(pending["3"]).toBeDefined();

    // page 3 (newest request) resolves first
    await act(async () => {
      pending["3"].resolve(
        response({
          results: [{ ...PRODUCT, id: "11", name: "Page three item" }],
          total: 12,
          page: 3,
          page_size: 5,
        }),
      );
    });
    await screen.findByText("Page three item");

    // the slow, stale page 2 response arrives afterwards and must be dropped
    await act(async () => {
      pending["2"].resolve(
        response({
          results: [{ ...PRODUCT, id: "6", name: "Page two item" }],
          total: 12,
          page: 2,
          page_size: 5,
        }),
      );
    });

    expect(screen.getByText("Page three item")).toBeInTheDocument();
    expect(screen.queryByText("Page two item")).not.toBeInTheDocument();
    expect(screen.getByText("Page 3 of 3")).toBeInTheDocument();
  });

  test("shows an error and keeps the row when deleting fails", async () => {
    global.fetch = jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/products/categories/")) {
        return Promise.resolve(response({ results: ["Furniture"] }));
      }
      if (init?.method === "DELETE") {
        return Promise.resolve(response({}, 500));
      }
      return Promise.resolve(
        response({ results: [PRODUCT], total: 1, page: 1, page_size: 5 }),
      );
    }) as unknown as typeof fetch;

    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(
      await screen.findByText(
        "Could not delete the product. Please try again.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Standing Desk")).toBeInTheDocument();
  });

  test("leaves edit mode when the product being edited is deleted", async () => {
    let products = [PRODUCT];
    global.fetch = jest.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/products/categories/")) {
        return Promise.resolve(
          response({ results: products.map((p) => p.category) }),
        );
      }
      if (init?.method === "DELETE") {
        products = [];
        return Promise.resolve(response(undefined, 204));
      }
      return Promise.resolve(
        response({
          results: products,
          total: products.length,
          page: 1,
          page_size: 5,
        }),
      );
    }) as unknown as typeof fetch;

    render(<ProductsPage />);
    await screen.findByText("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByText("Edit product")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Standing Desk");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await screen.findByText("No products yet. Add one below.");
    expect(
      screen.getByRole("heading", { name: "Add product" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });
});
