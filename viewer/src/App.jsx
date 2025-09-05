
import React, { useState } from "react";
import Grid from "./Grid";
import ControlPanel from "./ControlPanel";

// Example function to map numbers to colors
function defaultGetColor(value) {
  const colors = ["#fca5a5", "#fdba74", "#fcd34d", "#86efac", "#93c5fd", "#c4b5fd"];
  return colors[value % colors.length];
}

export default function App() {
  // Example 2D array
  const initialData = [
    [1, 2, 3, 4],
    [5, 6, 7, 8],
    [2, 4, 6, 8],
    [9, 1, 4, 7],
  ];

  const [data] = useState(initialData);
  const [hiddenRows, setHiddenRows] = useState([]);
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState("Click a cell to play the game!");
  const [dragging, setDragging] = useState(null);     // { rowIndex, colIndex }
  const [dragOver, setDragOver] = useState(null);     // { rowIndex, colIndex }


  // When a cell is clicked
  const handleCellClick = (rowIndex, colIndex) => {
    // Save previous state to history for undo
    setHistory((prev) => [
      ...prev,
      { hiddenRows, message }
    ]);

    // Example behavior: hide all rows that contain the clicked value
    const clickedValue = data[rowIndex][colIndex];
    const newHidden = data
      .map((row, i) => (row.includes(clickedValue) ? i : null))
      .filter((i) => i !== null);

    setHiddenRows(newHidden);
    setMessage(`You clicked (${rowIndex}, ${colIndex}) → hiding rows with ${clickedValue}`);
  };

  // Undo last action
  const handleUndo = () => {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setHiddenRows(prev.hiddenRows);
    setMessage(prev.message);
    setHistory(history.slice(0, -1));
  };

  return (
    <div className="flex gap-6 p-6">
      <Grid
        data={data}
        hiddenRows={hiddenRows}
        getColor={defaultGetColor}
        onCellClick={handleCellClick}
        onReorder={handleReorder}
        dragging={dragging}
        dragOver={dragOver}
        setDragging={setDragging}
        setDragOver={setDragOver}
      />


      <ControlPanel
        message={message}
        onUndo={handleUndo}
        canUndo={history.length > 0}
      />
    </div>
  );
}
