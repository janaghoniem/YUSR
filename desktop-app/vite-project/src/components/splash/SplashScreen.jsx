import React, { useEffect, useState } from 'react';

const SplashScreen = ({ onFinish }) => {
  const [fadingOut, setFadingOut] = useState(false);

  useEffect(() => {
    // Show splash screen for at least 3-4 seconds before starting fading out.
    const splashTimer = setTimeout(() => {
      setFadingOut(true);
      // Wait for fade out animation to finish before calling onFinish
      setTimeout(onFinish, 1000); 
    }, 8000);

    return () => clearTimeout(splashTimer);
  }, [onFinish]);

  return (
    <div 
      className={`fixed inset-0 z-50 transition-opacity duration-1000 ease-in-out bg-black ${fadingOut ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
      style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}
      onClick={() => {
        if (!fadingOut) {
          setFadingOut(true);
          setTimeout(onFinish, 1000);
        }
      }}
    >
      <iframe
        src="/aura-cinematic-intro.html"
        style={{
          width: '100vw',
          height: '100vh',
          border: 'none',
          pointerEvents: 'none'
        }}
        title="AURA Startup Animation"
      />
      
      {/* Optional skip overlay */}
      <div className="absolute bottom-6 text-white/30 text-xs tracking-[0.2em] uppercase font-light pointer-events-none">
        Click anywhere to skip
      </div>
    </div>
  );
};

export default SplashScreen;