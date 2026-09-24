import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import useKeyboardShortcuts, {
  ShortcutHandlers,
} from "../inventory/useKeyboardShortcuts";

function makeHandlers(): ShortcutHandlers {
  return {
    focusAddForm: jest.fn(),
    cancel: jest.fn(),
    nextPage: jest.fn(),
    previousPage: jest.fn(),
    focusFilter: jest.fn(),
    refresh: jest.fn(),
    toggleHelp: jest.fn(),
  };
}

function Harness({
  handlers,
  onSubmit,
}: {
  handlers: ShortcutHandlers;
  onSubmit: () => void;
}) {
  useKeyboardShortcuts(handlers);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <input aria-label="Name" />
      <textarea aria-label="Description" />
    </form>
  );
}

describe("useKeyboardShortcuts", () => {
  let handlers: ShortcutHandlers;
  let onSubmit: jest.Mock;

  const setup = () => {
    handlers = makeHandlers();
    onSubmit = jest.fn();
    render(<Harness handlers={handlers} onSubmit={onSubmit} />);
  };

  test.each([
    ["n", "focusAddForm"],
    ["]", "nextPage"],
    ["ArrowRight", "nextPage"],
    ["[", "previousPage"],
    ["ArrowLeft", "previousPage"],
    ["/", "focusFilter"],
    ["r", "refresh"],
    ["?", "toggleHelp"],
    ["Escape", "cancel"],
  ] as const)("'%s' triggers %s", (key, handler) => {
    setup();
    fireEvent.keyDown(document.body, { key });

    expect(handlers[handler]).toHaveBeenCalledTimes(1);
    Object.entries(handlers)
      .filter(([name]) => name !== handler)
      .forEach(([, fn]) => expect(fn).not.toHaveBeenCalled());
  });

  test("ignores single-key shortcuts while typing in an input", async () => {
    setup();
    const input = screen.getByLabelText("Name");
    await userEvent.type(input, "n]r/?[[");

    expect(input).toHaveValue("n]r/?[");
    expect(handlers.focusAddForm).not.toHaveBeenCalled();
    expect(handlers.nextPage).not.toHaveBeenCalled();
    expect(handlers.previousPage).not.toHaveBeenCalled();
    expect(handlers.focusFilter).not.toHaveBeenCalled();
    expect(handlers.refresh).not.toHaveBeenCalled();
    expect(handlers.toggleHelp).not.toHaveBeenCalled();
  });

  test("ignores arrow keys while typing in a textarea", () => {
    setup();
    const textarea = screen.getByLabelText("Description");
    fireEvent.keyDown(textarea, { key: "ArrowRight" });
    fireEvent.keyDown(textarea, { key: "ArrowLeft" });

    expect(handlers.nextPage).not.toHaveBeenCalled();
    expect(handlers.previousPage).not.toHaveBeenCalled();
  });

  test("ignores shortcuts combined with modifier keys", () => {
    setup();
    fireEvent.keyDown(document.body, { key: "r", ctrlKey: true });
    fireEvent.keyDown(document.body, { key: "n", metaKey: true });

    expect(handlers.refresh).not.toHaveBeenCalled();
    expect(handlers.focusAddForm).not.toHaveBeenCalled();
  });

  test("Ctrl+Enter inside the form submits it", () => {
    setup();
    fireEvent.keyDown(screen.getByLabelText("Name"), {
      key: "Enter",
      ctrlKey: true,
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  test("Cmd+Enter inside the form submits it", () => {
    setup();
    fireEvent.keyDown(screen.getByLabelText("Description"), {
      key: "Enter",
      metaKey: true,
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  test("Ctrl+Enter outside a form does nothing", () => {
    setup();
    fireEvent.keyDown(document.body, { key: "Enter", ctrlKey: true });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  test("Escape while typing blurs the field and cancels", () => {
    setup();
    const input = screen.getByLabelText("Name");
    input.focus();
    fireEvent.keyDown(input, { key: "Escape" });

    expect(handlers.cancel).toHaveBeenCalledTimes(1);
    expect(input).not.toHaveFocus();
  });
});
