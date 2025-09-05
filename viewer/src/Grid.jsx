import React from "react";
getColor,
onCellClick,
onReorder,
dragging,
dragOver,
setDragging,
setDragOver
}) {
const rowExit = { opacity: 0, y: -8, transition: { duration: 0.18 } };
const rowEnter = { opacity: 1, y: 0 };


return (
<div className="grid-wrap">
<AnimatePresence initial={false}>
{data.map((row, rowIndex) => {
if (hiddenRows.includes(rowIndex)) {
// Keep a placeholder key so exit animation can play for removed rows
return null;
}


return (
<motion.div
key={`row-${rowIndex}`}
layout
initial={{ opacity: 0, y: -8 }}
animate={rowEnter}
exit={rowExit}
transition={{ type: "spring", stiffness: 300, damping: 30 }}
className="row"
>
{row.map((value, colIndex) => {
const isDragging =
dragging &&
dragging.rowIndex === rowIndex &&
dragging.colIndex === colIndex;


const isDragOver =
dragOver &&
dragOver.rowIndex === rowIndex &&
dragOver.colIndex === colIndex;


return (
<Cell
key={`cell-${rowIndex}-${colIndex}`}
value={value}
color={getColor(value, rowIndex, colIndex)}
onClick={() => onCellClick(rowIndex, colIndex)}
draggable
isDragging={isDragging}
isDragOver={isDragOver}
onDragStart={() => setDragging({ rowIndex, colIndex })}
onDragOver={(e) => {
e.preventDefault();
setDragOver({ rowIndex, colIndex });
}}
onDrop={() => {
if (dragging && dragging.rowIndex === rowIndex) {
onReorder(rowIndex, dragging.colIndex, colIndex);
}
setDragging(null);
setDragOver(null);
}}
onDragEnd={() => {
setDragging(null);
setDragOver(null);
}}
/>
);
})}
</motion.div>
);
})}
</AnimatePresence>
</div>
);
}