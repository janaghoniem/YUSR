const VoiceVisualizer = ({ t, glowColor, pulseColor }) => {
    
    // Calculate dynamic styles using the pre-calculated values
    const visualizerStyle = {
        // 1. Shape/Scale Change (Pulsing)
        transform: `scale(${1 + t * 0.1})`, // Max scale of 1.1 at max intensity
        transition: 'all 0.1s ease-out', // Smooth pulse response

        // Dynamic gradient for the inner circle
        background: `radial-gradient(circle at 50% 50%, 
            ${glowColor} 0%, 
            ${lerpColor(PALETTE.IDLE_COLOR_RGB, [0, 0, 0], t * 0.5)} 100%)`, // Uses t and PALETTE defined globally

        // Dynamic Box Shadow (Glow Intensity)
        boxShadow: `0 0 ${10 + t * 40}px ${pulseColor}, 
                    0 0 ${5 + t * 20}px ${glowColor} inset`,
        
        opacity: 0.8 + t * 0.2,
    };

    // The component is designed to run in a dark environment.
    return (
        <div className="flex items-center justify-center">
            
            {/* Custom CSS for the liquid flow and base styles */}
            <style jsx="true">{`
                @keyframes liquid-flow {
                    0% {
                        transform: translate(-15%, -15%) rotate(0deg) scale(1.1);
                    }
                    50% {
                        transform: translate(15%, 15%) rotate(180deg) scale(1.0);
                    }
                    100% {
                        transform: translate(-15%, -15%) rotate(360deg) scale(1.1);
                    }
                }
                .visualizer-base {
                    position: relative;
                    width: 200px;
                    height: 200px;
                    border-radius: 50%;
                    background-color: transparent;
                    filter: saturate(1.5) contrast(1.2);
                }
                .visualizer-base::before {
                    content: '';
                    position: absolute;
                    inset: 10px;
                    border-radius: 50%;
                    /* 2. Core Effect: Liquid Flow */
                    background: radial-gradient(
                        circle at 50% 50%,
                        rgba(255, 255, 255, 0.2) 0%,
                        rgba(0, 255, 255, 0.3) 30%,
                        rgba(74, 0, 224, 0) 70%
                    );
                    /* Dynamic animation speed based on intensity factor (t) */
                    animation: liquid-flow ${4 - t * 3}s linear infinite; 
                    mix-blend-mode: soft-light;
                    opacity: 0.8;
                }
            `}</style>

            <div 
                className="visualizer-base"
                style={visualizerStyle}
            >
            </div>
        </div>
    );
};

export default VoiceVisualizer;