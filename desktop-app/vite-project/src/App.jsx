import React, { useState, useCallback } from "react";
import SplashBurst from "./components/SplashBurst";
import GradientBlobs from "./components/GradientBlobs";
import SpeechInput from "./components/SpeechInput";

function App() {
  const [showSplash, setShowSplash] = useState(true);
  const [agentResponse, setAgentResponse] = useState("Tap the mic and tell me your research task.");

  // Callback to handle the user's command
  const handleTranscript = useCallback((text) => {
    console.log("User said:", text);
    
    // Placeholder logic: This is where you would call the Gemini API
    setAgentResponse(`Got it! Analyzing your request: "${text}"...`);
    
  }, []);

  return (
    <div className="relative min-h-screen">
      {showSplash && (
        <SplashBurst onComplete={() => setShowSplash(false)} />
      )}

      {!showSplash && (
        <>
          <GradientBlobs />
          <div className="relative z-10 flex items-center justify-center min-h-screen text-white p-6">
            <div className="flex flex-col items-center">
              
              {/* Application Text Block */}
              <div className="text-center mb-10">
                <h1 className="text-5xl font-extrabold mb-2 bg-clip-text text-transparent bg-gradient-to-r from-pink-400 to-indigo-400">
                    YUSR
                </h1>
                <p className="text-lg text-gray-300">Your intelligent accessibility assistant.</p>
                <div className="mt-4 p-4 rounded-xl max-w-lg bg-white/10 backdrop-blur-sm shadow-xl">
                    <p className="text-white font-medium">Agent: {agentResponse}</p>
                </div>
              </div>

              {/* Speech Input Component (Placed directly below the text) */}
              <SpeechInput
                onTranscript={handleTranscript}
              />
              
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default App;
