"""Draw the plumber pack's "plumbing check": a house cut open at night, its water pipes and drains.

    python sites/assets/plumber/make_house.py      # writes house-check.svg next to this file

The page inlines the SVG (``"svg"`` in the pack's explorer) so ``site.css`` can animate it: water moving
through every pipe (``.hx-flow``), the shower running, a dripping faucet, the water heater's flame. The
pins' x / y in ``sites/trades/plumber.json`` are percent of the 1920 x 1088 view box - keep them in step
with ``SPOTS`` below when something moves.
"""
from __future__ import annotations

from pathlib import Path

W, H = 1920, 1088
GROUND = 820

#: where the pins go (x, y in the view box) - copied into plumber.json as percent
SPOTS = {"water-heater": (592, 960), "shutoff": (420, 962), "kitchen": (1250, 712), "toilet": (830, 478),
         "shower": (560, 420), "sewer": (1300, 1008)}

#: the pipes, each drawn in the direction the water moves
COLD = [
    "M200 838 V962 H470 V850 H1520 V760 H1572",          # street -> shut-off -> basement run -> hose bib
    "M570 850 V895",                                    # into the water heater
    "M720 850 V612 H915 V504",                          # up to the bathroom sink
    "M720 612 H680 V436",                               # the shower valve
    "M795 612 V512",                                    # the toilet tank
    "M1230 850 V748",                                   # the kitchen sink
]
HOT = [
    "M615 895 V870 H1262 V748",                         # water heater -> kitchen sink
    "M740 870 V628 H935 V504",                          # up to the bathroom sink
    "M740 628 H700 V436",                               # the shower valve
]
DRAIN = [
    "M450 562 V574 L990 592",                           # the tub
    "M842 562 V585",                                    # the toilet
    "M925 562 V588",                                    # the sink
    "M1250 762 V786 L1000 798",                         # the kitchen sink
    "M990 300 V1000 L1540 1012 L1920 1030",             # the stack, then the sewer line to the street
]


def pipes(paths: list[str], kind: str) -> str:
    out = []
    for d in paths:
        out.append(f'<path class="hx-pipe-edge hx-{kind}-edge" d="{d}"/>')
        out.append(f'<path class="hx-pipe hx-{kind}" d="{d}"/>')
    for d in paths:
        out.append(f'<path class="hx-flow hx-flow-{kind}" d="{d}"/>')
    return "\n    ".join(out)


def house() -> str:
    stars = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in [
        (120, 90, 2), (260, 170, 1.5), (420, 60, 1.6), (610, 130, 1.2), (1300, 70, 1.5), (1460, 150, 2),
        (1700, 80, 1.6), (1820, 200, 1.2), (1660, 260, 1.4), (90, 300, 1.3), (1840, 420, 1.5), (180, 520, 1.2)])
    return f"""<svg class="hx" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="hx-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0E1830"/><stop offset="1" stop-color="#16233F"/></linearGradient>
    <linearGradient id="hx-soil" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1E1A16"/><stop offset="1" stop-color="#110F0C"/></linearGradient>
    <linearGradient id="hx-window" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFC977" stop-opacity=".34"/><stop offset="1" stop-color="#FF9E4A" stop-opacity=".12"/></linearGradient>
    <linearGradient id="hx-tank" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#2A3757"/><stop offset=".5" stop-color="#3A4A70"/><stop offset="1" stop-color="#26324F"/></linearGradient>
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
    <rect x="610" y="610" width="120" height="96" rx="4"/>
  </g>
  <path class="hx-detail" d="M1365 360 V456 M1300 408 H1430 M1253 595 V687 M1205 641 H1301 M670 610 V706 M610 658 H730"/>

  <!-- upstairs: bathroom -->
  <rect class="hx-fixture" x="420" y="490" width="230" height="70" rx="12"/>
  <path class="hx-detail" d="M410 330 H660 M432 330 V470 M650 330 V470"/>
  <path class="hx-fixture-line" d="M700 436 V352 H640 M626 352 H654 L648 364 H632 Z"/>
  <g class="hx-spray"><path d="M632 372 L612 486 M640 372 L632 486 M648 372 L652 486 M656 372 L672 486"/></g>
  <circle class="hx-valve" cx="690" cy="436" r="10"/>
  <rect class="hx-fixture" x="780" y="440" width="34" height="72" rx="6"/>
  <path class="hx-fixture" d="M808 500 H876 Q876 520 856 522 L862 560 H826 L832 522 Q810 518 808 500 Z"/>
  <rect class="hx-fixture" x="888" y="502" width="74" height="58" rx="4"/>
  <path class="hx-fixture-line" d="M884 500 H966 M925 500 V486 H936"/>
  <rect class="hx-mirror" x="900" y="380" width="50" height="78" rx="8"/>

  <!-- upstairs: bedroom -->
  <rect class="hx-fixture" x="1150" y="505" width="250" height="55" rx="8"/>
  <rect class="hx-fixture" x="1140" y="462" width="16" height="98" rx="4"/>
  <rect class="hx-detail-fill" x="1166" y="490" width="62" height="20" rx="8"/>

  <!-- downstairs: living room and laundry -->
  <rect class="hx-fixture" x="420" y="730" width="92" height="90" rx="6"/>
  <circle class="hx-fixture-line" cx="466" cy="782" r="24"/>
  <path class="hx-fixture" d="M780 820 V770 Q780 752 798 752 H948 Q966 752 966 770 V820 Z"/>
  <path class="hx-detail" d="M800 752 V730 Q800 714 816 714 H930 Q946 714 946 730 V752"/>

  <!-- downstairs: kitchen -->
  <rect class="hx-fixture" x="1060" y="740" width="400" height="80" rx="4"/>
  <path class="hx-detail" d="M1130 750 V812 M1330 750 V812 M1400 750 V812"/>
  <rect class="hx-sink" x="1196" y="740" width="110" height="20" rx="4"/>
  <path class="hx-fixture-line" d="M1250 740 V700 Q1250 688 1262 688 H1278 V698"/>
  <circle class="hx-drip" cx="1278" cy="704" r="5"/>
  <rect class="hx-fixture" x="1060" y="600" width="120" height="70" rx="4"/>
  <rect class="hx-fixture" x="1340" y="600" width="120" height="70" rx="4"/>
  <rect class="hx-fixture" x="1472" y="616" width="58" height="204" rx="6"/>

  <!-- outside: water meter at the curb, a hose bib on the wall -->
  <rect class="hx-fixture" x="168" y="822" width="64" height="20" rx="3"/>
  <path class="hx-fixture-line" d="M1572 752 H1590 V770"/>

  <!-- the pipes -->
  <g class="hx-pipes">
    {pipes(DRAIN, "drain")}
    {pipes(COLD, "cold")}
    {pipes(HOT, "hot")}
  </g>
  <path class="hx-fixture-line" d="M990 300 V72"/>
  <rect class="hx-fixture" x="1072" y="992" width="18" height="18" rx="3"/>

  <!-- basement: the water heater and the main shut-off -->
  <rect class="hx-tank" x="545" y="895" width="95" height="135" rx="24"/>
  <path class="hx-detail" d="M552 930 H633 M552 990 H633"/>
  <rect class="hx-burner" x="577" y="1004" width="30" height="18" rx="3"/>
  <path class="hx-flame" d="M592 1020 C584 1014 586 1008 592 1002 C598 1008 600 1014 592 1020 Z"/>
  <g class="hx-shutoff"><circle cx="420" cy="962" r="18"/><path d="M420 944 V980 M402 962 H438"/></g>
</svg>
"""


if __name__ == "__main__":
    out = Path(__file__).with_name("house-check.svg")
    out.write_text(house(), encoding="utf-8")
    print(out, {k: (round(x * 100 / W, 2), round(y * 100 / H, 2)) for k, (x, y) in SPOTS.items()})
