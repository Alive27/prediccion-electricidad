"""Masterplan of the energy city: road network, districts and parcels.

Pure Python (no Blender) so it can be inspected and validated on its own:

    python blender/city_masterplan.py        # prints the ASCII plan and checks

Grid: N x N cells of one Kenney road tile (1 native unit). Cell (i, j) has its
centre at native (i - C, j - C); +i is east (+X), +j is north (+Y in Blender,
-Z in glTF/Three.js). Every native unit is scaled by WORLD_SCALE on export so
the whole plan fits the 64 x 64 terrain used by the dashboard scene.
"""

from collections import deque

N = 35
C = 17
WORLD_SCALE = 1.8           # 35 cells * 1.8 = 63 world units (terrain 64 x 64)
TERRAIN_HALF = 32.0 / WORLD_SCALE

# --- Road network -----------------------------------------------------------
# Lines are (axis, fixed, start, end): "h" = row j=fixed from i=start..end,
# "v" = column i=fixed from j=start..end.  Hierarchy is used for lighting,
# tree alignments and energy routes.
ROAD_LINES = {
    "ring": [("h", 2, 2, 32), ("h", 32, 2, 32), ("v", 2, 2, 32), ("v", 32, 2, 32)],
    "avenue": [("h", 17, 2, 32)],
    "collector": [("v", 17, 2, 32), ("h", 8, 2, 32), ("h", 26, 2, 32)],
    "center": [("v", 12, 12, 22), ("v", 22, 12, 22), ("h", 12, 12, 22), ("h", 22, 12, 22)],
    "local": [("v", 8, 2, 15), ("v", 8, 19, 32), ("v", 24, 26, 32),
              ("h", 12, 4, 8), ("h", 22, 4, 8), ("v", 13, 26, 30)],
    "industrial": [("v", 26, 2, 15), ("v", 26, 19, 26)],
}
# Roundabouts occupy 3 x 3 cells, centred on the given cell, exits on all sides.
ROUNDABOUTS = [(8, 17), (26, 17)]
# Pedestrian crossings (straight cells next to the busiest junctions).
CROSSINGS = [(16, 17), (18, 17), (17, 16), (17, 18), (13, 22), (12, 19),
             (21, 12), (22, 15), (17, 9), (17, 25), (10, 17), (24, 17)]
# Driveway tiles: (i, j, side of the road where the parcel is: "N","S","E","W").
DRIVEWAYS = [(21, 8, "S"), (29, 8, "N"), (26, 12, "E"), (26, 5, "E"), (24, 8, "S")]

# --- Parcels (blocks) -------------------------------------------------------
# (name, zone, i0, i1, j0, j1) inclusive cell ranges.
BLOCKS = [
    # Commercial core: three tower blocks around the central crossroads + plaza.
    ("core_sw", "towers", 13, 16, 13, 16),
    ("core_se", "towers", 18, 21, 13, 16),
    ("core_ne", "towers", 18, 21, 18, 21),
    ("core_plaza", "plaza", 13, 16, 18, 21),
    # Mid-rise commercial ring between the centre loop and the collectors.
    ("mid_s_w", "midrise", 9, 16, 9, 11),
    ("mid_s_e", "midrise", 18, 25, 9, 11),
    ("mid_n_w", "midrise", 9, 16, 23, 25),
    ("mid_n_e", "midrise", 18, 25, 23, 25),
    ("mid_w_s", "midrise", 9, 11, 12, 15),
    ("mid_w_n", "midrise", 9, 11, 19, 22),
    ("mid_e_s", "midrise", 23, 25, 12, 15),
    ("mid_e_n", "midrise", 23, 25, 19, 22),
    # Residential neighbourhoods (west, north and north-east).
    ("res_w1", "houses", 3, 7, 3, 7),
    ("res_w2", "houses", 3, 7, 9, 11),
    ("res_w3", "houses", 3, 6, 13, 16),
    ("res_w4", "houses", 3, 6, 18, 21),
    ("res_w5", "houses", 3, 7, 23, 25),
    ("res_w6", "houses", 3, 7, 27, 31),
    ("res_n1", "houses", 9, 12, 27, 31),
    ("res_n2", "houses", 14, 16, 27, 31),
    ("res_n3", "houses", 18, 23, 27, 31),
    ("res_n4", "houses", 25, 31, 27, 31),
    ("res_ne", "houses", 27, 31, 19, 25),
    # Green spaces.
    ("park_south", "park", 9, 16, 3, 7),
    ("buffer_east", "buffer", 28, 31, 16, 16),
    ("buffer_east_n", "buffer", 28, 31, 18, 18),
    # Industrial periphery (south-east) and the electrical substation.
    ("ind_yard", "industrial", 18, 25, 3, 7),
    ("ind_east", "industrial", 27, 31, 9, 15),
    ("substation", "substation", 27, 31, 3, 7),
]

DISTRICT_OF_ZONE = {
    "towers": "COMMERCIAL_DISTRICT", "plaza": "COMMERCIAL_DISTRICT",
    "midrise": "COMMERCIAL_DISTRICT", "houses": "RESIDENTIAL_DISTRICT",
    "park": "VEGETATION", "buffer": "VEGETATION",
    "industrial": "INDUSTRIAL_DISTRICT", "substation": "SUBSTATION",
}

DIRS = {"N": (0, 1), "E": (1, 0), "S": (0, -1), "W": (-1, 0)}


def cell_center(i, j):
    return (i - C, j - C)


def road_cells():
    """Return {(i, j): hierarchy} for every plain road cell (not roundabouts)."""
    cells = {}
    rank = ["local", "industrial", "center", "collector", "ring", "avenue"]
    for kind, lines in ROAD_LINES.items():
        for axis, fixed, a, b in lines:
            for k in range(a, b + 1):
                cell = (k, fixed) if axis == "h" else (fixed, k)
                if cell not in cells or rank.index(kind) > rank.index(cells[cell]):
                    cells[cell] = kind
    for ci, cj in ROUNDABOUTS:
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                cells.pop((ci + di, cj + dj), None)
    return cells


def roundabout_cells():
    out = {}
    for ci, cj in ROUNDABOUTS:
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                out[(ci + di, cj + dj)] = (ci, cj)
    return out


def roundabout_exits():
    """Cells that touch a roundabout exit, mapped to the exit direction."""
    exits = {}
    for ci, cj in ROUNDABOUTS:
        for d, (di, dj) in DIRS.items():
            exits[(ci + 2 * di, cj + 2 * dj)] = d
    return exits


def road_mask(cells, rb, cell):
    """Set of directions in which `cell` connects to another road."""
    i, j = cell
    exits = roundabout_exits()
    mask = set()
    for d, (di, dj) in DIRS.items():
        n = (i + di, j + dj)
        if n in cells:
            mask.add(d)
        elif n in rb and exits.get(cell) == {"N": "S", "S": "N", "E": "W", "W": "E"}[d]:
            mask.add(d)
    return mask


def block_cells(block):
    _, _, i0, i1, j0, j1 = block
    return {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}


def validate_plan():
    """Structural checks: connectivity, parcel overlaps, roads inside terrain."""
    problems = []
    cells = road_cells()
    rb = roundabout_cells()
    # 1. Connectivity over roads + roundabouts.
    graph = set(cells) | set(rb)
    start = next(iter(cells))
    seen, todo = {start}, deque([start])
    while todo:
        i, j = todo.popleft()
        for di, dj in DIRS.values():
            n = (i + di, j + dj)
            if n in graph and n not in seen:
                seen.add(n)
                todo.append(n)
    if seen != graph:
        problems.append(f"disconnected road cells: {sorted(graph - seen)[:10]}")
    # 2. Dead ends must be deliberate cul-de-sacs (rendered with a turning circle).
    dead_ends = [c for c in cells if len(road_mask(cells, rb, c)) == 1]
    # 3. Every roundabout exit must have a road attached.
    for cell in roundabout_exits():
        if cell not in cells:
            problems.append(f"roundabout exit without road at {cell}")
    # 4. Parcels: no overlap with roads, roundabouts or each other; inside ring.
    taken = {}
    for block in BLOCKS:
        for cell in block_cells(block):
            if cell in cells or cell in rb:
                problems.append(f"{block[0]} covers road cell {cell}")
            if cell in taken:
                problems.append(f"{block[0]} overlaps {taken[cell]} at {cell}")
            taken[cell] = block[0]
            if not all(2 < v < 32 for v in cell):
                problems.append(f"{block[0]} outside the ring at {cell}")
    # 5. Crossings and driveways must sit on straight road cells.
    for cell in CROSSINGS + [d[:2] for d in DRIVEWAYS]:
        mask = road_mask(cells, rb, cell) if cell in cells else set()
        if mask not in ({"E", "W"}, {"N", "S"}):
            problems.append(f"crossing/driveway {cell} not on a straight road ({mask})")
    return problems, dead_ends


def ascii_plan():
    cells = road_cells()
    rb = roundabout_cells()
    zone_char = {"towers": "T", "plaza": "Z", "midrise": "M", "houses": "h",
                 "park": "P", "buffer": "g", "industrial": "I", "substation": "S"}
    owner = {}
    for block in BLOCKS:
        for cell in block_cells(block):
            owner[cell] = zone_char[block[1]]
    rows = []
    for j in range(N - 1, -1, -1):
        row = ""
        for i in range(N):
            c = (i, j)
            row += ("O" if c in rb else "+" if c in cells and len(road_mask(cells, rb, c)) > 2
                    else "#" if c in cells else owner.get(c, "."))
        rows.append(f"{j:2d} {row}")
    return "\n".join(rows)


if __name__ == "__main__":
    print(ascii_plan())
    issues, ends = validate_plan()
    print("dead ends (cul-de-sacs):", sorted(ends))
    print("problems:", issues or "none")
