import React, { useMemo, memo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Cell from "./Cell";

export default function Grid({ data, hiddenRows, getColor, onCellClick, onRowIndexClick, selected }) {
  const hiddenSet = useMemo(() => new Set(hiddenRows), [hiddenRows]);

  // Only use an exit animation when a row is removed (hidden).
  const rowExit = { opacity: 0, y: -8, transition: { duration: 0.18 } };

  const Row = memo(function Row({ row, rowIndex }) {
    return (
      <motion.div
        key={`row-${rowIndex}`}
        // Disable layout/enter animations to prevent flicker on state changes
        layout={false}
        initial={false}
        exit={rowExit}
        className="row-with-index"
        style={{ display: "flex", alignItems: "center" }}
      >
        <div className="row" style={{ display: "flex", gap: "6px", flex: 1, alignItems: "center" }}>
          <button
            type="button"
            className="row-index-btn"
            onClick={() => onRowIndexClick(rowIndex)}
            title="Hide rows that contain this row's selected/first value"
          >
            {rowIndex}
          </button>

          {row.map((value, colIndex) => {
            const isSelected =
              selected &&
              selected.rowIndex === rowIndex &&
              selected.colIndex === colIndex;

            return (
              <Cell
                key={`cell-${rowIndex}-${colIndex}`}
                value={value}
                color={getColor(value, rowIndex, colIndex)}
                onClick={() => onCellClick(rowIndex, colIndex)}
                isSelected={isSelected}
              />
            );
          })}
        </div>
      </motion.div>
    );
  }, (prev, next) => prev.row === next.row && prev.rowIndex === next.rowIndex);

  return (
    <div className="grid-wrap">
      <AnimatePresence initial={false}>
        {data.map((row, rowIndex) => {
          if (hiddenSet.has(rowIndex)) return null;
          return <Row key={`row-${rowIndex}`} row={row} rowIndex={rowIndex} />;
        })}
      </AnimatePresence>
    </div>
  );
}
