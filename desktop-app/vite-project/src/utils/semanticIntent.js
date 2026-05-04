const ARABIC_DIACRITICS = /[\u064B-\u065F\u0670\u06D6-\u06ED]/g;

export const normalizeSemanticText = (value = "") => {
  return String(value)
    .toLowerCase()
    .replace(ARABIC_DIACRITICS, "")
    .replace(/[أإآ]/g, "ا")
    .replace(/[ؤ]/g, "و")
    .replace(/[ئ]/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/ى/g, "ي")
    .replace(/[^a-z0-9\u0600-\u06FF\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
};

const tokenize = (value = "") => {
  return normalizeSemanticText(value).split(" ").filter(Boolean);
};

const includesAny = (tokens, lexicon) => lexicon.some((token) => tokens.includes(token));

const EN_AFFIRMATIVE = ["yes", "yeah", "yep", "sure", "ok", "okay", "approve", "confirm", "go", "continue", "proceed", "fine"];
const AR_AFFIRMATIVE = ["نعم", "ايوه", "ايوا", "تمام", "موافق", "اكيد", "وافق", "استمر", "كمل"];
const EN_NEGATIVE = ["no", "nope", "nah", "dont", "skip", "reject", "decline", "cancel", "stop"];
const AR_NEGATIVE = ["لا", "كلا", "ارفض", "رفض", "تخطي", "الغاء", "وقف", "توقف"];

export const classifyPolarIntent = (value = "", langHint = "en") => {
  const tokens = tokenize(value);
  if (!tokens.length) return "neutral";

  const affirmative = includesAny(tokens, EN_AFFIRMATIVE) || includesAny(tokens, AR_AFFIRMATIVE);
  const negative = includesAny(tokens, EN_NEGATIVE) || includesAny(tokens, AR_NEGATIVE);

  if (affirmative && !negative) return "affirmative";
  if (negative && !affirmative) return "negative";

  if (langHint === "ar" && tokens.includes("نعم")) return "affirmative";
  if (langHint === "ar" && tokens.includes("لا")) return "negative";

  return "neutral";
};

const EN_READ = ["read", "listen", "aloud", "speak", "voice", "explain"];
const AR_READ = ["اقرا", "اقرأ", "اسمع", "اسمعني", "صوت", "اشرح"];
const EN_CONTENT = ["result", "results", "content", "draft", "response", "plan", "text", "page"];
const AR_CONTENT = ["نتيجه", "نتايج", "المحتوي", "المحتوى", "المسوده", "مسوده", "الرد", "الخطة", "صفحه"];

export const isReadAloudIntentSemantic = (value = "") => {
  const tokens = tokenize(value);
  if (!tokens.length) return false;

  const hasReadToken = includesAny(tokens, EN_READ) || includesAny(tokens, AR_READ);
  const hasContentToken = includesAny(tokens, EN_CONTENT) || includesAny(tokens, AR_CONTENT);

  // "read" alone is enough if there is no obvious contradiction.
  return hasReadToken && (hasContentToken || tokens.length <= 3);
};

const STOP_TERMS = ["stop", "cancel", "undo", "وقف", "توقف", "الغاء", "إلغاء"];
const PAUSE_TERMS = ["pause", "hold", "wait", "ايقاف", "إيقاف", "هدي", "استني"];
const RESUME_TERMS = ["resume", "continue", "proceed", "استمر", "كمل", "اكمل", "أكمل"];

export const classifyInterruptSemantic = (value = "") => {
  const tokens = tokenize(value);
  if (!tokens.length) return null;

  const hasAuraWord = tokens.includes("aura") || tokens.includes("اورا") || tokens.includes("أورا");

  if (hasAuraWord && includesAny(tokens, STOP_TERMS)) return "stop";
  if (hasAuraWord && includesAny(tokens, PAUSE_TERMS)) return "pause";
  if (hasAuraWord && includesAny(tokens, RESUME_TERMS)) return "resume";

  // Also allow direct control words when the utterance is very short.
  if (tokens.length <= 2 && includesAny(tokens, STOP_TERMS)) return "stop";
  if (tokens.length <= 2 && includesAny(tokens, PAUSE_TERMS)) return "pause";
  if (tokens.length <= 2 && includesAny(tokens, RESUME_TERMS)) return "resume";

  return null;
};
