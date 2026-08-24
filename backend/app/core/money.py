"""All money is integer paise. Rupees exist only at the display edge."""


def format_inr(paise: int) -> str:
    """1234500 -> '₹12,345'. Indian digit grouping (last 3, then pairs)."""
    rupees = paise // 100
    s = str(abs(rupees))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    sign = "-" if rupees < 0 else ""
    return f"₹{sign}{s}"


def pct_of(paise: int, pct: float) -> int:
    """Percentage of an amount, floored to whole paise."""
    return int(paise * pct / 100)
