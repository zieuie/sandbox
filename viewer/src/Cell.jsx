import React from "react";
import { motion } from "framer-motion";


export default function Cell({
value,
color,
onClick,
draggable,
onDragStart,
onDragOver,
onDrop,
onDragEnd,
isDragging,
isDragOver
}) {
return (
<motion.div
layout
transition={{ type: "spring", stiffness: 300, damping: 30 }}
className="cell"
style={{
backgroundColor: color,
opacity: isDragging ? 0.5 : 1,
outline: isDragOver ? "2px solid #3b82f6" : "none",
border: isDragging ? "2px solid #374151" : "1px solid #e5e7eb"
}}
onClick={onClick}
draggable={draggable}
onDragStart={onDragStart}
onDragOver={onDragOver}
onDrop={onDrop}
onDragEnd={onDragEnd}
title={`${value}`}
>
{value}
</motion.div>
);
}