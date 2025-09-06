import React, { memo } from "react";

function Cell({ value, color, onClick, isSelected }) {
  return (
    <div
      className="cell"
      style={{
        backgroundColor: color,
        outline: isSelected ? "2px solid #3b82f6" : "none"
      }}
      onClick={onClick}
    >
      {value}
    </div>
  );
}

export default memo(Cell, (prev, next) => {
  return (
    prev.value === next.value &&
    prev.color === next.color &&
    prev.isSelected === next.isSelected
  );
});
