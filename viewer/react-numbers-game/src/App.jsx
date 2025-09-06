import React, { useMemo, useState } from "react";
import Grid from "./components/Grid";
import ControlPanel from "./components/ControlPanel";

// Color mapping helper — tweak as you like
// Color mapping helper — now receives D and N
function defaultGetColor(value, _row, _col, D, N) {
  const palette = [
    "#ff5757ff", // red
    "#ff902fff", // orange
    "#ffff47ff", // yellow
    "#97ff97ff", // green
    "#5151ffff", // blue
    "#98ffffff", // cyan
    "#ff84ffff", // magenta
    "#c0c0c0ff", // black
  ];
  // const idx = Math.abs(Math.floor(value)) % palette.length;
  // Simple example: shift color index by D (and touch N to show availability)
  // Feel free to replace with your own mapping.
  // const idx = Math.abs(Math.floor(value + D)) % palette.length;
  const idx = Math.floor(value / D) % palette.length;
  return palette[idx];
}

export default function App() {
  // Example starting data — replace with your own 2D array
  const initialData = useMemo(
    () => [
      [1, 2, 3, 4, 5, 6, 7],
      [5, 6, 7, 8, 9, 10, 11],
      [2, 4, 6, 8, 10, 12, 14],
      [9, 1, 4, 7, 3, 8, 2],
      [3, 1, 4, 1, 5, 9, 2]
    ],
    []
  );

  const [data, setData] = useState(initialData);
  const [N, setN] = useState(initialData[0]?.length ?? 0); // row length (auto)
  const [D, setD] = useState(0); // user-provided parameter for coloring

  const [hiddenRows, setHiddenRows] = useState([]); // array of row indices
  const [history, setHistory] = useState([]); // stack of previous states
  const [message, setMessage] = useState("Click a cell to play the game — or drag within a row to reorder.");

  const [dragging, setDragging] = useState(null); // { rowIndex, colIndex }
  const [dragOver, setDragOver] = useState(null); // { rowIndex, colIndex }
  const [showHidden, setShowHidden] = useState(false);
  const [clickedRow, setClickedRow] = useState(null); // { rowIndex, colIndex }
  const [clickedCol, setClickedCol] = useState(null); // { rowIndex, colIndex }


  // Helper to push current state to history for undo
  const pushHistory = () => {
    setHistory((prev) => [
      ...prev,
      {
        data: data.map((r) => [...r]), // deep-ish copy for 2D array
        hiddenRows: [...hiddenRows],
        message
      }
    ]);
  };

  const isSeparated = (u, v) => {
    var ret = false;
    u.forEach((e,i) => {
      if (Math.abs(e-v[i]) >= D) {
        ret = true;
      }
    })
    return ret;
  }

  // Click logic: sample rule — hide all rows containing the clicked value
  const handleCellClick = (rowIndex, colIndex) => {
    pushHistory();

    const u = data[rowIndex];
    const newHidden = data.map((v,x) => {
      return isSeparated(u,v) ? -1 : x;
        // var ret = false;
        // v.forEach((e,i) => {
        //   if (Math.abs(e-u[i]) >= D) {
        //     ret = true;
        //   }
        // })
        // return ret ? -1 : x
    });
    console.log(newHidden);

    setClickedRow(rowIndex);
    setClickedCol(colIndex);
    setHiddenRows(newHidden);
    setMessage(`Clicked row ${rowIndex} - hiding ${newHidden.length} separated`);
  };

  // Reorder values within a row via drag & drop
  const handleReorder = (rowIndex, fromCol, toCol) => {
    if (fromCol === toCol) return;
    pushHistory();


    setData((prev) =>
      prev.map((row, i) => {
        if (i !== rowIndex) return row;
        const newRow = [...row];
        // swap values (no shifting of other cells)
        [newRow[fromCol], newRow[toCol]] = [newRow[toCol], newRow[fromCol]];
        return newRow;
      })
    );


    setMessage(`Swapped row ${rowIndex}: ${fromCol} ↔ ${toCol}`);
  };

  // Undo last action
  const handleUndo = () => {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setData(prev.data);
    setHiddenRows(prev.hiddenRows);
    setMessage(prev.message);
    setHistory(history.slice(0, -1));
  };

  // Export current grid as text file
  const handleExport = () => {
    const text = data.map(row => row.join(" ")).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "grid.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  // Import grid from file (robust + no hoisting on pushHistory)
  const handleImport = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const text = String(event.target?.result ?? "");
        const rows = text
          .trim()
          .split(/\r?\n/)
          .filter((line) => line.trim().length > 0)
          .map((line) =>
            line.trim().split(/\s+/).map((tok) => {
              const n = Number(tok);
              if (!Number.isFinite(n) || !Number.isInteger(n)) {
                throw new Error(`Invalid integer: ${tok}`);
              }
              return n;
            })
          );

        // Validate consistent row length and set N
        const detectedN = rows[0].length;
        if (!rows.every((row) => row.length === detectedN)) {
          setMessage("Inconsistent row lengths in file (N is not constant)");
          throw new Error("Inconsistent row lengths in file (N is not constant)");
        }


        // push history inline (avoid reliance on pushHistory order)
        setHistory((prev) => [
          ...prev,
          { data: data.map((r) => [...r]), hiddenRows: [...hiddenRows], message },
        ]);
        setData(rows);
        setHiddenRows([]);
        setMessage(`Imported new grid from "${file.name}"`);

        setN(detectedN);
        setMessage(`Imported ${rows.length} rows (N=${detectedN}) from "${file.name}"`);


        // allow importing the same file again
        e.target.value = "";
      } catch (err) {
        console.error(err);
        alert(`Import failed: ${err.message || err}`);
      }
    };
    reader.onerror = () => alert("Failed to read file");
    reader.readAsText(file);
  };


  return (
    <div className="app">
      <Grid
        data={data}
        hiddenRows={showHidden ? [] : hiddenRows}
        getColor={(v, r, c) => defaultGetColor(v, r, c, D, N)}
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
        onExport={handleExport}
        onImport={handleImport}
        N={N}
        D={D}
        onDChange={setD}
        setD={setD}
        showHidden={showHidden}
        toggleShowHidden={() => setShowHidden(!showHidden)}
      />
    </div>
  );
}