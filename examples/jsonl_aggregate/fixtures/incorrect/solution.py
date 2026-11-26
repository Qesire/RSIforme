"""Intentionally wrong fixture: loses all negative contributions."""


def aggregate(records):
    groups = {}
    for row in records:
        count, total = groups.get(row["group"], (0, 0))
        groups[row["group"]] = (count + 1, total + max(0, row["value"]))
    return [
        {"group": group, "count": groups[group][0], "sum": groups[group][1]}
        for group in sorted(groups)
    ]
