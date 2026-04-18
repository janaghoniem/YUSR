import { motion } from 'framer-motion';

const SplitText = ({ text, className = '', delay = 40 }) => {
  const letters = text.split("");

  return (
    <span className={className}>
      {letters.map((letter, i) => (
        <motion.span
          key={i}
          initial={{ opacity: 0, y: '50%', rotateX: 90 }}
          animate={{ opacity: 1, y: '0%', rotateX: 0 }}
          transition={{ 
            delay: i * (delay / 1000), 
            duration: 0.5, 
            type: 'spring', 
            damping: 12, 
            stiffness: 100 
          }}
          style={{ display: 'inline-block', transformOrigin: '50% 50% -20px' }}
        >
          {letter === " " ? "\u00A0" : letter}
        </motion.span>
      ))}
    </span>
  );
};

export default SplitText;