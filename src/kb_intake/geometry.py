"""Boxes are [x0, y0, x1, y1] in PDF points, TOPLEFT origin."""


def area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a, b) -> float:
    i = inter(a, b)
    u = area(a) + area(b) - i
    return i / u if u > 0 else 0.0


def match_one_to_one(left: list, right: list, threshold: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one pairs (i, j, iou) of boxes with IoU >= threshold, best pair first; ties by (i, j)."""
    pairs = sorted(((iou(a, b), i, j) for i, a in enumerate(left) for j, b in enumerate(right)),
                   key=lambda p: (-p[0], p[1], p[2]))
    used_l, used_r, out = set(), set(), []
    for v, i, j in pairs:
        if v < threshold or i in used_l or j in used_r:
            continue
        used_l.add(i), used_r.add(j)
        out.append((i, j, v))
    return sorted(out)
