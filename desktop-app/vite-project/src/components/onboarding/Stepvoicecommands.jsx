// StepVoiceCommands.jsx — Auto-spinning carousel:
//  • One command per slide for a shorter, cleaner page
//  • Reads only when the user asks or clicks the read aloud button
//  • Accessible controls for screen readers

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, ChevronLeft, ChevronRight, Pause, Play, Volume2 } from "lucide-react";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import screenReader from "../../utils/ScreenReader";

const COMMANDS = [
  { phrase: "AURA stop", effect: "Immediately stops whatever AURA is doing", effectAr: "يوقف كل حاجة فوراً", ar: "أورا وقف" },
  { phrase: "AURA pause", effect: "Pauses the current task", effectAr: "يوقف المهمة مؤقتاً", ar: "أورا انتظر" },
  { phrase: "AURA resume", effect: "Continues a paused task", effectAr: "يكمل المهمة اللي اتوقفت", ar: "أورا استمر" },
  { phrase: "AURA undo", effect: "Reverses the last completed action", effectAr: "يتراجع عن آخر حاجة اتعملت", ar: "أورا تراجع" },
  { phrase: "AURA redo", effect: "Re-attempts the last failed task", effectAr: "يعيد آخر مهمة فشلت", ar: null },
  { phrase: "AURA settings", effect: "Opens the settings panel", effectAr: "يفتح لوحة الإعدادات", ar: null },
  { phrase: "AURA new chat", effect: "Starts a fresh conversation", effectAr: "يبدأ محادثة جديدة", ar: null },
  { phrase: "AURA read that", effect: "Reads the last response aloud", effectAr: "يقرأ الرد الأخير بصوت عالي", ar: null },
  { phrase: "AURA help", effect: "Lists available commands", effectAr: "يعرض الأوامر المتاحة", ar: null },
];

const slideVariants = {
  enter: (dir) => ({ x: dir > 0 ? 52 : -52, opacity: 0, scale: 0.98, filter: "blur(4px)" }),
  center: {
    x: 0,
    opacity: 1,
    scale: 1,
    filter: "blur(0px)",
    transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] },
  },
  exit: (dir) => ({ x: dir > 0 ? -52 : 52, opacity: 0, scale: 0.98, filter: "blur(4px)", transition: { duration: 0.2 } }),
};

const AUTO_SPIN_MS = 7600;

const buildCommandSpeech = (command, isAr) => {
  const commandLabel = isAr ? (command.ar || command.phrase) : command.phrase;
  const effectLabel = isAr ? command.effectAr : command.effect;
  const extraLabel = isAr
    ? command.phrase !== command.ar && command.phrase ? `, ${command.phrase}` : ""
    : command.ar ? `, Arabic: ${command.ar}` : "";

  return `${commandLabel}. ${effectLabel}${extraLabel}.`;
};

const StepVoiceCommands = ({ onNext, lang = "en" }) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [direction, setDirection] = useState(1);
  const [isReading, setIsReading] = useState(false);
  const isAr = lang === "ar";

  const activeCommand = useMemo(() => COMMANDS[activeIndex], [activeIndex]);

  const currentSpeech = useMemo(() => buildCommandSpeech(activeCommand, isAr), [activeCommand, isAr]);

  useEffect(() => {
    screenReader.setLanguage(isAr ? "ar" : "en");
    return () => screenReader.setLanguage(null);
  }, [isAr]);

  useEffect(() => {
    setActiveIndex(0);
    setIsPlaying(true);
    setIsReading(false);
    screenReader.stop();
  }, [isAr]);

  useEffect(() => {
    if (!isPlaying || COMMANDS.length < 2) return;
    const timer = window.setInterval(() => {
      setDirection(1);
      setActiveIndex((current) => (current + 1) % COMMANDS.length);
    }, AUTO_SPIN_MS);

    return () => window.clearInterval(timer);
  }, [isPlaying]);

  const goToIndex = useCallback((index) => {
    setDirection(index >= activeIndex ? 1 : -1);
    setActiveIndex(index);
  }, [activeIndex]);

  const handlePrev = useCallback(() => {
    setIsPlaying(false);
    setDirection(-1);
    setActiveIndex((current) => (current - 1 + COMMANDS.length) % COMMANDS.length);
  }, []);

  const handleNextSlide = useCallback(() => {
    setIsPlaying(false);
    setDirection(1);
    setActiveIndex((current) => (current + 1) % COMMANDS.length);
  }, []);

  const readCurrentCommand = useCallback(async () => {
    setIsReading(true);
    screenReader.stop();

    try {
      await screenReader.speak(currentSpeech);
    } finally {
      setIsReading(false);
    }
  }, [currentSpeech]);

  const toggleAutoplay = useCallback(() => {
    setIsPlaying((current) => !current);
  }, []);

  const progress = ((activeIndex + 1) / COMMANDS.length) * 100;

  const continueLabel = isAr ? "تمام، نعمل حسابك →" : "Got it, let's create your account →";

  return (
    <section className="onboarding-step voice-carousel-step" role="region" aria-labelledby="vc-title" aria-describedby="vc-summary vc-help">
      <div className="voice-carousel-shell">
        <div className="voice-carousel-panel">
          <div className="voice-carousel-hero">
            <h2 className="onboarding-title" id="vc-title">
              <BlurText text={isAr ? "صوتك هو الريموت" : "Your voice is the remote"} delay={45} />
            </h2>

            <p className="onboarding-subtitle" id="vc-summary">
              {isAr
                ? "الصفحة دي أخف من قبل: أمر واحد ظاهر في كل مرة، وكل تبديل بيتقال بصوت واضح علشان تفضل متابع من غير زحمة."
                : "This page is lighter now: one command is shown at a time, and each rotation is read aloud so the flow stays clear."}
            </p>

            <div className="voice-carousel-controls" aria-label={isAr ? "التحكم في الكاروسيل" : "Carousel controls"}>
              <button
                className="onboarding-btn primary"
                type="button"
                onClick={readCurrentCommand}
                aria-label={isAr ? "اقرأ الأمر الحالي بصوت عالٍ" : "Read the current command aloud"}
                aria-busy={isReading}
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                <Volume2 size={14} aria-hidden="true" />
                <span>{isReading ? (isAr ? "جارِ القراءة..." : "Reading...") : (isAr ? "اقرأ بصوت عالٍ" : "Read aloud")}</span>
              </button>

              <button
                className="onboarding-btn ghost"
                type="button"
                onClick={toggleAutoplay}
                aria-pressed={!isPlaying}
                aria-label={isPlaying ? (isAr ? "أوقف الحركة التلقائية" : "Pause automatic spinning") : (isAr ? "شغل الحركة التلقائية" : "Resume automatic spinning")}
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                {isPlaying ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
                <span>{isPlaying ? (isAr ? "إيقاف مؤقت" : "Pause") : (isAr ? "تشغيل" : "Play")}</span>
              </button>

              <button
                className="onboarding-btn ghost"
                type="button"
                onClick={handlePrev}
                aria-label={isAr ? "الأمر السابق" : "Previous command"}
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                <span>{isAr ? "السابق" : "Prev"}</span>
              </button>

              <button
                className="onboarding-btn ghost"
                type="button"
                onClick={handleNextSlide}
                aria-label={isAr ? "الأمر التالي" : "Next command"}
                style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
              >
                <span>{isAr ? "التالي" : "Next"}</span>
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="voice-carousel-progress" aria-hidden="true">
            <div className="voice-carousel-progress-fill" style={{ width: `${progress}%` }} />
          </div>

          <AnimatePresence mode="wait" custom={direction} initial={false}>
            <motion.article
              key={activeIndex}
              custom={direction}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              className="voice-carousel-slide"
              aria-roledescription="slide"
              aria-label={isAr ? `الشريحة ${activeIndex + 1} من ${COMMANDS.length}` : `Slide ${activeIndex + 1} of ${COMMANDS.length}`}
            >
              <div className="voice-carousel-slide-top">
                <span className="voice-carousel-counter">
                  {activeIndex + 1}/{COMMANDS.length}
                </span>
                <span className="voice-carousel-status">
                  {isPlaying ? (isAr ? "يتحرك تلقائياً" : "Auto spinning") : (isAr ? "متوقف مؤقتاً" : "Paused")}
                </span>
              </div>

              <h3 className="voice-carousel-command">{activeCommand.phrase}</h3>
              <p className="voice-carousel-effect">{isAr ? activeCommand.effectAr : activeCommand.effect}</p>
              {activeCommand.ar && (
                <p className="voice-carousel-phrase-ar" dir="rtl">
                  {activeCommand.ar}
                </p>
              )}
              <p className="voice-carousel-caption">
                <Volume2 size={13} aria-hidden="true" />
                <span>{isAr ? "ده الأمر اللي أورا هتقراه دلوقتي." : "This is the command AURA is reading right now."}</span>
              </p>
            </motion.article>
          </AnimatePresence>

          <div className="voice-carousel-dots" role="tablist" aria-label={isAr ? "اختيار الأمر" : "Select a command"}>
            {COMMANDS.map((command, index) => (
              <button
                key={command.phrase}
                type="button"
                className={`voice-carousel-dot ${index === activeIndex ? "active" : ""}`}
                role="tab"
                aria-selected={index === activeIndex}
                aria-label={isAr ? `${command.phrase}، الشريحة ${index + 1}` : `${command.phrase}, slide ${index + 1}`}
                onClick={() => {
                  setIsPlaying(false);
                  goToIndex(index);
                }}
              />
            ))}
          </div>

          <p className="voice-carousel-note">
            {isAr
              ? "الكاروسيل بيتحرك تلقائياً، لكن أورا ما بتقرأش إلا لما تضغط اقرأ بصوت عالٍ أو لو استخدمت أزرار التحكم."
              : "The carousel keeps moving automatically, but AURA only speaks when you press Read aloud or use the controls."}
          </p>

          <div className="voice-carousel-actions">
            <button
              className="onboarding-btn primary"
              type="button"
              onClick={onNext}
              aria-label={isAr ? "كمل لإنشاء حسابك" : "Continue to create your account"}
              style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
            >
              <ShinyText text={continueLabel} speed={3} />
              <ArrowRight size={14} aria-hidden="true" />
            </button>
          </div>

          <p id="vc-help" className="sr-only">
            {isAr
              ? "استخدم زر اقرأ بصوت عالٍ لسماع الأمر الحالي، أو السابق والتالي للتنقل بين الأوامر."
              : "Use Read aloud to hear the current command, or previous and next to move between commands."}
          </p>
        </div>
      </div>
    </section>
  );
};

export default StepVoiceCommands;