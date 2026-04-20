import React, { useEffect, useRef } from "react";
import * as THREE from "three";

const BackgroundCanvas = () => {
  const mountRef = useRef(null);

  useEffect(() => {
    if (!mountRef.current) return;

    let frameId;
    let renderer, scene, camera, particles, geometry;

    /* ------------------ SCENE INIT ------------------ */
    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x0f0c0a, 0.02);

    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(0, 8, 18);
    camera.lookAt(0, 0, 0);

    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0); // transparent
    mountRef.current.appendChild(renderer.domElement);

    /* ------------------ RESIZE (ROBUST) ------------------ */
    const resize = () => {
      const width = document.documentElement.clientWidth || window.innerWidth;
      const height = document.documentElement.clientHeight || window.innerHeight;

      if (width && height) {
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      }
    };

    resize();

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(document.body);

    /* ------------------ BLOBS ------------------ */
    const blobGeo = new THREE.SphereGeometry(9, 48, 48);

    const blob1 = new THREE.Mesh(
      blobGeo,
      new THREE.MeshBasicMaterial({
        color: 0x8a0a4a,
        transparent: true,
        opacity: 0.7,
      })
    );
    blob1.position.set(-8, 4, -12);

    const blob2 = new THREE.Mesh(
      blobGeo,
      new THREE.MeshBasicMaterial({
        color: 0x3f155f,
        transparent: true,
        opacity: 0.7,
      })
    );
    blob2.position.set(8, -4, -15);

    const blobgeo2 = new THREE.BoxGeometry(4, 4, 4);
    const testCube = new THREE.Mesh(
      blobgeo2,
      new THREE.MeshBasicMaterial({ color: 0x00ff00, wireframe: true })
    );
    scene.add(testCube);

    scene.add(blob1, blob2);

    /* ------------------ PARTICLES ------------------ */
    const grid = 100;
    const count = grid * grid;

    geometry = new THREE.BufferGeometry();

    const positions = new Float32Array(count * 3);
    const base = new Float32Array(count * 3);

    let i3 = 0;

    for (let i = 0; i < grid; i++) {
      for (let j = 0; j < grid; j++) {
        const x = (i - grid / 2) * 0.4;
        const y = (j - grid / 2) * 0.4;

        positions[i3] = base[i3] = x;
        positions[i3 + 1] = base[i3 + 1] = y;
        positions[i3 + 2] = base[i3 + 2] = 0;

        i3 += 3;
      }
    }

    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    /* texture */
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 64;

    const ctx = canvas.getContext("2d");
    const grad = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255, 255, 255, 1)");
    grad.addColorStop(1, "rgba(255, 255, 255, 0)");

    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 64, 64);

    const texture = new THREE.CanvasTexture(canvas);

    const material = new THREE.PointsMaterial({
      size: 2.5,
      map: texture,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });

    particles = new THREE.Points(geometry, material);
    particles.rotation.x = -Math.PI / 2.5;
    scene.add(particles);

    /* ------------------ ANIMATION ------------------ */
    const clock = new THREE.Clock();

    const animate = () => {
      const t = clock.getElapsedTime();

      const pos = geometry.attributes.position.array;

      for (let i = 0; i < count; i++) {
        const x = base[i * 3];
        const y = base[i * 3 + 1];
        const d = Math.sqrt(x * x + y * y);

        const wave =
          Math.sin(d * 1.5 - t * 2) +
          Math.sin(x * 0.8 + t) * Math.cos(y * 0.8 - t);

        pos[i * 3 + 2] = wave * (1 - d / 25) * 6.0;
      }

      geometry.attributes.position.needsUpdate = true;

      /* cube rotation */
      if (testCube) {
        testCube.rotation.x += 0.01;
        testCube.rotation.y += 0.02;
      }

      /* blob motion */
      blob1.position.x = -8 + Math.sin(t * 0.6) * 3;
      blob1.position.y = 4 + Math.cos(t * 0.5) * 3;

      blob2.position.x = 8 + Math.sin(t * 0.7) * 4;
      blob2.position.y = -4 + Math.cos(t * 0.6) * 3;

      renderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    };

    animate();

    /* ------------------ VISIBILITY / PERFORMANCE CHECK ------------------ */
    const handleVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(frameId);
        frameId = null;
      } else if (!frameId) {
        clock.getElapsedTime(); // Refresh clock
        animate();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    /* ------------------ CLEANUP ------------------ */
    return () => {
      cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", handleVisibility);

      geometry.dispose();
      renderer.dispose();

      if (mountRef.current?.contains(renderer.domElement)) {
        mountRef.current.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <div
      ref={mountRef}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: -1,
        pointerEvents: "none",
      }}
    />
  );
};

export default BackgroundCanvas;