import React, { useRef, useEffect } from "react";
import * as THREE from "three";

const FlowyRibbonBackground = () => {
  const mountRef = useRef(null);

  useEffect(() => {
    if (!mountRef.current) return;

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      0.1,
      1000
    );
    camera.position.z = 20;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 1);
    mountRef.current.appendChild(renderer.domElement);

    const NUM_RIBBONS = 4;
    const ribbons = [];

    const createRibbon = () => {
      const length = 15 + Math.random() * 10;
      const segments = 120;
      const width = 0.3 + Math.random() * 0.4;

      const positions = [];
      const uvs = [];
      const indices = [];

      for (let i = 0; i <= segments; i++) {
        const t = i / segments;
        const y = t * length;
        const offset = width / 2;

        // Left vertex
        positions.push(-offset, y, 0);
        uvs.push(0, t);

        // Right vertex
        positions.push(offset, y, 0);
        uvs.push(1, t);
      }

      for (let i = 0; i < segments; i++) {
        const a = i * 2;
        const b = i * 2 + 1;
        const c = i * 2 + 2;
        const d = i * 2 + 3;

        indices.push(a, b, c);
        indices.push(b, d, c);
      }

      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
      geometry.setIndex(indices);
      geometry.computeVertexNormals();

      const material = new THREE.ShaderMaterial({
        uniforms: {
          time: { value: 0 },
          color1: { value: new THREE.Color(Math.random(), Math.random(), Math.random()) },
          color2: { value: new THREE.Color(Math.random(), Math.random(), Math.random()) },
          amplitude: { value: 0.5 + Math.random() * 0.5 },
          frequency: { value: 1 + Math.random() * 2 },
        },
        vertexShader: `
          uniform float time;
          uniform float amplitude;
          uniform float frequency;
          varying vec2 vUv;
          void main() {
            vUv = uv;
            vec3 pos = position;

            float wave = sin(pos.y * frequency + time) * amplitude;
            pos.x += wave;
            pos.z += cos(pos.y * frequency + time) * amplitude * 0.5;

            gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 color1;
          uniform vec3 color2;
          varying vec2 vUv;
          void main() {
            vec3 color = mix(color1, color2, vUv.y);
            gl_FragColor = vec4(color, 0.7);
          }
        `,
        side: THREE.DoubleSide,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.x = (Math.random() - 0.5) * 10;
      mesh.position.y = -length / 2 + (Math.random() - 0.5) * 2;
      mesh.position.z = (Math.random() - 0.5) * 5;
      mesh.userData = {
        speed: 0.5 + Math.random() * 0.5,
        phase: Math.random() * Math.PI * 2,
      };

      scene.add(mesh);
      ribbons.push(mesh);
    };

    for (let i = 0; i < NUM_RIBBONS; i++) createRibbon();

    const clock = new THREE.Clock();

    const animate = () => {
      requestAnimationFrame(animate);
      const elapsed = clock.getElapsedTime();

      ribbons.forEach((ribbon) => {
        ribbon.material.uniforms.time.value = elapsed * ribbon.userData.speed;
      });

      renderer.render(scene, camera);
    };
    animate();

    const handleResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      if (mountRef.current && renderer.domElement.parentNode === mountRef.current) {
        mountRef.current.removeChild(renderer.domElement);
      }
    };
  }, []);

  return <div ref={mountRef} className="absolute top-0 left-0 w-full h-full -z-10 pointer-events-none" />;
};

export default FlowyRibbonBackground;
