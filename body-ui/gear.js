// Draws the gear used for the settings icon. Every <svg data-gear> on the page gets its shape from here.
// Change TEETH, OUTER or ROOT to make the teeth more or fewer, longer or shorter.
(function () {
  const TEETH = 8;
  const OUTER = 10.2; // how far the teeth reach from the center (the icon box is 24 wide)
  const ROOT = 7.9; // the body of the gear, between the teeth

  function gearPath(cx, cy) {
    const step = (Math.PI * 2) / TEETH;
    const points = [];
    for (let i = 0; i < TEETH; i++) {
      const center = i * step - Math.PI / 2; // the first tooth points straight up
      const at = (offset, radius) => [
        cx + radius * Math.cos(center + step * offset),
        cy + radius * Math.sin(center + step * offset),
      ];
      points.push(
        at(-0.31, ROOT),
        at(-0.17, OUTER),
        at(0.17, OUTER),
        at(0.31, ROOT),
        at(0.5, ROOT),
      );
    }
    return (
      "M" +
      points.map((p) => p[0].toFixed(2) + " " + p[1].toFixed(2)).join("L") +
      "Z"
    );
  }

  function paint() {
    document.querySelectorAll("svg[data-gear]").forEach((svg) => {
      svg.innerHTML =
        '<path d="' + gearPath(12, 12) + '"/><circle cx="12" cy="12" r="3.1"/>';
    });
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", paint);
  else paint();
})();
