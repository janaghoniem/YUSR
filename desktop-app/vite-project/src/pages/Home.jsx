import React from "react";
import SpeechInput from "../components/SpeechInput";

const Home = () => {
  return (
    <div className="relative flex flex-col justify-center items-center min-h-screen text-center z-10">
      <h1 className="text-6xl md:text-7xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-sky-400 via-indigo-400 to-pink-500 mb-4">
        YUSR
      </h1>
      <p className="text-lg md:text-xl text-gray-300 max-w-2xl">
        Your Unified Smart Reasoner — a personal AI companion designed for accessibility and autonomy.
      </p>

      <div className="mt-10 w-full flex justify-center">
        <div className="w-[90%] md:w-[50%] backdrop-blur-md bg-white/10 rounded-2xl p-6 shadow-xl border border-white/20">
          <SpeechInput />
        </div>
      </div>

      <footer className="absolute bottom-6 text-gray-500 text-sm">
        Speak naturally — YUSR listens and acts.
      </footer>
    </div>
  );
};

export default Home;
