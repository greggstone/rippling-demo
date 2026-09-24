import React from "react";

import { SHORTCUTS } from "./useKeyboardShortcuts";
import "./ShortcutsHelp.scss";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ShortcutsHelp({ open, onClose }: Props) {
  if (!open) {
    return null;
  }
  return (
    <aside
      className="shortcuts-help"
      role="dialog"
      aria-labelledby="shortcuts-help-title"
    >
      <div className="shortcuts-help__header">
        <h2 id="shortcuts-help-title">Keyboard shortcuts</h2>
        <button type="button" onClick={onClose} aria-label="Close shortcuts">
          ×
        </button>
      </div>
      <dl>
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.keys} className="shortcuts-help__row">
            <dt>
              <kbd>{shortcut.keys}</kbd>
            </dt>
            <dd>{shortcut.description}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
