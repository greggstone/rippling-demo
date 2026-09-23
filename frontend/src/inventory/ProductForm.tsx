import React, { useEffect, useState } from "react";

import { Product, ProductInput } from "./api";

const EMPTY: ProductInput = {
  name: "",
  description: "",
  category: "",
  brand: "",
  price: 0,
  quantity: 0,
};

function toInput(product: Product | null): ProductInput {
  if (!product) {
    return EMPTY;
  }
  const { id, created_at, updated_at, ...input } = product;
  return input;
}

interface Props {
  product: Product | null;
  fieldErrors: Record<string, string>;
  submitting: boolean;
  onSubmit: (product: ProductInput) => void;
  onCancel: () => void;
}

export default function ProductForm({
  product,
  fieldErrors,
  submitting,
  onSubmit,
  onCancel,
}: Props) {
  const [values, setValues] = useState<ProductInput>(toInput(product));

  useEffect(() => {
    setValues(toInput(product));
  }, [product]);

  const update =
    (field: keyof ProductInput) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const { value } = event.target;
      setValues((current) => ({
        ...current,
        [field]:
          field === "price" || field === "quantity" ? Number(value) : value,
      }));
    };

  return (
    <form
      className="product-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(values);
      }}
    >
      <h2>{product ? "Edit product" : "Add product"}</h2>

      <label htmlFor="name">Name</label>
      <input id="name" value={values.name} onChange={update("name")} />
      {fieldErrors.name && <p className="field-error">{fieldErrors.name}</p>}

      <label htmlFor="category">Category</label>
      <input
        id="category"
        value={values.category}
        onChange={update("category")}
      />
      {fieldErrors.category && (
        <p className="field-error">{fieldErrors.category}</p>
      )}

      <label htmlFor="brand">Brand</label>
      <input id="brand" value={values.brand} onChange={update("brand")} />

      <label htmlFor="price">Price</label>
      <input
        id="price"
        type="number"
        step="0.01"
        value={values.price}
        onChange={update("price")}
      />
      {fieldErrors.price && <p className="field-error">{fieldErrors.price}</p>}

      <label htmlFor="quantity">Quantity</label>
      <input
        id="quantity"
        type="number"
        value={values.quantity}
        onChange={update("quantity")}
      />
      {fieldErrors.quantity && (
        <p className="field-error">{fieldErrors.quantity}</p>
      )}

      <label htmlFor="description">Description</label>
      <textarea
        id="description"
        value={values.description}
        onChange={update("description")}
      />

      <div className="product-form__actions">
        <button type="submit" disabled={submitting}>
          {product ? "Save changes" : "Add product"}
        </button>
        {product && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
