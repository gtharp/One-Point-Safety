/* =========================================================
   Flying Wedge animation -- a simple X's-and-O's diagram of
   football's most notorious formation, reconstructed from
   historical accounts (see data/history.json id
   "1892-flying-wedge-invented" for sourcing and caveats).

   Built as inline SVG + the Web Animations API rather than a
   literal GIF: smaller, crisp at any size, theme-aware (light/
   dark via CSS variables), and trivially replayable -- no extra
   file to host, consistent with the site's no-build-step,
   vanilla-JS approach.
   ========================================================= */

(function () {
  const DUR = 3400; // ms per play
  const EASE = "cubic-bezier(.3,.05,.2,1)";

  // Defensive line: 11 players along the line of scrimmage.
  // Indices 4,5,6 (the aim point) get knocked back on impact.
  const DEFENSE = [60, 128, 196, 264, 332, 400, 468, 536, 604, 672, 740].map((x, i) => {
    const hit = i >= 4 && i <= 6;
    const dx = hit ? (i - 5) * 25 : 0;
    const dy = hit ? -35 - (i === 5 ? 10 : 0) : 0;
    return { x, y: 190, dx, dy, hit };
  });

  // Offensive wings: 5 players each side, starting split wide and
  // back, converging into a V with its point toward the defense.
  const LEFT_WING = [
    { sx: 230, sy: 320, ex: 386, ey: 180 },
    { sx: 200, sy: 340, ex: 360, ey: 205 },
    { sx: 170, sy: 360, ex: 334, ey: 230 },
    { sx: 140, sy: 380, ex: 308, ey: 255 },
    { sx: 110, sy: 400, ex: 282, ey: 280 },
  ];
  const RIGHT_WING = LEFT_WING.map(p => ({ sx: 800 - p.sx, sy: p.sy, ex: 800 - p.ex, ey: p.ey }));
  // Ball carrier: tucked behind the point of the wedge, shielded,
  // then bursts through the gap the wedge opens.
  const CARRIER = { sx: 400, sy: 400, mx: 400, my: 240, ex: 400, ey: 160 };

  const svgNS = "http://www.w3.org/2000/svg";
  function el(tag, attrs) {
    const node = document.createElementNS(svgNS, tag);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  function build() {
    const defenseG = document.getElementById("wedgeDefense");
    const offenseG = document.getElementById("wedgeOffense");
    if (!defenseG || !offenseG) return null;

    // Each defender is an outer <g> that sets the base position via the
    // SVG `transform` attribute, wrapping an inner, attribute-transform-free
    // <g> that's the actual Web Animations API target. Animating the CSS
    // `transform` property directly on an element that also carries a
    // `transform` *attribute* makes the CSS value replace the attribute
    // entirely (per the SVG2/CSS Transforms spec) rather than compose with
    // it -- without this split, animated defenders snap to the SVG origin
    // at the animation's first frame instead of staying at their base spot.
    const defenseEls = DEFENSE.map(d => {
      const outer = el("g", { transform: `translate(${d.x},${d.y})` });
      const inner = el("g", { class: "wedge-x" });
      inner.appendChild(el("line", { x1: -8, y1: -8, x2: 8, y2: 8 }));
      inner.appendChild(el("line", { x1: -8, y1: 8, x2: 8, y2: -8 }));
      outer.appendChild(inner);
      defenseG.appendChild(outer);
      return { node: inner, data: d };
    });

    const wingEls = [...LEFT_WING, ...RIGHT_WING].map(p => {
      const c = el("circle", { class: "wedge-o", cx: p.sx, cy: p.sy, r: 10 });
      offenseG.appendChild(c);
      return { node: c, data: p };
    });

    const carrierNode = el("circle", { class: "wedge-ball", cx: CARRIER.sx, cy: CARRIER.sy, r: 11 });
    offenseG.appendChild(carrierNode);

    return { defenseEls, wingEls, carrierNode };
  }

  function play(scene) {
    if (!scene) return;
    const { defenseEls, wingEls, carrierNode } = scene;

    wingEls.forEach(({ node, data }) => {
      node.animate(
        [
          { transform: "translate(0px,0px)", offset: 0 },
          { transform: `translate(${data.ex - data.sx}px,${data.ey - data.sy}px)`, offset: 0.55 },
          { transform: `translate(${data.ex - data.sx}px,${data.ey - data.sy}px)`, offset: 1 },
        ],
        { duration: DUR, easing: EASE, fill: "forwards" }
      );
    });

    defenseEls.forEach(({ node, data }) => {
      if (!data.hit) return;
      node.animate(
        [
          { transform: "translate(0px,0px)", offset: 0 },
          { transform: "translate(0px,0px)", offset: 0.55 },
          { transform: `translate(${data.dx}px,${data.dy}px)`, offset: 0.85 },
          { transform: `translate(${data.dx}px,${data.dy}px)`, offset: 1 },
        ],
        { duration: DUR, easing: EASE, fill: "forwards" }
      );
    });

    carrierNode.animate(
      [
        { transform: "translate(0px,0px)", offset: 0 },
        { transform: `translate(0px,${CARRIER.my - CARRIER.sy}px)`, offset: 0.55 },
        { transform: `translate(0px,${CARRIER.ey - CARRIER.sy}px)`, offset: 0.9 },
        { transform: `translate(0px,${CARRIER.ey - CARRIER.sy}px)`, offset: 1 },
      ],
      { duration: DUR, easing: EASE, fill: "forwards" }
    );
  }

  document.addEventListener("DOMContentLoaded", () => {
    const scene = build();
    if (!scene) return;
    play(scene);

    const replay = document.getElementById("wedgeReplay");
    if (replay) replay.addEventListener("click", () => play(scene));

    const readMore = document.getElementById("wedgeReadMore");
    if (readMore) {
      readMore.addEventListener("click", () => {
        if (typeof window.openHistoryEntry === "function") {
          window.openHistoryEntry("1892-flying-wedge-invented");
        }
      });
    }
  });
})();
