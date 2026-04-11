import { useRef, useEffect, useState } from 'react';
import { motion, useSpring, useTransform } from 'framer-motion';

const BlurText = ({ text, delay = 200, className = '', animateBy = 'words' }) => {
  const elements = animateBy === 'words' ? text.split(' ') : text.split('');
  
  return (
    <span className={className}>
      {elements.map((el, i) => (
        <motion.span
          key={i}
          initial={{ filter: 'blur(10px)', opacity: 0, y: 10 }}
          animate={{ filter: 'blur(0px)', opacity: 1, y: 0 }}
          transition={{ delay: i * (delay / 1000), duration: 0.6, ease: 'easeOut' }}
          style={{ display: 'inline-block', marginRight: animateBy === 'words' ? '0.3em' : '0' }}
        >
          {el === " " ? "\u00A0" : el}
        </motion.span>
      ))}
    </span>
  );
};

export default BlurText;