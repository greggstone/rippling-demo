import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ThemeToggle from "../theme/ThemeToggle";
import { THEME_ATTRIBUTE, THEME_STORAGE_KEY } from "../theme/theme";

function mockPrefersDark(matches: boolean) {
  window.matchMedia = jest.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  }));
}

describe("ThemeToggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute(THEME_ATTRIBUTE);
    mockPrefersDark(false);
  });

  it("defaults to the OS color scheme when nothing is saved", () => {
    mockPrefersDark(true);
    render(<ThemeToggle />);
    expect(document.documentElement.getAttribute(THEME_ATTRIBUTE)).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("applies the saved preference on mount", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(document.documentElement.getAttribute(THEME_ATTRIBUTE)).toBe("dark");
    expect(
      screen.getByRole("button", { name: "Switch to light mode" }),
    ).toBeInTheDocument();
  });

  it("toggles the theme attribute and persists the choice", async () => {
    render(<ThemeToggle />);
    expect(document.documentElement.getAttribute(THEME_ATTRIBUTE)).toBe(
      "light",
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Switch to dark mode" }),
    );
    expect(document.documentElement.getAttribute(THEME_ATTRIBUTE)).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await userEvent.click(
      screen.getByRole("button", { name: "Switch to light mode" }),
    );
    expect(document.documentElement.getAttribute(THEME_ATTRIBUTE)).toBe(
      "light",
    );
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
