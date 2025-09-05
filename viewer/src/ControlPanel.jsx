import React from "react";


export default function ControlPanel({ message, onUndo, canUndo }) {
return (
<div className="panel">
<div className="panel__title">Controls</div>
<div className="panel__message" aria-live="polite">{message}</div>
<button className="btn" onClick={onUndo} disabled={!canUndo}>
Undo
</button>
<p className="panel__hint">
Tip: drag numbers left/right within the same row to reorder.
</p>
</div>
);
}