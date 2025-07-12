from gurobipy import Model, GRB
import numpy as np

def solve_sudoku(grid):
    model = Model("Sudoku")
    
    # Decision variables: x[i,j,k] = 1 if digit k is in cell (i,j), else 0
    x = model.addVars(9, 9, 9, vtype=GRB.BINARY, name="x")
    
    # Constraint: Each cell must have exactly one number
    for i in range(9):
        for j in range(9):
            model.addConstr(sum(x[i, j, k] for k in range(9)) == 1)
    
    # Constraint: Each number appears once per row
    for i in range(9):
        for k in range(9):
            model.addConstr(sum(x[i, j, k] for j in range(9)) == 1)
    
    # Constraint: Each number appears once per column
    for j in range(9):
        for k in range(9):
            model.addConstr(sum(x[i, j, k] for i in range(9)) == 1)
    
    # Constraint: Each number appears once per 3x3 subgrid
    for bi in range(3):
        for bj in range(3):
            for k in range(9):
                model.addConstr(sum(x[i, j, k] 
                                    for i in range(bi * 3, (bi + 1) * 3) 
                                    for j in range(bj * 3, (bj + 1) * 3)) == 1)
    
    # Constraint: Pre-filled cells from the input grid
    for i in range(9):
        for j in range(9):
            if grid[i][j] != 0:
                model.addConstr(x[i, j, grid[i][j] - 1] == 1)
    
    # Optimize model
    model.optimize()
    
    # Extract solution
    if model.status == GRB.OPTIMAL:
        solution = np.zeros((9, 9), dtype=int)
        for i in range(9):
            for j in range(9):
                for k in range(9):
                    if x[i, j, k].x > 0.5:
                        solution[i, j] = k + 1
        return solution
    else:
        return None

# Example Sudoku puzzle (0 represents empty cells)
grid = [
    [5, 3, 0, 0, 7, 0, 0, 0, 0],
    [6, 0, 0, 1, 9, 5, 0, 0, 0],
    [0, 9, 8, 0, 0, 0, 0, 6, 0],
    [8, 0, 0, 0, 6, 0, 0, 0, 3],
    [4, 0, 0, 8, 0, 3, 0, 0, 1],
    [7, 0, 0, 0, 2, 0, 0, 0, 6],
    [0, 6, 0, 0, 0, 0, 2, 8, 0],
    [0, 0, 0, 4, 1, 9, 0, 0, 5],
    [0, 0, 0, 0, 8, 0, 0, 7, 9]
]

solution = solve_sudoku(grid)
if solution is not None:
    print("Sudoku solution:")
    print(solution)
else:
    print("No solution found.")
