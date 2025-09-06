import { useCallback, useMemo, useState } from "react";
import ControlPanel from "./components/ControlPanel";
import Grid from "./components/Grid";

// Color mapping helper — tweak as you like
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
  // const idx = Math.abs(Math.floor(value + D)) % palette.length;
  const idx = Math.floor(value / D) % palette.length;
  return palette[idx];
}

export default function App() {
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
  const [hiddenRows, setHiddenRows] = useState([]);
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState("Click a cell to select, then another in the same row to swap.");
  const [N, setN] = useState(initialData[0]?.length ?? 0);
  const [D, setD] = useState(0);
  const [selected, setSelected] = useState(null);
  const [showHidden, setShowHidden] = useState(false);

  const pushHistory = useCallback(() => {
    setHistory((prev) => [
      ...prev,
      { data: data.map((r) => [...r]), hiddenRows: [...hiddenRows], message }
    ]);
  }, [data, hiddenRows, message]);

  // Click-to-swap within the same row
  const handleCellClick = useCallback((rowIndex, colIndex) => {
    if (!selected) {
      setSelected({ rowIndex, colIndex });
      setMessage(`Selected (${rowIndex}, ${colIndex}). Click another in row ${rowIndex} to swap.`);
      return;
    }
    if (selected.rowIndex === rowIndex && selected.colIndex === colIndex) {
      setSelected(null);
      setMessage("Selection cleared.");
      return;
    }
    if (selected.rowIndex !== rowIndex) {
      setSelected(null);
      setMessage(`Row mismatch. Only swap within row ${selected.rowIndex}.`);
      return;
    }

    const fromCol = selected.colIndex;
    const toCol = colIndex;
    if (fromCol === toCol) {
      setSelected(null);
      setMessage("Selection cleared.");
      return;
    }

    pushHistory();
    setData((prev) =>
      prev.map((row, i) => {
        if (i !== rowIndex) return row;
        const newRow = [...row];
        [newRow[fromCol], newRow[toCol]] = [newRow[toCol], newRow[fromCol]];
        return newRow;
      })
    );
    setSelected(null);
    setMessage(`Swapped row ${rowIndex}: ${fromCol} ↔ ${toCol}`);
  }, [selected, pushHistory]);

  const handleUndo = () => {
    if (history.length === 0) return;
    const prev = history[history.length - 1];
    setData(prev.data);
    setHiddenRows(prev.hiddenRows);
    setMessage(prev.message);
    setHistory(history.slice(0, -1));
  };

  const getColor = useCallback((v, r, c) => defaultGetColor(v, r, c, D, N), [D, N]);

  const isSeparated = (u, v) => {
    var ret = false;
    u.forEach((e, i) => {
      if (Math.abs(e - v[i]) >= D) {
        ret = true;
      }
    })
    return ret;
  }

  // Row index button → hide rows containing that value
  const handleRowIndexClick = useCallback((rowIndex) => {
    const col = (selected && selected.rowIndex === rowIndex) ? selected.colIndex : 0;
    const clickedValue = data[rowIndex]?.[col];
    if (clickedValue === undefined) return;

    pushHistory();
    const u = data[rowIndex];
    const newHidden = data.map((v, x) => {
      return isSeparated(u, v) ? x : -1;
    }).filter(e => e >= 0);
    console.log(newHidden);

    setHiddenRows(newHidden);
    setMessage(`Row ${rowIndex} button → hiding rows containing value ${clickedValue}`);
  }, [data, selected, pushHistory]);

  // --- Import/Export helpers ---
  const serializeGrid = useCallback(
    (grid) => grid.map((row) => row.join(" ")).join("\n"),
    []
  );

  const parseGridText = useCallback((text) => {
    const rows = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map((line) =>
        line.split(/\s+/).map((tok) => {
          const n = Number(tok);
          if (!Number.isFinite(n) || !Number.isInteger(n)) {
            throw new Error(`Invalid integer token: "${tok}"`);
          }
          return n;
        })
      );
    if (rows.length === 0) throw new Error("File had no rows");
    const detectedN = rows[0].length;
    if (!rows.every((r) => r.length === detectedN)) {
      throw new Error("Inconsistent row lengths (N must be constant)");
    }
    return { rows, detectedN };
  }, []);

  const handleExport = useCallback(() => {
    const blob = new Blob([serializeGrid(data)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "grid.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [data, serializeGrid]);

  const handleImportFile = useCallback((file) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const text = String(reader.result || "");
        const { rows, detectedN } = parseGridText(text);
        pushHistory();
        setData(rows);
        setHiddenRows([]);
        setSelected(null);
        setN(detectedN);
        setMessage(`Imported ${rows.length} rows (N=${detectedN}) from "${file.name}"`);
      } catch (err) {
        console.error(err);
        alert(`Import failed: ${err.message || err}`);
      }
    };
    reader.onerror = () => alert("Failed to read file");
    reader.readAsText(file);
  }, [parseGridText, pushHistory]);

  return (
    <div className="app">
      <Grid
        data={data}
        hiddenRows={showHidden ? [] : hiddenRows}
        getColor={getColor}
        onCellClick={handleCellClick}
        onRowIndexClick={handleRowIndexClick}
        selected={selected}
      />
      <ControlPanel
        message={message}
        onUndo={handleUndo}
        canUndo={history.length > 0}
        onExport={handleExport}
        onImportFile={handleImportFile}
        N={N}
        D={D}
        setD={setD}
        showHidden={showHidden}
        onToggleShowHidden={() => setShowHidden((s) => !s)}
      />
    </div>
  );
}
