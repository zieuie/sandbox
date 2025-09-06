import React, { useRef } from "react";

export default function ControlPanel({ message, onUndo, canUndo, onExport, onImport, N, D, onDChange, setD, showHidden, toggleShowHidden }) {
    const fileInputRef = useRef(null);
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
                        onChange={(e) => onDChange(Number(e.target.value) || 0)}
                    />
                </label>
            </div>

            <div className="panel__actions">
                <button className="btn" onClick={toggleShowHidden}>
                    {showHidden ? "Hide Hidden Rows" : "Show Hidden Rows"}
                </button>
                <button className="btn" onClick={onUndo} disabled={!canUndo}>Undo</button>
                <button className="btn" onClick={onExport}>Export Grid</button>
                <input
                    type="file"
                    accept=".txt,.dat,.csv,text/plain"
                    onChange={onImport}
                    style={{ display: "none" }}
                    ref={fileInputRef}
                />
                <button className="btn" onClick={() => fileInputRef.current?.click()}>
                    Import Grid
                </button>
            </div>

            <p className="panel__hint">
                Tip: drag numbers left/right within the same row to reorder.
            </p>
        </div>
    );
}