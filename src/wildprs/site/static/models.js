(() => {
  'use strict';
  const FLYERS = new Set(['bird', 'bat']);
  const FAST = 2.6;
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const num = (v, fallback) => (v === undefined || v === '' ? fallback : +v);

  const flies = taxon => FLYERS.has(Zoo.species[taxon].kind);
  function shadow(taxon, col, drop = 0.18) {
    const b = Zoo.bounds(taxon);
    return { col, y: flies(taxon) ? b.lo[1] - drop * b.r : undefined };
  }
  const VIEW = { ground: [-0.42, 0.26], air: [-0.5, 0.42], airLane: [-0.9, 0.25] };
  function options(canvas) {
    const d = canvas.dataset;
    const [yaw, pitch] = VIEW[!flies(d.taxon) ? 'ground' : 'lane' in d ? 'airLane' : 'air'];
    return {
      yaw: num(d.yaw, yaw), pitch: num(d.pitch, pitch), pad: num(d.pad, 0.9), phase: num(d.phase, 0),
      drag: 'drag' in d,
      shadow: d.shadow === 'none' ? null : shadow(d.taxon, d.shadow || 'rgba(46,10,0,.3)'),
    };
  }
  window.Models = { flies, shadow, options };

  function mount(canvas) {
    return Zoo.species[canvas.dataset.taxon] ? Zoo.mount(canvas, canvas.dataset.taxon, options(canvas)) : null;
  }

  function rush(link, m) {
    let target = 1, raf = 0;
    const step = () => {
      const s = m.state.speed + (target - m.state.speed) * 0.14;
      m.set({ speed: Math.abs(target - s) < 0.02 ? target : s });
      raf = m.state.speed === target ? 0 : requestAnimationFrame(step);
    };
    const go = on => { target = on ? FAST : 1; if (!raf) raf = requestAnimationFrame(step); };
    link.addEventListener('pointerenter', () => go(true));
    link.addEventListener('pointerleave', () => go(link.matches(':focus-visible')));
    link.addEventListener('focus', () => go(true));
    link.addEventListener('blur', () => go(link.matches(':hover')));
  }

  function turnWithKeys(canvas, m) {
    canvas.addEventListener('keydown', e => {
      const d = { ArrowLeft: [-0.15, 0], ArrowRight: [0.15, 0], ArrowUp: [0, 0.1], ArrowDown: [0, -0.1] }[e.key];
      if (!d) return;
      e.preventDefault();
      m.set({ yaw: m.state.yaw + d[0], pitch: Math.min(1.45, Math.max(-0.3, m.state.pitch + d[1])) });
    });
  }

  for (const canvas of document.querySelectorAll('canvas[data-taxon]')) {
    const m = mount(canvas);
    if (!m) continue;
    const link = canvas.closest('a');
    if (link && !REDUCED) rush(link, m);
    if ('drag' in canvas.dataset) turnWithKeys(canvas, m);
  }
  const settle = n => requestAnimationFrame(() => (n ? settle(n - 1) : (document.documentElement.dataset.models = 'ready')));
  settle(2);
})();
