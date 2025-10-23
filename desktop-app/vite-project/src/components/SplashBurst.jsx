import React, { useEffect, useRef } from "react";
import * as THREE from "three";

const SplashBurst = ({ onComplete }) => {
  const mountRef = useRef(null);

  useEffect(() => {
    if (!mountRef.current) return;

    const width = window.innerWidth;
    const height = window.innerHeight;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(70, width / height, 0.1, 2000);
    camera.position.z = 400;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setClearColor(0x000000, 1);
    mountRef.current.appendChild(renderer.domElement);

    // Create a radial burst of lines
    const particleCount = 1400; // dense for a vivid burst
    const positions = new Float32Array(particleCount * 6); // line segments (start/end)
    const colors = new Float32Array(particleCount * 6);
    const velocities = new Float32Array(particleCount); // speed magnitude
    const decay = new Float32Array(particleCount); // fade timings

    // Color palette (purple, pink, red, white) resembling the image
    const palette = [
      new THREE.Color("#c64bf8"),
      new THREE.Color("#ff66c4"),
      new THREE.Color("#ff3b3b"),
      new THREE.Color("#ffffff"),
      new THREE.Color("#b23be0"),
      new THREE.Color("#ff8ad6"),
    ];

    // Initialize rays from center
    const origin = new THREE.Vector3(0, 0, 0);
    for (let i = 0; i < particleCount; i++) {
      // Random direction on sphere
      const theta = Math.acos(2 * Math.random() - 1);
      const phi = 2 * Math.PI * Math.random();
      const dir = new THREE.Vector3(
        Math.sin(theta) * Math.cos(phi),
        Math.sin(theta) * Math.sin(phi),
        Math.cos(theta)
      );

      // Start near center
      const start = origin.clone().addScaledVector(dir, Math.random() * 2);
      const end = start.clone().addScaledVector(dir, 5 + Math.random() * 20);

      positions[i * 6 + 0] = start.x;
      positions[i * 6 + 1] = start.y;
      positions[i * 6 + 2] = start.z;
      positions[i * 6 + 3] = end.x;
      positions[i * 6 + 4] = end.y;
      positions[i * 6 + 5] = end.z;

      const color = palette[Math.floor(Math.random() * palette.length)];
      // Brighter near center → fade outward
      const c1 = color.clone().multiplyScalar(1.2);
      const c2 = color.clone().multiplyScalar(0.8);
      colors.set([c1.r, c1.g, c1.b, c2.r, c2.g, c2.b], i * 6);

      // Velocity and decay variance
      velocities[i] = 80 + Math.random() * 160; // rapid initial bursts
      decay[i] = 0.8 + Math.random() * 1.6; // seconds until strong fade
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    const material = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 1.0,
      blending: THREE.AdditiveBlending,
    });

    const lines = new THREE.LineSegments(geometry, material);
    scene.add(lines);

    // Subtle camera drift for depth
    let time = 0;
    const clock = new THREE.Clock();

    // Phase control: rapid expansion then fade
    const totalDuration = 3.5; // seconds
    const expandDuration = 1.4; // rapid move
    const lingerDuration = totalDuration - expandDuration; // fade-out

    const animate = () => {
      const dt = clock.getDelta();
      time += dt;

      const pos = geometry.getAttribute("position");
      const arr = pos.array;

      // Expand positions outward during expandDuration
      const expandFactor = Math.min(time / expandDuration, 1.0);
      for (let i = 0; i < particleCount; i++) {
        // Move both endpoints outward along their line direction
        const sx = arr[i * 6 + 0];
        const sy = arr[i * 6 + 1];
        const sz = arr[i * 6 + 2];
        const ex = arr[i * 6 + 3];
        const ey = arr[i * 6 + 4];
        const ez = arr[i * 6 + 5];

        // Direction from start to end
        const dx = ex - sx;
        const dy = ey - sy;
        const dz = ez - sz;

        const speed = velocities[i];
        const step = speed * dt * (1.0 - Math.pow(expandFactor, 0.5)); // fast then easing

        arr[i * 6 + 0] = sx - dx * step * 0.001;
        arr[i * 6 + 1] = sy - dy * step * 0.001;
        arr[i * 6 + 2] = sz - dz * step * 0.001;
        arr[i * 6 + 3] = ex + dx * step * 0.001;
        arr[i * 6 + 4] = ey + dy * step * 0.001;
        arr[i * 6 + 5] = ez + dz * step * 0.001;
      }
      pos.needsUpdate = true;

      // Fade out opacity during lingerDuration
      if (time > expandDuration) {
        const fadeT = (time - expandDuration) / lingerDuration;
        material.opacity = THREE.MathUtils.lerp(1.0, 0.0, fadeT);
      }

      // Gentle camera motion
      camera.position.x = Math.sin(time * 0.6) * 8;
      camera.position.y = Math.cos(time * 0.4) * 6;
      camera.lookAt(0, 0, 0);

      renderer.render(scene, camera);

      if (time >= totalDuration) {
        cleanup();
        onComplete?.();
        return;
      }
      requestAnimationFrame(animate);
    };

    const onResize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    window.addEventListener("resize", onResize);
    requestAnimationFrame(animate);

    const cleanup = () => {
      window.removeEventListener("resize", onResize);
      try {
        mountRef.current && mountRef.current.removeChild(renderer.domElement);
      } catch {}
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    };

    return cleanup;
  }, [onComplete]);

  return (
    <div
      ref={mountRef}
      className="fixed inset-0 z-50 bg-black"
      aria-hidden="true"
    />
  );
};

export default SplashBurst;
