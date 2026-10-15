"""CVSS v3 base formulas, FIRST section 7.1/7.4 and Appendix A; not a model estimate."""
def calculate_v3_base(vector):
    if not isinstance(vector, str) or not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return None
    parts = [part.split(":", 1) for part in vector.split("/")[1:]]
    if any(len(part) != 2 for part in parts) or len({part[0] for part in parts}) != len(parts):
        raise ValueError("invalid_vector")
    values = dict(parts)
    scope = values["S"]
    if scope not in {"U", "C"}:
        raise ValueError("invalid_scope")
    av = {"N": .85, "A": .62, "L": .55, "P": .2}[values["AV"]]
    ac = {"L": .77, "H": .44}[values["AC"]]
    pr = {"N": .85, "L": .62 if scope == "U" else .68, "H": .27 if scope == "U" else .5}[values["PR"]]
    ui = {"N": .85, "R": .62}[values["UI"]]
    impacts = [{"N": 0, "L": .22, "H": .56}[values[name]] for name in ("C", "I", "A")]
    iss = 1 - (1 - impacts[0]) * (1 - impacts[1]) * (1 - impacts[2])
    impact = 6.42 * iss if scope == "U" else 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15
    if impact <= 0:
        return 0.0
    score = min((impact + 8.22 * av * ac * pr * ui) * (1 if scope == "U" else 1.08), 10)
    integer = round(score * 100000)
    return integer / 100000 if integer % 10000 == 0 else (integer // 10000 + 1) / 10
