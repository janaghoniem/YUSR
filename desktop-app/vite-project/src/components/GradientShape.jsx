import React from "react";
import { motion } from "framer-motion";

const GradientShape = () => {
  return (
    <motion.div
      className="absolute w-[500px] h-[500px] rounded-full blur-3xl"
      style={{
        background: "linear-gradient(135deg, #38bdf8, #6366f1, #ec4899)",
        opacity: 0.7,
      }}
      animate={{
        x: ["-20%", "10%", "0%", "-15%"],
        y: ["0%", "10%", "-10%", "5%"],
        scale: [1, 1.2, 1, 0.9],
        rotate: [0, 90, 180, 360],
      }}
      transition={{
        duration: 15,
        ease: "easeInOut",
        repeat: Infinity,
      }}
    />
  );
};

export default GradientShape;
