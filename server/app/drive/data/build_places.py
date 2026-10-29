"""Regenerate india_places.tsv from the GeoNames dump (run once; output is committed).

Source: GeoNames (https://www.geonames.org), licensed CC BY 4.0.
    https://download.geonames.org/export/dump/cities1000.zip
    https://download.geonames.org/export/dump/admin1CodesASCII.txt

    python build_places.py <cities1000.txt> <admin1CodesASCII.txt>

Keeps every Indian populated place with at least 1,000 people, its state,
coordinates, population and ASCII alternate names (Bombay -> Mumbai, Bangalore
-> Bengaluru, ...), so place lookup works offline without a map service.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

OUT = Path(__file__).with_name("india_places.tsv")
# Alternates people could actually say: capitalised romanisations of 4+ letters.
# Drops airport codes (BOM, DEL), fragments (Pun) and lowercase transliterations
# (pu na), which would otherwise match ordinary words in speech.
_ASCII_NAME = re.compile(r"^[A-Z][A-Za-z .'-]{3,39}$")


def _speakable(alt: str) -> bool:
    return bool(_ASCII_NAME.match(alt)) and not alt.isupper()


def main(cities_path: str, admin1_path: str) -> None:
    states = {}
    for line in Path(admin1_path).read_text(encoding="utf-8").splitlines():
        code, name, ascii_name, _ = line.split("\t")
        if code.startswith("IN."):
            states[code.split(".", 1)[1]] = ascii_name
    rows = []
    for line in Path(cities_path).read_text(encoding="utf-8").splitlines():
        f = line.split("\t")
        if f[8] != "IN":
            continue
        name = f[2] or f[1]
        alternates = []
        for alt in f[3].split(","):
            alt = alt.strip()
            if _speakable(alt) and alt.lower() != name.lower() and alt.lower() not in (a.lower() for a in alternates):
                alternates.append(alt)
        rows.append((f[0], name, states.get(f[10], ""), f"{float(f[4]):.4f}", f"{float(f[5]):.4f}", f[14], "|".join(alternates[:12])))
    rows.sort(key=lambda r: -int(r[5] or 0))
    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        out.write("# Source: GeoNames (www.geonames.org), CC BY 4.0. Regenerate with build_places.py.\n")
        out.write("geonameid\tname\tstate\tlat\tlon\tpopulation\talternates\n")
        for r in rows:
            out.write("\t".join(r) + "\n")
    print(f"wrote {len(rows)} places to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
