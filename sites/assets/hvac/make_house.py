"""Draw the HVAC pack's "system check": a house cut open at night, its ducts, furnace and outdoor unit.

    python sites/assets/hvac/make_house.py      # writes house-check.svg next to this file

The page inlines the SVG (``"svg"`` in the pack's explorer) so ``site.css`` (the "air" design) can animate it:
air moving through the supply and return ducts (``.hx-flow``), puffs from the vents, the outdoor unit's fan
turning, the furnace flame and the thermostat's screen. The pins' x / y in ``sites/trades/hvac.json`` are
percent of the 1920 x 1088 view box - keep them in step with ``SPOTS`` below when something moves.
"""
from __future__ import annotations

from pathlib import Path

W, H = 1920, 1088
GROUND = 820

#: where the pins go (x, y in the view box) - copied into hvac.json as percent
SPOTS = {"thermostat": (900, 676), "outdoor": (1690, 742), "furnace": (586, 1000), "filter": (700, 912),
         "ducts": (1060, 262), "vents": (1200, 334)}

#: the ducts, each drawn in the direction the air moves
SUPPLY = [
    "M600 884 V846 H1470",                              # furnace -> basement trunk under the ground floor
    "M840 846 V826", "M1270 846 V826",                  # floor registers downstairs
    "M560 884 V856 H412 V268 H1470",                    # up the chase to the attic trunk
    "M560 268 V298", "M860 268 V298", "M1200 268 V298", "M1440 268 V298",   # ceiling vents upstairs
]
RETURN = [
    "M995 470 V800 H700 V912",                          # the hall return, down to the furnace's filter
]
LINES = [
    "M1614 790 H1580 V870 H640 V876",                   # refrigerant lines: outdoor unit -> the coil
    "M1614 802 H1592 V882 H628 V876",
]


def ducts(paths: list[str], kind: str) -> str:
    out = []
    for d in paths:
        out.append(f'<path class="hx-pipe-edge hx-{kind}-edge" d="{d}"/>')
        out.append(f'<path class="hx-pipe hx-{kind}" d="{d}"/>')
    for d in paths:
        out.append(f'<path class="hx-flow hx-flow-{kind}" d="{d}"/>')
    return "\n    ".join(out)


def vent(x: int, y: int, down: bool = True) -> str:
    """A grille, and three puffs of air coming out of it."""
    sign = 1 if down else -1
    puffs = "".join(
        f'<path class="hx-puff" style="--d:{i}" d="M{x - 26 + i * 4} {y + sign * (10 + i * 2)} q{22 - i * 4} {sign * 10} {44 - i * 8} 0"/>'
        for i in range(3))
    return f'<rect class="hx-grille" x="{x - 26}" y="{y - (4 if down else 0)}" width="52" height="8" rx="2"/><g class="hx-puffs">{puffs}</g>'


def house() -> str:
    stars = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in [
        (120, 90, 2), (260, 170, 1.5), (420, 60, 1.6), (610, 130, 1.2), (1300, 70, 1.5), (1460, 150, 2),
        (1700, 80, 1.6), (1820, 200, 1.2), (1660, 260, 1.4), (90, 300, 1.3), (1840, 420, 1.5), (180, 520, 1.2)])
    vents_up = "".join(vent(x, 300) for x in (560, 860, 1200, 1440))
    vents_down = "".join(vent(x, 820, down=False) for x in (840, 1270))
    blades = "".join(f'<path d="M0 0 C 14 -10 30 -8 44 2 C 30 6 14 6 0 0 Z" transform="rotate({a})"/>' for a in (0, 90, 180, 270))
    return f"""<svg class="hx" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="hx-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0E1830"/><stop offset="1" stop-color="#16233F"/></linearGradient>
    <linearGradient id="hx-soil" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1E1A16"/><stop offset="1" stop-color="#110F0C"/></linearGradient>
    <linearGradient id="hx-window" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFC977" stop-opacity=".34"/><stop offset="1" stop-color="#FF9E4A" stop-opacity=".12"/></linearGradient>
    <linearGradient id="hx-tank" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2A3757"/><stop offset=".5" stop-color="#3A4A70"/><stop offset="1" stop-color="#26324F"/></linearGradient>
    <radialGradient id="hx-glow"><stop offset="0" stop-color="#FFB03A" stop-opacity=".55"/><stop offset="1" stop-color="#FFB03A" stop-opacity="0"/></radialGradient>
    <pattern id="hx-grain" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="4" cy="6" r="1.6" fill="#fff" fill-opacity=".05"/><circle cx="17" cy="19" r="1.1" fill="#fff" fill-opacity=".04"/></pattern>
  </defs>

  <!-- night sky, ground and soil -->
  <rect width="{W}" height="{GROUND}" fill="url(#hx-sky)"/>
  <g class="hx-stars" fill="#DCE6FF" fill-opacity=".5">{stars}</g>
  <circle cx="1700" cy="250" r="46" fill="#F4F1E6" fill-opacity=".1"/><circle cx="1700" cy="250" r="30" fill="#F4F1E6" fill-opacity=".22"/>
  <rect y="{GROUND}" width="{W}" height="{H - GROUND}" fill="url(#hx-soil)"/>
  <rect y="{GROUND}" width="{W}" height="{H - GROUND}" fill="url(#hx-grain)"/>
  <path class="hx-grass" d="M0 {GROUND} H{W}"/>

  <!-- the house -->
  <path class="hx-roof" d="M330 300 L960 90 L1590 300 Z"/>
  <rect class="hx-room" x="380" y="300" width="600" height="260"/>
  <rect class="hx-room" x="980" y="300" width="560" height="260"/>
  <rect class="hx-room" x="380" y="560" width="630" height="260"/>
  <rect class="hx-room" x="1010" y="560" width="530" height="260"/>
  <rect class="hx-base" x="380" y="{GROUND}" width="1160" height="210"/>
  <path class="hx-wall" d="M330 300 H1590 M380 300 V1030 H1540 V300 M380 560 H1540 M380 {GROUND} H1540 M980 300 V560 M1010 560 V{GROUND}"/>
  <path class="hx-floor" d="M380 560 H1540 M380 {GROUND} H1540"/>

  <!-- windows -->
  <g class="hx-windows">
    <rect x="1300" y="360" width="130" height="96" rx="4"/><rect x="1205" y="595" width="96" height="92" rx="4"/>
    <rect x="610" y="610" width="120" height="96" rx="4"/><rect x="640" y="360" width="120" height="96" rx="4"/>
  </g>
  <path class="hx-detail" d="M1365 360 V456 M1300 408 H1430 M1253 595 V687 M1205 641 H1301 M670 610 V706 M610 658 H730 M700 360 V456 M640 408 H760"/>

  <!-- upstairs: two bedrooms -->
  <rect class="hx-fixture" x="440" y="505" width="230" height="55" rx="8"/>
  <rect class="hx-fixture" x="430" y="462" width="16" height="98" rx="4"/>
  <rect class="hx-detail-fill" x="456" y="490" width="62" height="20" rx="8"/>
  <rect class="hx-fixture" x="1150" y="505" width="250" height="55" rx="8"/>
  <rect class="hx-fixture" x="1140" y="462" width="16" height="98" rx="4"/>
  <rect class="hx-detail-fill" x="1166" y="490" width="62" height="20" rx="8"/>
  <rect class="hx-fixture" x="820" y="470" width="110" height="90" rx="6"/>

  <!-- downstairs: living room and kitchen -->
  <path class="hx-fixture" d="M450 820 V770 Q450 752 468 752 H618 Q636 752 636 770 V820 Z"/>
  <path class="hx-detail" d="M470 752 V730 Q470 714 486 714 H600 Q616 714 616 730 V752"/>
  <rect class="hx-fixture" x="1060" y="740" width="400" height="80" rx="4"/>
  <path class="hx-detail" d="M1130 750 V812 M1330 750 V812 M1400 750 V812"/>
  <rect class="hx-fixture" x="1060" y="600" width="120" height="70" rx="4"/>
  <rect class="hx-fixture" x="1340" y="600" width="120" height="70" rx="4"/>

  <!-- the thermostat on the living-room wall, its wire to the furnace -->
  <path class="hx-wire" d="M900 702 V800 H720 V900 H664"/>
  <g class="hx-stat"><rect x="872" y="650" width="56" height="52" rx="12"/><rect class="hx-stat-screen" x="882" y="660" width="36" height="22" rx="5"/><circle cx="900" cy="692" r="3"/></g>

  <!-- the ducts -->
  <g class="hx-pipes">
    {ducts(LINES, "line")}
    {ducts(RETURN, "return")}
    {ducts(SUPPLY, "supply")}
  </g>
  <g class="hx-vents">{vents_up}{vents_down}</g>
  <rect class="hx-grille hx-grille-return" x="975" y="450" width="40" height="40" rx="4"/>
  <path class="hx-detail" d="M979 460 H1011 M979 470 H1011 M979 480 H1011"/>

  <!-- basement: the furnace with its coil on top and the filter in its side -->
  <circle class="hx-heat" cx="600" cy="990" r="70" fill="url(#hx-glow)"/>
  <rect class="hx-tank" x="540" y="884" width="120" height="146" rx="10"/>
  <rect class="hx-coil" x="548" y="868" width="104" height="22" rx="4"/>
  <path class="hx-detail" d="M552 912 H648 M552 1018 H648"/>
  <rect class="hx-burner" x="566" y="972" width="68" height="34" rx="4"/>
  <path class="hx-flame" d="M582 1002 C576 996 578 990 582 984 C586 990 588 996 582 1002 Z M600 1002 C592 994 594 986 600 978 C606 986 608 994 600 1002 Z M618 1002 C612 996 614 990 618 984 C622 990 624 996 618 1002 Z"/>
  <rect class="hx-filter" x="660" y="896" width="16" height="104" rx="3"/>
  <path class="hx-filter-mesh" d="M664 904 L672 916 M664 920 L672 932 M664 936 L672 948 M664 952 L672 964 M664 968 L672 980 M664 984 L672 996"/>

  <!-- outside: the outdoor unit, its fan turning -->
  <rect class="hx-pad" x="1600" y="814" width="180" height="10" rx="3"/>
  <rect class="hx-unit" x="1614" y="680" width="152" height="134" rx="10"/>
  <circle class="hx-unit-grille" cx="1690" cy="747" r="50"/>
  <g transform="translate(1690 747)"><g class="hx-fan">{blades}</g></g>
  <circle class="hx-unit-hub" cx="1690" cy="747" r="7"/>
  <path class="hx-detail" d="M1626 690 V804 M1754 690 V804"/>
  <g class="hx-exhaust"><path d="M1660 670 q10 -18 0 -36"/><path d="M1690 668 q10 -18 0 -36"/><path d="M1720 670 q10 -18 0 -36"/></g>
</svg>
"""


if __name__ == "__main__":
    out = Path(__file__).with_name("house-check.svg")
    out.write_text(house(), encoding="utf-8")
    print(out, {k: (round(x * 100 / W, 2), round(y * 100 / H, 2)) for k, (x, y) in SPOTS.items()})
