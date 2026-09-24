import { useEffect } from "react";

export interface ShortcutHandlers {
  focusAddForm: () => void;
  cancel: () => void;
  nextPage: () => void;
  previousPage: () => void;
  focusFilter: () => void;
  refresh: () => void;
  toggleHelp: () => void;
}

export interface ShortcutDescription {
  keys: string;
  description: string;
}

export const SHORTCUTS: ShortcutDescription[] = [
  { keys: "n", description: "Focus the add product form" },
  { keys: "Ctrl+Enter", description: "Submit the form (while in the form)" },
  { keys: "Esc", description: "Cancel editing / clear the form" },
  { keys: "] or →", description: "Next page" },
  { keys: "[ or ←", description: "Previous page" },
  { keys: "/", description: "Focus the category filter" },
  { keys: "r", description: "Refresh the product list" },
  { keys: "?", description: "Toggle this help panel" },
];

const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTyping(target: EventTarget | null): target is HTMLElement {
  return (
    target instanceof HTMLElement &&
    (EDITABLE_TAGS.has(target.tagName) || target.isContentEditable)
  );
}

export function submitClosestForm(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const form = target.closest("form");
  if (!form) {
    return false;
  }
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  }
  return true;
}

export default function useKeyboardShortcuts(handlers: ShortcutHandlers) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) {
        return;
      }
      const typing = isTyping(event.target);

      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        if (typing && submitClosestForm(event.target)) {
          event.preventDefault();
        }
        return;
      }

      if (event.key === "Escape") {
        if (typing) {
          event.target.blur();
        }
        handlers.cancel();
        return;
      }

      if (typing || event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }

      switch (event.key) {
        case "n":
          handlers.focusAddForm();
          break;
        case "]":
        case "ArrowRight":
          handlers.nextPage();
          break;
        case "[":
        case "ArrowLeft":
          handlers.previousPage();
          break;
        case "/":
          handlers.focusFilter();
          break;
        case "r":
          handlers.refresh();
          break;
        case "?":
          handlers.toggleHelp();
          break;
        default:
          return;
      }
      event.preventDefault();
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [handlers]);
}
