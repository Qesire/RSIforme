"""Reference starting point: rescan all rows for each group."""


def aggregate(records):
    groups = sorted({row["group"] for row in records})
    result = []
    for group in groups:
        matches = [row["value"] for row in records if row["group"] == group]
        result.append({"group": group, "count": len(matches), "sum": sum(matches)})
    return result
