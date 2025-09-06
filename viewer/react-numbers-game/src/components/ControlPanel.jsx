import React, { useRef } from "react";

export default function ControlPanel({
  message,
  onUndo,
  canUndo,
  onExport,
  onImportFile,
  N,
  D,
  setD,
  showHidden,
  onToggleShowHidden
}) {
  const fileRef = useRef(null);

  return (
    <div className="panel">
      <div className="panel__title">Controls</div>
      <div className="panel__message" aria-live="polite">{message}</div>

      <div className="panel__fields">
        <label className="field">
          <span>N (row length)</span>
          <input className="input" type="number" value={N} readOnly />
        </label>
        <label className="field">
          <span>D</span>
          <input
            className="input"
            type="number"
            value={D}
            onChange={(e) => setD(Number(e.target.value) || 0)}
          />
        </label>
      </div>

      <div className="panel__actions">
        <button className="btn" onClick={onUndo} disabled={!canUndo}>Undo</button>
        <button className="btn" onClick={onExport}>Export Grid</button>

        {/* Hidden file input + button to open it */}
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.dat,.csv,text/plain"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onImportFile(f);
            e.target.value = ""; // allow re-importing the same file
          }}
        />
        <button className="btn" onClick={() => fileRef.current?.click()}>
          Import Grid
        </button>

        <button className="btn" onClick={onToggleShowHidden}>
          {showHidden ? "Hide Hidden Rows" : "Show Hidden Rows"}
        </button>
      </div>

      <p className="panel__hint">
        Tip: click a cell to select, then another in the same row to swap.
        Click the row number to hide rows containing its value.
      </p>
    </div>
  );
}
