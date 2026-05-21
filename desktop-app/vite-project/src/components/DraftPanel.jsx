// DraftPanel.jsx — Claude-style side canvas panel that slides in from the right
import React, { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { ChevronRight, Check, X, Volume2, SkipForward, ChevronLeft, FileText } from 'lucide-react';

/**
 * DraftPanel — A full-height side panel (canvas) that slides in from the right.
 * Mirrors Claude's artifact/canvas panel behavior.
 * 
 * Props:
 *   isOpen            — boolean to show/hide
 *   draftFlow         — { pages, pageIndex, awaitingListenChoice, awaitingPageApproval, awaitingFinalApproval, fullContent }
 *   onApprove         — called when user approves current page or final
 *   onReject          — called when user rejects final
 *   onContinue        — called to advance to next page
 *   onListenChoice    — called with (boolean) when user chooses to listen or skip
 *   onClose           — called to dismiss the panel
 *   t                 — i18n helper (en, ar) => string
 */
export function DraftPanel({
  isOpen,
  draftFlow,
  onApprove,
  onReject,
  onContinue,
  onListenChoice,
  onClose,
  t = (en) => en,
}) {
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const currentPage = draftFlow.pages?.[draftFlow.pageIndex];
  const totalPages  = draftFlow.pages?.length || 0;

  // Trap focus inside panel when open
  useEffect(() => {
    if (!isOpen) return;
    const el = panelRef.current;
    if (!el) return;

    // Focus the close button on open
    closeRef.current?.focus();

    const focusable = el.querySelectorAll(
      'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];

    const onKeyDown = (e) => {
      if (e.key === 'Escape') { onClose?.(); return; }
      if (e.key !== 'Tab') return;
      if (e.shiftKey) {
        if (document.activeElement === first) { e.preventDefault(); last?.focus(); }
      } else {
        if (document.activeElement === last)  { e.preventDefault(); first?.focus(); }
      }
    };

    el.addEventListener('keydown', onKeyDown);
    return () => el.removeEventListener('keydown', onKeyDown);
  }, [isOpen, onClose]);

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop — subtle, doesn't block main UI on desktop */}
          <motion.div
            className="draft-panel-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.22 }}
            onClick={onClose}
            aria-hidden="true"
          />

          {/* Side panel */}
          <motion.aside
            ref={panelRef}
            className="draft-panel"
            role="complementary"
            aria-label={t("Draft content panel", "لوحة المحتوى المسود")}
            aria-modal="false"
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 32, mass: 0.9 }}
          >
            {/* Header */}
            <div className="draft-panel-header" role="banner">
              <div className="draft-panel-header-left">
                <div className="draft-panel-icon" aria-hidden="true">
                  <FileText size={16} />
                </div>
                <div>
                  <h2 className="draft-panel-title" id="draft-panel-heading">
                    {t("Draft Content", "المحتوى المسود")}
                  </h2>
                  {totalPages > 0 && (
                    <p className="draft-panel-meta" aria-live="polite">
                      {t(`Page ${draftFlow.pageIndex + 1} of ${totalPages}`, `صفحة ${draftFlow.pageIndex + 1} من ${totalPages}`)}
                    </p>
                  )}
                </div>
              </div>
              <button
                ref={closeRef}
                className="draft-panel-close"
                onClick={onClose}
                aria-label={t("Close draft panel", "إغلاق لوحة المحتوى")}
                type="button"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>

            {/* Progress bar */}
            {totalPages > 1 && (
              <div
                className="draft-panel-progress-track"
                role="progressbar"
                aria-valuenow={draftFlow.pageIndex + 1}
                aria-valuemin={1}
                aria-valuemax={totalPages}
                aria-label={t("Draft progress", "تقدم المسودة")}
              >
                <div
                  className="draft-panel-progress-fill"
                  style={{ width: `${((draftFlow.pageIndex + 1) / totalPages) * 100}%` }}
                />
              </div>
            )}

            {/* Body */}
            <div className="draft-panel-body" role="main" aria-labelledby="draft-panel-heading">

              {/* Listen choice */}
              {draftFlow.awaitingListenChoice && (
                <div className="draft-choice-card" role="region" aria-label={t("Listen to draft", "استماع للمسودة")}>
                  <p className="draft-choice-question">
                    {t("Would you like to listen to the drafted content?", "هل تريد الاستماع إلى المحتوى المسود؟")}
                  </p>
                  <div className="draft-choice-actions" role="group" aria-label={t("Listen options", "خيارات الاستماع")}>
                    <button
                      className="draft-btn draft-btn-primary"
                      onClick={() => onListenChoice(true)}
                      type="button"
                      aria-label={t("Listen to draft content", "استمع للمحتوى المسود")}
                    >
                      <Volume2 size={15} aria-hidden="true" />
                      {t("Listen", "استماع")}
                    </button>
                    <button
                      className="draft-btn draft-btn-ghost"
                      onClick={() => onListenChoice(false)}
                      type="button"
                      aria-label={t("Skip listening and proceed", "تخطي الاستماع والمتابعة")}
                    >
                      <SkipForward size={15} aria-hidden="true" />
                      {t("Skip", "تخطي")}
                    </button>
                  </div>
                </div>
              )}

              {/* Page approval */}
              {draftFlow.awaitingPageApproval && currentPage && (
                <div className="draft-page-section" role="region" aria-label={t(`Page ${draftFlow.pageIndex + 1} content`, `محتوى الصفحة ${draftFlow.pageIndex + 1}`)}>
                  <div className="draft-page-content" aria-live="polite">
                    <div className="draft-page-label" aria-hidden="true">
                      {t("Page", "صفحة")} {draftFlow.pageIndex + 1}
                    </div>
                    <div
                      className="draft-page-text"
                      tabIndex={0}
                      aria-label={t(`Page ${draftFlow.pageIndex + 1} text`, `نص الصفحة ${draftFlow.pageIndex + 1}`)}
                    >
                      {currentPage.content}
                    </div>
                  </div>

                  <div className="draft-page-actions" role="group" aria-label={t("Page actions", "إجراءات الصفحة")}>
                    <button
                      className="draft-btn draft-btn-approve"
                      onClick={onApprove}
                      type="button"
                      aria-label={t("Approve this page", "الموافقة على هذه الصفحة")}
                    >
                      <Check size={15} aria-hidden="true" />
                      {t("Approve", "موافقة")}
                    </button>
                    {draftFlow.pageIndex + 1 < totalPages && (
                      <button
                        className="draft-btn draft-btn-next"
                        onClick={onContinue}
                        type="button"
                        aria-label={t("Go to next page", "الانتقال إلى الصفحة التالية")}
                      >
                        {t("Next", "التالي")}
                        <ChevronRight size={15} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Final approval */}
              {draftFlow.awaitingFinalApproval && (
                <div className="draft-choice-card" role="region" aria-label={t("Final approval", "الموافقة النهائية")}>
                  <div className="draft-final-icon" aria-hidden="true">✓</div>
                  <p className="draft-choice-question">
                    {t(
                      "That's the full draft. Do you approve this content?",
                      "هذا هو كامل المحتوى. هل توافق عليه؟"
                    )}
                  </p>
                  {draftFlow.fullContent && (
                    <div
                      className="draft-full-preview"
                      role="region"
                      aria-label={t("Full draft preview", "معاينة المسودة الكاملة")}
                      tabIndex={0}
                    >
                      {draftFlow.fullContent}
                    </div>
                  )}
                  <div className="draft-choice-actions" role="group" aria-label={t("Approval options", "خيارات الموافقة")}>
                    <button
                      className="draft-btn draft-btn-approve"
                      onClick={onApprove}
                      type="button"
                      aria-label={t("Approve the full draft", "الموافقة على المسودة الكاملة")}
                    >
                      <Check size={15} aria-hidden="true" />
                      {t("Approve", "موافقة")}
                    </button>
                    <button
                      className="draft-btn draft-btn-danger"
                      onClick={onReject}
                      type="button"
                      aria-label={t("Reject the draft", "رفض المسودة")}
                    >
                      <X size={15} aria-hidden="true" />
                      {t("Reject", "رفض")}
                    </button>
                  </div>
                </div>
              )}

              {/* Empty state */}
              {!draftFlow.awaitingListenChoice && !draftFlow.awaitingPageApproval && !draftFlow.awaitingFinalApproval && (
                <div className="draft-empty" role="status" aria-live="polite">
                  <FileText size={32} aria-hidden="true" style={{ opacity: 0.25, marginBottom: 12 }} />
                  <p>{t("Draft content will appear here.", "المحتوى المسود سيظهر هنا.")}</p>
                </div>
              )}
            </div>

            {/* Page navigator (multi-page) */}
            {totalPages > 1 && (
              <nav
                className="draft-panel-pager"
                aria-label={t("Page navigation", "التنقل بين الصفحات")}
              >
                <button
                  className="draft-pager-btn"
                  disabled={draftFlow.pageIndex === 0}
                  onClick={() => {
                    if (draftFlow.pageIndex > 0) {
                      // Navigate back — caller should handle via onContinue with negative direction
                      // For now we just expose a prop-compatible way
                    }
                  }}
                  aria-label={t("Previous page", "الصفحة السابقة")}
                  type="button"
                >
                  <ChevronLeft size={14} aria-hidden="true" />
                </button>
                <span className="draft-pager-label" aria-live="polite">
                  {draftFlow.pageIndex + 1} / {totalPages}
                </span>
                <button
                  className="draft-pager-btn"
                  disabled={draftFlow.pageIndex >= totalPages - 1}
                  onClick={onContinue}
                  aria-label={t("Next page", "الصفحة التالية")}
                  type="button"
                >
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              </nav>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}