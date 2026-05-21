/**
 * RTL/LTR Language Detection Utilities
 * Supports automatic direction detection and text alignment
 */

/**
 * List of RTL (Right-to-Left) language codes
 */
const RTL_LANGUAGES = new Set(["ar", "he", "fa", "ur", "yi", "iw"]);

/**
 * Determine if a language is RTL (Right-to-Left)
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 * @returns {boolean} - True if the language is RTL
 */
export const isRTLLanguage = (languageCode) => {
  return RTL_LANGUAGES.has(languageCode?.toLowerCase());
};

/**
 * Get CSS direction value for a language
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 * @returns {string} - "rtl" or "ltr"
 */
export const getDirection = (languageCode) => {
  return isRTLLanguage(languageCode) ? "rtl" : "ltr";
};

/**
 * Get text alignment value for a language
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 * @returns {string} - "right" for RTL languages, "left" for LTR
 */
export const getTextAlign = (languageCode) => {
  return isRTLLanguage(languageCode) ? "right" : "left";
};

/**
 * Get flexDirection value for a language
 * Used for reversing flex order in RTL layouts
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 * @returns {string} - "row-reverse" for RTL, "row" for LTR
 */
export const getFlexDirection = (languageCode) => {
  return isRTLLanguage(languageCode) ? "row-reverse" : "row";
};

/**
 * Apply RTL/LTR document direction
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 */
export const applyDocumentDirection = (languageCode) => {
  const direction = getDirection(languageCode);
  document.documentElement.dir = direction;
  document.documentElement.lang = languageCode;
};

/**
 * Get margin values that respect RTL/LTR
 * @param {string} languageCode - Language code (e.g., "ar", "en")
 * @param {object} margins - Object with ltr and rtl margin properties
 * @returns {object} - Margin values for current direction
 */
export const getResponsiveMargin = (languageCode, { ltr = {}, rtl = {} } = {}) => {
  return isRTLLanguage(languageCode) ? rtl : ltr;
};
