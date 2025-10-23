import React from "react";
import { motion } from "framer-motion";

const GradientBackground = () => {
  return (
    <div className="absolute inset-0 overflow-hidden">
      <motion.div
        className="absolute -top-1/3 -left-1/3 w-[150%] h-[150%] bg-gradient-to-r from-sky-400 via-indigo-500 to-pink-500 blur-3xl opacity-40"
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, duration: 20, ease: "linear" }}
      />
    </div>
  );
};

export default GradientBackground;
