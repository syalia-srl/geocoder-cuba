"""Held-Karp exact solver for the visiting order of a set of stops.

Matrix in, order out — no store, no router, no IO. Kept pure so the one
genuinely algorithmic part of multi-point routing can be tested against
matrices whose optimum is known by inspection.

Exact rather than heuristic: at the 12-point cap the DP is 2^12 * 12^2 ~ 0.6M
steps (instant), which lets the UI promise "the best path" literally. A
nearest-neighbour + 2-opt fallback for larger tours belongs behind this same
signature — see the spec's "Deferred (B)".
"""
from dataclasses import dataclass


@dataclass
class TourOrder:
    order: list          # visiting sequence, always starting at index 0
    total_cost: float    # sum of the legs actually travelled
    feasible: bool       # False when every completion needs an unreachable leg


def solve_tour(matrix, return_to_start: bool) -> TourOrder:
    """Optimal visiting order over `matrix`, starting at index 0.

    `matrix[i][j]` is the cost of going i->j and need not be symmetric: one-way
    streets make the real cost matrix asymmetric, and Held-Karp does not assume
    otherwise. `float("inf")` marks a leg no route can serve.
    """
    n = len(matrix)
    if n == 0:
        return TourOrder(order=[], total_cost=0.0, feasible=False)
    if n == 1:
        return TourOrder(order=[0], total_cost=0.0, feasible=True)

    others = list(range(1, n))
    size = 1 << len(others)
    INF = float("inf")

    # dp[mask][j] = cheapest cost starting at 0, visiting exactly the stops in
    # `mask` (bits index into `others`), and standing at others[j].
    dp = [[INF] * len(others) for _ in range(size)]
    parent = [[-1] * len(others) for _ in range(size)]

    for j, stop in enumerate(others):
        dp[1 << j][j] = matrix[0][stop]

    for mask in range(size):
        for j in range(len(others)):
            cur = dp[mask][j]
            if cur == INF or not (mask >> j) & 1:
                continue
            for k in range(len(others)):
                if (mask >> k) & 1:
                    continue
                step = matrix[others[j]][others[k]]
                if step == INF:
                    continue
                nmask = mask | (1 << k)
                cand = cur + step
                if cand < dp[nmask][k]:
                    dp[nmask][k] = cand
                    parent[nmask][k] = j

    full = size - 1
    best, best_j = INF, -1
    for j, stop in enumerate(others):
        total = dp[full][j]
        if total == INF:
            continue
        if return_to_start:
            back = matrix[stop][0]
            if back == INF:
                continue
            total += back
        if total < best:
            best, best_j = total, j

    if best_j < 0:
        return TourOrder(order=[0] + others, total_cost=INF, feasible=False)

    seq, mask, j = [], full, best_j
    while j >= 0:
        seq.append(others[j])
        pj = parent[mask][j]
        mask ^= 1 << j
        j = pj
    seq.reverse()
    return TourOrder(order=[0] + seq, total_cost=best, feasible=True)
