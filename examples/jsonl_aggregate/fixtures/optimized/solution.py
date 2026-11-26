"""Hand-written fixture: accumulate in one pass, then sort the groups."""


def aggregate(records):
    groups = {}
    for row in records:
        count, total = groups.get(row["group"], (0, 0))
        groups[row["group"]] = (count + 1, total + row["value"])
    return [
        {"group": group, "count": groups[group][0], "sum": groups[group][1]}
        for group in sorted(groups)
    ]
