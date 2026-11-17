"""Draw the Electrician pack's "power check": a house cut open at night, the power coming in and its circuits.

    python sites/assets/electrician/make_house.py      # writes house-check.svg next to this file

The page inlines the SVG (``"svg"`` in the pack's explorer) so ``site.css`` (the "spark" design) can animate it:
current running from the pole through the meter to the panel and out along each circuit (``.hx-flow``), the
meter's disc turning, the lights glowing, the EV charger's light and the car's battery filling, the smoke
alarms blinking. The pins' x / y in ``sites/trades/electrician.json`` are percent of the 1920 x 1088 view
box - keep them in step with ``SPOTS`` below when something moves.
"""
from __future__ import annotations

from pathlib import Path

W, H = 1920, 1088
GROUND = 820

#: where the pins go (x, y in the view box) - copied into electrician.json as percent
SPOTS = {"panel": (1480, 918), "meter": (1576, 698), "outlets": (1300, 768), "lights": (700, 336),
         "ev": (346, 730), "smoke": (1200, 314)}

#: the service: from the pole to the weatherhead, down to the meter and into the panel
SERVICE = [
    "M1840 404 Q1712 470 1578 452",                       # the drop from the pole
    "M1578 452 V672",                                      # down the mast to the meter
    "M1578 724 V880 H1522",                                # meter -> the panel in the basement
]
#: the circuits, drawn from the panel out
CIRCUITS = [
    "M1440 896 H1320 V778",                                # kitchen outlets (on their own circuit)
    "M1440 878 H1416 V312 H640",                           # upstairs: ceiling lights and smoke alarms
    "M700 312 V326", "M1200 312 V304", "M860 312 V304",    # drops to the lights / alarms
    "M1440 942 H560 V572",                                 # downstairs: the living-room light
    "M900 942 V790",                                       # a living-room outlet
    "M1440 964 H400 V740 H372",                            # the EV charger outside
]


def wires(paths: list[str], kind: str) -> str:
    out = []
    for d in paths:
        out.append(f'<path class="hx-pipe-edge hx-{kind}-edge" d="{d}"/>')
        out.append(f'<path class="hx-pipe hx-{kind}" d="{d}"/>')
    for d in paths:
        out.append(f'<path class="hx-flow hx-flow-{kind}" d="{d}"/>')
    return "\n    ".join(out)


def lamp(x: int, y: int, i: int) -> str:
    """A ceiling light: the shade, the bulb and its glow."""
    return (f'<g class="sx-lamp" style="--d:{i}"><circle class="sx-glow" cx="{x}" cy="{y + 22}" r="70" fill="url(#sx-light)"/>'
            f'<path class="hx-fixture" d="M{x - 26} {y + 18} L{x - 12} {y} H{x + 12} L{x + 26} {y + 18} Z"/>'
            f'<circle class="sx-bulb" cx="{x}" cy="{y + 22}" r="7"/></g>')


def house() -> str:
    stars = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in [
        (120, 90, 2), (260, 170, 1.5), (420, 60, 1.6), (610, 130, 1.2), (1300, 70, 1.5), (1460, 150, 2),
        (1700, 80, 1.6), (1820, 200, 1.2), (1660, 260, 1.4), (90, 300, 1.3), (1840, 320, 1.5), (180, 520, 1.2)])
    breakers = "".join(f'<rect class="sx-breaker{" is-off" if (r, c) == (2, 1) else ""}" x="{1452 + c * 30}" y="{876 + r * 22}" width="22" height="14" rx="3"/>'
                       for r in range(4) for c in range(2))
    return f"""<svg class="hx" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="hx-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0D0F1A"/><stop offset="1" stop-color="#171A2B"/></linearGradient>
    <linearGradient id="hx-soil" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1E1A16"/><stop offset="1" stop-color="#110F0C"/></linearGradient>
    <linearGradient id="hx-window" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFD977" stop-opacity=".34"/><stop offset="1" stop-color="#FFB24A" stop-opacity=".12"/></linearGradient>
    <radialGradient id="sx-light"><stop offset="0" stop-color="#FFE7A0" stop-opacity=".5"/><stop offset="1" stop-color="#FFE7A0" stop-opacity="0"/></radialGradient>
    <pattern id="hx-grain" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="4" cy="6" r="1.6" fill="#fff" fill-opacity=".05"/><circle cx="17" cy="19" r="1.1" fill="#fff" fill-opacity=".04"/></pattern>
  </defs>

  <!-- night sky, ground and soil -->
  <rect width="{W}" height="{GROUND}" fill="url(#hx-sky)"/>
  <g class="hx-stars" fill="#DCE6FF" fill-opacity=".5">{stars}</g>
  <rect y="{GROUND}" width="{W}" height="{H - GROUND}" fill="url(#hx-soil)"/>
  <rect y="{GROUND}" width="{W}" height="{H - GROUND}" fill="url(#hx-grain)"/>
  <path class="hx-grass" d="M0 {GROUND} H{W}"/>

  <!-- the utility pole -->
  <path class="sx-pole" d="M1840 {GROUND} V380 M1804 404 H1876"/>
  <rect class="sx-can" x="1852" y="430" width="30" height="44" rx="6"/>

  <!-- the house -->
  <path class="hx-roof" d="M330 300 L960 90 L1590 300 Z"/>
  <rect class="hx-room" x="380" y="300" width="600" height="260"/>
  <rect class="hx-room" x="980" y="300" width="560" height="260"/>
  <rect class="hx-room" x="380" y="560" width="630" height="260"/>
  <rect class="hx-room" x="1010" y="560" width="530" height="260"/>
  <rect class="hx-base" x="380" y="{GROUND}" width="1160" height="210"/>
  <path class="hx-wall" d="M330 300 H1590 M380 300 V1030 H1540 V300 M380 560 H1540 M380 {GROUND} H1540 M980 300 V560 M1010 560 V{GROUND}"/>
  <path class="hx-floor" d="M380 560 H1540 M380 {GROUND} H1540"/>

  <!-- windows: lit from inside -->
  <g class="hx-windows sx-windows">
    <rect x="1300" y="380" width="130" height="96" rx="4"/><rect x="1205" y="595" width="96" height="92" rx="4"/>
    <rect x="610" y="610" width="120" height="96" rx="4"/><rect x="760" y="380" width="120" height="96" rx="4"/>
  </g>
  <path class="hx-detail" d="M1365 380 V476 M1300 428 H1430 M1253 595 V687 M1205 641 H1301 M670 610 V706 M610 658 H730 M820 380 V476 M760 428 H880"/>

  <!-- upstairs: two bedrooms -->
  <rect class="hx-fixture" x="440" y="505" width="230" height="55" rx="8"/>
  <rect class="hx-fixture" x="430" y="462" width="16" height="98" rx="4"/>
  <rect class="hx-fixture" x="1150" y="505" width="250" height="55" rx="8"/>
  <rect class="hx-fixture" x="1140" y="462" width="16" height="98" rx="4"/>

  <!-- downstairs: living room and kitchen -->
  <path class="hx-fixture" d="M450 820 V770 Q450 752 468 752 H618 Q636 752 636 770 V820 Z"/>
  <path class="hx-detail" d="M470 752 V730 Q470 714 486 714 H600 Q616 714 616 730 V752"/>
  <rect class="hx-fixture" x="1060" y="790" width="400" height="30" rx="4"/>
  <rect class="hx-fixture" x="1060" y="600" width="120" height="70" rx="4"/>
  <rect class="hx-fixture" x="1340" y="600" width="120" height="70" rx="4"/>
  <g class="sx-tv"><rect x="760" y="640" width="150" height="86" rx="6"/></g>

  <!-- the wiring -->
  <g class="hx-pipes">
    {wires(CIRCUITS, "circuit")}
    {wires(SERVICE, "service")}
  </g>

  <!-- the lights, the smoke alarms, the outlets -->
  {lamp(700, 326, 0)}{lamp(560, 572, 1)}
  <g class="sx-alarm"><circle cx="1200" cy="304" r="14"/><circle class="sx-led" cx="1206" cy="300" r="3"/></g>
  <g class="sx-alarm"><circle cx="860" cy="304" r="14"/><circle class="sx-led" cx="866" cy="300" r="3"/></g>
  <g class="sx-outlet"><rect x="1306" y="760" width="28" height="36" rx="4"/><path d="M1314 772 V778 M1326 772 V778 M1320 786 V788"/><circle class="sx-led sx-gfci" cx="1320" cy="766" r="2.5"/></g>
  <g class="sx-outlet"><rect x="886" y="778" width="28" height="36" rx="4"/><path d="M894 790 V796 M906 790 V796 M900 804 V806"/></g>

  <!-- basement: the panel -->
  <rect class="sx-panel" x="1440" y="862" width="82" height="112" rx="6"/>
  <g class="sx-breakers">{breakers}</g>

  <!-- outside: the weatherhead, the meter -->
  <path class="sx-mast" d="M1578 446 Q1578 434 1590 434"/>
  <rect class="sx-meter-box" x="1554" y="672" width="48" height="52" rx="8"/>
  <circle class="sx-meter-glass" cx="1578" cy="698" r="16"/>
  <g transform="translate(1578 698)"><rect class="sx-meter-disc" x="-11" y="-2" width="22" height="4" rx="2"/></g>

  <!-- outside, left: the EV charger, the car charging -->
  <rect class="sx-charger" x="334" y="712" width="28" height="44" rx="6"/>
  <circle class="sx-led sx-ev-led" cx="348" cy="724" r="4"/>
  <path class="sx-cable" d="M348 756 Q352 800 300 790 Q270 784 262 770"/>
  <path class="sx-car" d="M70 808 V778 Q72 762 92 758 L132 726 Q142 718 160 718 H236 Q252 718 262 728 L292 758 Q312 762 314 778 V808 Z"/>
  <circle class="sx-wheel" cx="118" cy="810" r="20"/><circle class="sx-wheel" cx="268" cy="810" r="20"/>
  <path class="hx-detail" d="M140 730 L120 758 H186 V730 Z M200 730 V758 H270 L248 730 Z"/>
  <rect class="sx-batt" x="160" y="772" width="52" height="20" rx="4"/>
  <rect class="sx-batt-fill" x="164" y="776" width="44" height="12" rx="2"/>
</svg>
"""


if __name__ == "__main__":
    out = Path(__file__).with_name("house-check.svg")
    out.write_text(house(), encoding="utf-8")
    print(out, {k: (round(x * 100 / W, 2), round(y * 100 / H, 2)) for k, (x, y) in SPOTS.items()})
