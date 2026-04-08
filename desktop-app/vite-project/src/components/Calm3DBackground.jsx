import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';

const Calm3DBackground = ({ width = "100%", height = "100%" }) => {
  const mountRef = useRef(null);

  useEffect(() => {
    let animationFrameId;
    const PARTICLE_COUNT = 20000;

    const renderer = new THREE.WebGLRenderer({ 
      antialias: true, 
      alpha: false,
      preserveDrawingBuffer: false
    });
    renderer.setClearColor(0x0f0c0a, 1.0);
    renderer.outputColorSpace = THREE.SRGBColorSpace; 
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    // Lowered exposure to reduce brightness
    renderer.toneMappingExposure = 1.0; 

    const updateSize = () => {
      if (!mountRef.current) return;
      const w = mountRef.current.clientWidth || window.innerWidth;
      const h = mountRef.current.clientHeight || window.innerHeight;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };

    if (mountRef.current) {
      mountRef.current.appendChild(renderer.domElement);
    }

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x0f0c0a, 0.04);

    const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.01, 200);
    camera.position.set(0, 0, 6.2);
    
    updateSize();
    window.addEventListener('resize', updateSize);

    // ========== SHAPE GENERATORS ==========
    function hash(n) { return ((Math.sin(n) * 43758.5453) % 1 + 1) % 1; }

    function chaosField(count) {
      const pts = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const r = Math.pow(Math.random(), 0.5) * 3.4;
        const a = Math.random() * Math.PI * 2;
        pts[i*3] = Math.cos(a) * r;
        pts[i*3+1] = Math.sin(a) * r;
        pts[i*3+2] = (Math.random()-0.5) * 1.2;
      }
      return pts;
    }

    function neuralNet(count, scale = 2.4) {
      const pts = new Float32Array(count * 3);
      const hubs = 12;
      const hx = [], hy = [];
      for (let h = 0; h < hubs; h++) {
        const a = (h / hubs) * Math.PI * 2 + (hash(h*7.3)-0.5)*0.15;
        const r = scale * (0.7 + hash(h*13.7)*0.2);
        hx.push(Math.cos(a) * r);
        hy.push(Math.sin(a) * r);
      }
      let filled = 0;
      const perHub = Math.floor(count * 0.22 / hubs);
      for (let h = 0; h < hubs && filled < count; h++) {
        for (let p = 0; p < perHub && filled < count; p++) {
          pts[filled*3] = hx[h] + (Math.random()-0.5)*0.09;
          pts[filled*3+1] = hy[h] + (Math.random()-0.5)*0.09;
          pts[filled*3+2] = (Math.random()-0.5)*0.1;
          filled++;
        }
      }
      const edges = [];
      for (let a = 0; a < hubs; a++) {
        for (let b = a+1; b < hubs; b++) {
          const dx = hx[a] - hx[b];
          const dy = hy[a] - hy[b];
          const dist = Math.hypot(dx, dy);
          if (dist < scale * 1.2) edges.push([a,b]);
        }
      }
      const perEdge = Math.floor((count * 0.7) / Math.max(edges.length, 1));
      for (const [a,b] of edges) {
        for (let p = 0; p < perEdge && filled < count; p++) {
          const t = Math.random();
          const noise = (Math.random()-0.5)*0.05;
          pts[filled*3] = hx[a] + (hx[b]-hx[a])*t + noise;
          pts[filled*3+1] = hy[a] + (hy[b]-hy[a])*t + noise;
          pts[filled*3+2] = (Math.random()-0.5)*0.07;
          filled++;
        }
      }
      while (filled < count) {
        const r = scale * (1 + Math.random()*0.6);
        const a = Math.random() * Math.PI * 2;
        pts[filled*3] = Math.cos(a) * r;
        pts[filled*3+1] = Math.sin(a) * r;
        pts[filled*3+2] = (Math.random()-0.5)*0.35;
        filled++;
      }
      return pts;
    }

    function waveform(count, scale = 2.3) {
      const pts = new Float32Array(count * 3);
      const rings = [{r:0.4, w:0.08, weight:0.5}, {r:0.75, w:0.07, weight:0.3}, {r:1.05, w:0.06, weight:0.2}];
      let filled = 0;
      for (let i = 0; i < count; i++) {
        let rnd = Math.random();
        let ring = rings[0];
        let acc = 0;
        for (const rg of rings) { acc += rg.weight; if (rnd < acc) { ring = rg; break; } }
        const angle = Math.random() * Math.PI * 2;
        const r = (ring.r + (Math.random()-0.5)*ring.w*2) * scale;
        const mod = 1 + 0.05 * Math.sin(6*angle) + 0.02 * Math.cos(11*angle);
        pts[filled*3] = Math.cos(angle) * r * mod;
        pts[filled*3+1] = Math.sin(angle) * r * mod;
        pts[filled*3+2] = (Math.random()-0.5)*0.08;
        filled++;
      }
      return pts;
    }

    function hexGrid(count, scale = 2.2) {
      const pts = new Float32Array(count * 3);
      const hexSize = 0.28;
      const cols = 12, rows = 12;
      let filled = 0;
      for (let i = 0; i < rows; i++) {
        for (let j = 0; j < cols; j++) {
          const x = (j - cols/2) * hexSize * 1.5;
          const y = (i - rows/2) * hexSize * Math.sqrt(3) + (j%2)*hexSize*Math.sqrt(3)/2;
          const dist = Math.hypot(x, y);
          if (dist < scale) {
            for (let p = 0; p < 3 && filled < count; p++) {
              pts[filled*3] = x + (Math.random()-0.5)*0.06;
              pts[filled*3+1] = y + (Math.random()-0.5)*0.06;
              pts[filled*3+2] = (Math.random()-0.5)*0.1;
              filled++;
            }
          }
        }
      }
      while (filled < count) {
        const r = Math.random() * scale;
        const a = Math.random() * Math.PI * 2;
        pts[filled*3] = Math.cos(a) * r;
        pts[filled*3+1] = Math.sin(a) * r;
        pts[filled*3+2] = (Math.random()-0.5)*0.2;
        filled++;
      }
      return pts;
    }

    function dataFlow(count, scale = 2.3) {
      const pts = new Float32Array(count * 3);
      const streams = 12;
      for (let i = 0; i < count; i++) {
        const s = Math.floor(Math.random() * streams);
        const baseA = (s / streams) * Math.PI * 2;
        const t = Math.pow(Math.random(), 0.6);
        const r = scale * (1.0 - t * 0.85);
        const curl = 0.25 * t;
        const angle = baseA + curl + (Math.random()-0.5)*0.1;
        pts[i*3] = Math.cos(angle) * r;
        pts[i*3+1] = Math.sin(angle) * r;
        pts[i*3+2] = (Math.random()-0.5)*0.1 * (1-t);
      }
      return pts;
    }

    function auraFinal(count, scale = 2.2) {
      const pts = new Float32Array(count * 3);
      let filled = 0;
      const inner = Math.floor(count * 0.95);
      const attempts = inner * 40;
      for (let a = 0; a < attempts && filled < inner; a++) {
        const x = (Math.random()-0.5)*2;
        const y = (Math.random()-0.5)*2;
        const r = Math.hypot(x, y);
        const v = Math.sin(4 * Math.atan2(y, x)) * 0.6;
        const thr = 0.15 + Math.random() * 0.1;
        const env = Math.exp(-r*r*1.2) * (0.4 + 0.6*Math.abs(Math.sin(r*5)));
        if (Math.abs(v) < thr && Math.random() < env+0.2) {
          pts[filled*3] = x * scale;
          pts[filled*3+1] = y * scale;
          pts[filled*3+2] = (Math.random()-0.5)*0.1;
          filled++;
        }
      }
      while (filled < count) {
        const r = Math.random() * scale;
        const a = Math.random() * Math.PI * 2;
        pts[filled*3] = Math.cos(a) * r;
        pts[filled*3+1] = Math.sin(a) * r;
        pts[filled*3+2] = (Math.random()-0.5)*0.3;
        filled++;
      }
      return pts;
    }

    const shapes = {
      chaos: chaosField(PARTICLE_COUNT),
      neural: neuralNet(PARTICLE_COUNT),
      waveform: waveform(PARTICLE_COUNT),
      hexgrid: hexGrid(PARTICLE_COUNT),
      flow: dataFlow(PARTICLE_COUNT),
      aura: auraFinal(PARTICLE_COUNT)
    };

    // ========== GEOMETRY WITH MORPH TARGETS ==========
    const geo = new THREE.BufferGeometry();
    const initialPos = shapes.chaos.slice();
    geo.setAttribute('position', new THREE.BufferAttribute(initialPos, 3));
    geo.setAttribute('aTargetA', new THREE.BufferAttribute(new Float32Array(PARTICLE_COUNT*3), 3));
    geo.setAttribute('aTargetB', new THREE.BufferAttribute(new Float32Array(PARTICLE_COUNT*3), 3));
    const seeds = new Float32Array(PARTICLE_COUNT);
    for (let i = 0; i < PARTICLE_COUNT; i++) seeds[i] = Math.random();
    geo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));

    // ========== SHADER ==========
    const mat = new THREE.ShaderMaterial({
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      uniforms: {
        uTime: { value: 0 },
        uProgress: { value: 0 },
        // Lowered baseline brightness uniform to not be blinding
        uBrightness: { value: 0.5 } 
      },
      vertexShader: `
        uniform float uTime;
        uniform float uProgress;
        attribute float aSeed;
        attribute vec3 aTargetA;
        attribute vec3 aTargetB;
        varying float vAlpha;
        varying float vSeed;
        
        float hash(float n){ return fract(sin(n)*43758.5453); }
        
        void main(){
          vec3 pos = mix(aTargetA, aTargetB, uProgress);
          float wave = sin(pos.x*1.2 + uTime)*cos(pos.y*1.1 - uTime)*0.02;
          pos.z += wave;
          pos.x += sin(uTime*0.9 + aSeed*50.0)*0.008;
          pos.y += cos(uTime*0.7 + aSeed*40.0)*0.008;
          pos.z += sin(uTime*0.5 + aSeed*30.0)*0.008;
          
          vec4 mv = modelViewMatrix * vec4(pos, 1.0);
          float depth = 1.0 / (-mv.z + 0.001);
          float size = 14.0 * depth * (0.8 + aSeed * 1.2);
          gl_PointSize = max(2.5, size);
          gl_Position = projectionMatrix * mv;
          
          vAlpha = 0.6 + aSeed * 0.4;
          vSeed = aSeed;
        }
      `,
      fragmentShader: `
        uniform float uTime;
        uniform float uBrightness;
        varying float vAlpha;
        varying float vSeed;
        
        float hash(float n){ return fract(sin(n)*43758.5453); }
        
        void main(){
          vec2 uv = gl_PointCoord - 0.5;
          float d = length(uv);
          if(d > 0.5) discard;
          float core = exp(-d*d*10.0);
          vec3 pink1 = vec3(1.0, 0.7, 0.75);
          vec3 pink2 = vec3(1.0, 0.8, 0.85);
          float mixF = 0.3 + 0.7 * hash(vSeed*17.3);
          vec3 base = mix(pink1, pink2, mixF);
          base += vec3(0.05, 0.03, 0.06) * sin(vSeed*35.0 + uTime*1.2)*0.12;
          // Clamp changed slightly to be less intensely white
          base = clamp(base, 0.4, 0.9);
          float alpha = vAlpha * core * (0.5 + uBrightness * 0.5);
          alpha = min(1.0, alpha * 1.2);
          gl_FragColor = vec4(base, alpha);
        }
      `
    });

    const pointsMesh = new THREE.Points(geo, mat);
    scene.add(pointsMesh);

    // Background depth layer
    const bgCount = 12000;
    const bgGeo = new THREE.BufferGeometry();
    const bgPos = new Float32Array(bgCount*3);
    for (let i=0; i<bgCount; i++) {
      bgPos[i*3] = (Math.random()-0.5)*14;
      bgPos[i*3+1] = (Math.random()-0.5)*14;
      bgPos[i*3+2] = -Math.random()*10 - 2;
    }
    bgGeo.setAttribute('position', new THREE.BufferAttribute(bgPos, 3));
    // Reduced bg layer opacity
    const bgMat = new THREE.PointsMaterial({ color: 0xffa5b3, size: 0.03, transparent: true, opacity: 0.15, blending: THREE.AdditiveBlending });
    const bgPoints = new THREE.Points(bgGeo, bgMat);
    scene.add(bgPoints);

    // ========== TIMELINE ==========
    const timeline = [
      { from: 'chaos',    to: 'neural',   dur: 3.0, hold: 1.0 },
      { from: 'neural',   to: 'waveform', dur: 3.0, hold: 1.0 },
      { from: 'waveform', to: 'hexgrid',  dur: 3.0, hold: 1.0 },
      { from: 'hexgrid',  to: 'flow',     dur: 3.0, hold: 1.0 },
      { from: 'flow',     to: 'aura',     dur: 3.5, hold: 1.5 },
      { from: 'aura',     to: 'chaos',    dur: 2.5, hold: 0.5 }
    ];

    let stage = 0, phase = 'warmup', phaseT = 0;
    function loadStage(idx) {
      const s = timeline[idx];
      geo.attributes.aTargetA.array.set(shapes[s.from]);
      geo.attributes.aTargetB.array.set(shapes[s.to]);
      geo.attributes.aTargetA.needsUpdate = true;
      geo.attributes.aTargetB.needsUpdate = true;
    }
    function easeApple(t){ return t<0.5?8*t*t*t*t:1-8*Math.pow(t-1,4); }

    loadStage(0);
    mat.uniforms.uProgress.value = 0;
    mat.uniforms.uBrightness.value = 0.5;

    // ========== ANIMATION LOOP ==========
    // Use manual rigid time increment to absolutely guarantee movement
    let previewTime = 0; 
    
    const animatePreview = () => {
      animationFrameId = requestAnimationFrame(animatePreview);
      
      // Forces linear progression every frame to avoid clock pausing issues
      previewTime += 1/60; 
      const t = previewTime;

      if (phase === 'warmup') {
        if (t > 1.2) { phase = 'morph'; phaseT = t; }
      } else if (phase === 'morph') {
        const seg = timeline[stage];
        const elapsed = t - phaseT;
        let raw = Math.min(elapsed / seg.dur, 1.0);
        const progress = easeApple(raw);
        mat.uniforms.uProgress.value = progress;
        mat.uniforms.uBrightness.value = 0.4 + 0.2 * Math.sin(progress * Math.PI);
        if (raw >= 1.0) { phase = 'hold'; phaseT = t; }
      } else if (phase === 'hold') {
        const seg = timeline[stage];
        const elapsed = t - phaseT;
        mat.uniforms.uProgress.value = 1.0;
        mat.uniforms.uBrightness.value = 0.5 + 0.1 * Math.sin(t * 1.2);
        if (elapsed >= seg.hold) {
          stage = (stage + 1) % timeline.length;
          loadStage(stage);
          mat.uniforms.uProgress.value = 0;
          phase = 'morph';
          phaseT = t;
        }
      }

      camera.position.x = Math.sin(t * 0.02) * 0.05;
      camera.position.y = Math.cos(t * 0.015) * 0.04;
      camera.position.z = 6.2 + Math.sin(t * 0.03) * 0.1;
      camera.lookAt(0, 0, 0);
      mat.uniforms.uTime.value = t;

      renderer.render(scene, camera);
    };

    animatePreview();

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener('resize', updateSize);
      if (mountRef.current && mountRef.current.contains(renderer.domElement)) {
        mountRef.current.removeChild(renderer.domElement);
      }
      geo.dispose();
      mat.dispose();
      bgGeo.dispose();
      bgMat.dispose();
      renderer.dispose();
    };
  }, []);

  return (
    <div style={{ width, height, position: 'absolute', top: 0, left: 0, zIndex: 0, overflow: 'hidden' }}>
      {/* 3D Canvas Base */}
      <div 
        ref={mountRef} 
        style={{ 
          width: '100%', 
          height: '100%', 
          position: 'absolute', 
          top: 0, 
          left: 0, 
          zIndex: 0,
          background: '#0f0c0a'
        }} 
        aria-hidden="true" 
      />
      {/* Glassmorphism Blur Overlay */}
      <div style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        zIndex: 1,
        pointerEvents: 'none',
        background: 'rgba(15, 12, 10, 0.45)', // Slight dark tint over elements
        backdropFilter: 'blur(10px)',         // Beautiful glass blur
        WebkitBackdropFilter: 'blur(10px)'
      }} />
    </div>
  );
};

export default Calm3DBackground;
