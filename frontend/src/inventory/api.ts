export interface Product {
  id: string;
  name: string;
  description: string;
  category: string;
  brand: string;
  price: number;
  quantity: number;
  created_at?: string;
  updated_at?: string;
}

export type ProductInput = Omit<Product, "id" | "created_at" | "updated_at">;

export interface ProductPage {
  results: Product[];
  total: number;
  page: number;
  page_size: number;
}

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: Record<string, string>;

  constructor(
    message: string,
    status: number,
    fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

const BASE_URL = process.env.REACT_APP_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    let fieldErrors: Record<string, string> = {};
    try {
      fieldErrors = (await response.json()).errors ?? {};
    } catch {
      // response body was not JSON
    }
    throw new ApiError(
      `Request failed with status ${response.status}`,
      response.status,
      fieldErrors,
    );
  }

  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}

export function listProducts(params: {
  page: number;
  pageSize: number;
  category?: string;
}): Promise<ProductPage> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.category) {
    query.set("category", params.category);
  }
  return request<ProductPage>(`/products/?${query.toString()}`);
}

export function listCategories(): Promise<{ results: string[] }> {
  return request<{ results: string[] }>("/products/categories/");
}

export function createProduct(product: ProductInput): Promise<Product> {
  return request<Product>("/products/", {
    method: "POST",
    body: JSON.stringify(product),
  });
}

export function updateProduct(
  id: string,
  product: ProductInput,
): Promise<Product> {
  return request<Product>(`/products/${id}/`, {
    method: "PUT",
    body: JSON.stringify(product),
  });
}

export function deleteProduct(id: string): Promise<void> {
  return request<void>(`/products/${id}/`, { method: "DELETE" });
}
