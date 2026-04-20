import React, { useRef } from 'react';
import { motion, useSpring, useMotionValue, useTransform } from 'framer-motion';

export default function TiltedCard({ children, className = '', containerClassName = '' }) {
  const ref = useRef(null);

  const x = useMotionValue(0.5);
  const y = useMotionValue(0.5);

  const mouseXSpring = useSpring(x, { stiffness: 300, damping: 20 });
  const mouseYSpring = useSpring(y, { stiffness: 300, damping: 20 });

  const rotateX = useTransform(mouseYSpring, [0, 1], ["20deg", "-20deg"]);
  const rotateY = useTransform(mouseXSpring, [0, 1], ["-20deg", "20deg"]);

  const handleMouseMove = (e) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;

    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    x.set(mouseX / width);
    y.set(mouseY / height);
  };

  const handleMouseLeave = () => {
    x.set(0.5);
    y.set(0.5);
  };

  return (
    <div className={containerClassName} style={{ perspective: "1000px" }}>
      <motion.div
        ref={ref}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        style={{
          rotateX,
          rotateY,
          transformStyle: "preserve-3d",
          width: "100%",
          height: "100%",
        }}
        className={className}
      >
        {children}
      </motion.div>
    </div>
  );
}