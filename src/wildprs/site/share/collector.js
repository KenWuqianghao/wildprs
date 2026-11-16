(() => {
  'use strict';
  const Z = window.Zoo;
  const ngon = (cx, cy, r, n, rot = 0.2) => Array.from({ length: n }, (_, i) => { const a = rot + (i / n) * Math.PI * 2; return (cx + r * Math.cos(a)).toFixed(1) + ',' + (cy + r * Math.sin(a)).toFixed(1); }).join(' ');
  const SCENE = {
    sky: `<polygon class="s-l" points="${ngon(68, 23, 17, 9)}"/><polygon class="s-d" points="0,64 0,53 10,46 20,51 33,42 45,50 57,45 70,52 84,43 100,51 100,64"/><polygon class="s-dd" points="0,64 0,59 16,54 30,58 48,53 66,59 82,55 100,60 100,64"/>`,
    night: `<polygon class="s-l" points="${ngon(71, 20, 13, 9)}"/><polygon class="s-d" points="0,64 0,50 6,43 12,50 18,41 25,50 32,45 38,52 46,43 54,51 62,46 70,52 78,44 86,51 93,46 100,51 100,64"/><polygon class="s-dd" points="0,64 0,59 20,56 44,60 70,56 100,60 100,64"/>`,
    land: `<polygon class="s-l" points="${ngon(73, 20, 14, 9)}"/><polygon class="s-d" points="0,64 0,43 14,35 30,42 48,32 66,41 84,34 100,40 100,64"/><polygon class="s-dd" points="0,64 0,52 40,49.5 100,51.5 100,64"/>`,
    sea: `<polygon class="s-ray" points="16,0 36,0 20,64 0,64"/><polygon class="s-ray" points="58,0 70,0 62,64 46,64"/><polygon class="s-d" points="0,64 0,56 14,52 30,58 50,53 68,59 86,54 100,58 100,64"/>`,
  };
  const VIEW = {
    bird: { scene: 'sky', yaw: -0.9, pitch: 0.6 }, bat: { scene: 'night', yaw: -0.9, pitch: 0.6 },
    quad: { scene: 'land', yaw: -0.42, pitch: 0.12, ground: true }, tortoise: { scene: 'land', yaw: -0.5, pitch: 0.2, ground: true },
    swim: { scene: 'sea', yaw: -0.5, pitch: 0.26 }, turtle: { scene: 'sea', yaw: -0.55, pitch: 0.5 },
    penguin: { scene: 'sea', yaw: -0.5, pitch: 0.3 }, ray: { scene: 'sea', yaw: -0.5, pitch: 0.5 },
  };
  const PHASE = 0.03;
  const REACH = { fx: 1.34, ox: 0.14, tall: 1.3, top: 0.03, topGround: 0.06, bottom: 0.14 };

  function drawModel(cv) {
    const taxon = cv.dataset.taxon, v = VIEW[Z.species[taxon].kind], art = cv.parentNode.querySelector('.art');
    art.querySelector('svg').innerHTML = SCENE[v.scene];
    const w = cv.offsetWidth, h = cv.offsetHeight, g = cv.getContext('2d');
    cv.width = w * 2; cv.height = h * 2; g.scale(2, 2);
    const bx = -cv.offsetLeft, by = -cv.offsetTop, bw = art.offsetWidth, bh = art.offsetHeight;
    const pb = Z.view(taxon, v.yaw, v.pitch), fit = Z.bounds(taxon);
    const top = by + bh * (v.ground ? REACH.topGround : REACH.top), bot = by + bh * (1 - REACH.bottom);
    const s = Math.min((bw * REACH.fx) / (pb.x1 - pb.x0), ((bot - top) * (v.ground ? 1 : REACH.tall)) / (pb.y1 - pb.y0));
    const x = bx + bw * (0.5 + REACH.ox) - (s * (pb.x0 + pb.x1)) / 2;
    const y = v.ground ? bot + s * pb.y0 : (top + bot) / 2 + (s * (pb.y0 + pb.y1)) / 2;
    Z.draw(g, Z.build(taxon, PHASE), { x, y, fit, size: s * fit.r, yaw: v.yaw, pitch: v.pitch, outline: { w: bw * 0.014, col: '#fffdf6' } });
  }

  function fit(el) {
    const lines = +el.dataset.fit;
    for (let s = 1; s > 0.4; s -= 0.04) {
      el.style.setProperty('--s', s.toFixed(2));
      if (el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= parseFloat(getComputedStyle(el).lineHeight) * (lines + 0.35)) return;
    }
  }
  function fitColumn(col) {
    const hook = col.querySelector('.hook');
    for (let s = 1; s > 0.6 && col.scrollHeight > col.clientHeight; s -= 0.04) hook.style.setProperty('--s', s.toFixed(2));
  }

  Promise.all(['400 40px "Bagel Fat One"', '600 20px Gabarito'].map(f => document.fonts.load(f))).then(() => {
    document.querySelectorAll('[data-fit]').forEach(fit);
    document.querySelectorAll('.txt').forEach(fitColumn);
    document.querySelectorAll('canvas[data-taxon]').forEach(drawModel);
    requestAnimationFrame(() => (document.documentElement.dataset.models = 'ready'));
  });
})();
