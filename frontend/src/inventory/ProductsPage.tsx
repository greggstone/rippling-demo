import React, { useCallback, useEffect, useMemo, useState } from "react";

import ProductForm from "./ProductForm";
import ShortcutsHelp from "./ShortcutsHelp";
import useKeyboardShortcuts from "./useKeyboardShortcuts";
import {
  ApiError,
  Product,
  ProductInput,
  createProduct,
  deleteProduct,
  listCategories,
  listProducts,
  updateProduct,
} from "./api";
import "./ProductsPage.scss";

const PAGE_SIZE = 5;

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [editing, setEditing] = useState<Product | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [resetToken, setResetToken] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [productPage, categoryList] = await Promise.all([
        listProducts({ page, pageSize: PAGE_SIZE, category }),
        listCategories(),
      ]);
      setCategories(categoryList.results);
      if (category && !categoryList.results.includes(category)) {
        setCategory("");
        setPage(1);
        return;
      }
      const lastPage = Math.max(1, Math.ceil(productPage.total / PAGE_SIZE));
      if (page > lastPage) {
        setPage(lastPage);
        return;
      }
      setProducts(productPage.results);
      setTotal(productPage.total);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, category]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async (values: ProductInput) => {
    setSubmitting(true);
    try {
      if (editing) {
        await updateProduct(editing.id, values);
      } else {
        await createProduct(values);
      }
      setEditing(null);
      setFieldErrors({});
      setResetToken((current) => current + 1);
      setPage(1);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length > 0) {
        setFieldErrors(err.fieldErrors);
        setError(null);
      } else {
        setError("Could not save the product. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (product: Product) => {
    await deleteProduct(product.id);
    await refresh();
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const shortcutHandlers = useMemo(
    () => ({
      focusAddForm: () => document.getElementById("name")?.focus(),
      cancel: () => {
        setEditing(null);
        setFieldErrors({});
        setResetToken((current) => current + 1);
        setHelpOpen(false);
      },
      nextPage: () => setPage((current) => Math.min(totalPages, current + 1)),
      previousPage: () => setPage((current) => Math.max(1, current - 1)),
      focusFilter: () => document.getElementById("category-filter")?.focus(),
      refresh,
      toggleHelp: () => setHelpOpen((open) => !open),
    }),
    [refresh, totalPages],
  );
  useKeyboardShortcuts(shortcutHandlers);

  return (
    <main className="products-page">
      <header>
        <h1>Product Inventory</h1>
        <p>
          {total} {total === 1 ? "product" : "products"}
        </p>
        <button
          type="button"
          className="products-page__help-toggle"
          onClick={() => setHelpOpen((open) => !open)}
          aria-label="Keyboard shortcuts"
          title="Keyboard shortcuts (?)"
        >
          ?
        </button>
      </header>
      <ShortcutsHelp open={helpOpen} onClose={() => setHelpOpen(false)} />

      <div className="products-page__filters">
        <label htmlFor="category-filter">Filter by category</label>
        <select
          id="category-filter"
          value={category}
          onChange={(event) => {
            setCategory(event.target.value);
            setPage(1);
          }}
        >
          <option value="">All categories</option>
          {categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="products-page__error">{error}</p>}

      <table className="products-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Category</th>
            <th>Brand</th>
            <th>Price</th>
            <th>Quantity</th>
            <th aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {loading && (
            <tr>
              <td colSpan={6}>Loading…</td>
            </tr>
          )}
          {!loading && products.length === 0 && (
            <tr>
              <td colSpan={6}>
                {category
                  ? `No products in ${category}.`
                  : "No products yet. Add one below."}
              </td>
            </tr>
          )}
          {!loading &&
            products.map((product) => (
              <tr key={product.id}>
                <td>{product.name}</td>
                <td>{product.category}</td>
                <td>{product.brand}</td>
                <td>${product.price.toFixed(2)}</td>
                <td>{product.quantity}</td>
                <td>
                  <button type="button" onClick={() => setEditing(product)}>
                    Edit
                  </button>
                  <button type="button" onClick={() => remove(product)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
        </tbody>
      </table>

      <div className="products-page__pagination">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => setPage((current) => current - 1)}
        >
          Previous
        </button>
        <span>
          Page {page} of {totalPages}
        </span>
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => setPage((current) => current + 1)}
        >
          Next
        </button>
      </div>

      <ProductForm
        product={editing}
        resetToken={resetToken}
        fieldErrors={fieldErrors}
        submitting={submitting}
        onSubmit={save}
        onCancel={() => {
          setEditing(null);
          setFieldErrors({});
        }}
      />
    </main>
  );
}
