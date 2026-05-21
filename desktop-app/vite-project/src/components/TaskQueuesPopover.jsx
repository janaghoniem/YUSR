// TaskQueuesPopover.jsx — Compact task bar, auto-clears completed tasks
import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence, LayoutGroup } from 'motion/react';
import { ListChecks, Clock, CheckCircle, Circle, X } from 'lucide-react';

/**
 * TaskQueuesPopover — A compact pill bar that sits at the top of the main area.
 * Completed tasks are shown briefly then auto-removed.
 * 
 * Props:
 *   type            — 'queued' | 'scheduled'
 *   title           — section label
 *   activeTasks     — array of { id, name|task }
 *   completedTasks  — array of { id, name|task }
 *   onCompleteTask  — (id) => void
 *   clearDelay      — ms before a completed task fades out (default 2500)
 */
export function TaskQueuesPopover({
  type,
  title,
  activeTasks = [],
  completedTasks = [],
  onCompleteTask,
  clearDelay = 2500,
}) {
  const [visibleCompleted, setVisibleCompleted] = useState([]);

  // When a new completed task arrives, show it briefly then remove it
  useEffect(() => {
    if (!completedTasks.length) return;

    setVisibleCompleted(prev => {
      const prevIds = new Set(prev.map(t => t.id));
      const newOnes = completedTasks.filter(t => !prevIds.has(t.id));
      return [...prev, ...newOnes];
    });
  }, [completedTasks]);

  // Auto-remove a completed task from display after clearDelay
  useEffect(() => {
    if (!visibleCompleted.length) return;
    const timer = setTimeout(() => {
      setVisibleCompleted(prev => prev.slice(1));
    }, clearDelay);
    return () => clearTimeout(timer);
  }, [visibleCompleted, clearDelay]);

  const allActive    = activeTasks;
  const allCompleted = visibleCompleted;

  if (allActive.length === 0 && allCompleted.length === 0) return null;

  const IconComponent = type === 'queued' ? ListChecks : Clock;

  return (
    <div
      className="task-queue-section"
      role="region"
      aria-label={title}
    >
      <span className="task-queue-label" aria-hidden="true">
        <IconComponent size={11} aria-hidden="true" />
        {title}
      </span>

      <LayoutGroup>
        <div className="task-queue-pills" role="list" aria-label={`${title} items`}>
          <AnimatePresence>
            {allActive.map(task => (
              <motion.div
                key={`active-${task.id}`}
                layoutId={`task-${task.id}`}
                initial={{ opacity: 0, scale: 0.82, y: -6 }}
                animate={{ opacity: 1, scale: 1,    y: 0   }}
                exit={{    opacity: 0, scale: 0.82, y: -6  }}
                transition={{ type: 'spring', stiffness: 400, damping: 28 }}
                className="task-pill task-pill-active"
                role="listitem"
                aria-label={`Active task: ${task.name || task.task || 'Unnamed'}`}
              >
                <IconComponent size={12} aria-hidden="true" className="task-pill-icon" />
                <span className="task-pill-name">
                  {task.name || task.task || 'Unnamed task'}
                </span>
                <button
                  className="task-pill-complete"
                  onClick={() => onCompleteTask?.(task.id)}
                  aria-label={`Mark "${task.name || task.task || 'task'}" as completed`}
                  title="Mark as completed"
                  type="button"
                >
                  <Circle size={12} aria-hidden="true" />
                </button>
              </motion.div>
            ))}

            {allCompleted.map(task => (
              <motion.div
                key={`done-${task.id}`}
                layoutId={`task-${task.id}`}
                initial={{ opacity: 0, scale: 0.82 }}
                animate={{ opacity: 1, scale: 1    }}
                exit={{    opacity: 0, scale: 0.82, x: 8 }}
                transition={{ type: 'spring', stiffness: 400, damping: 28 }}
                className="task-pill task-pill-done"
                role="listitem"
                aria-label={`Completed task: ${task.name || task.task || 'Unnamed'}`}
              >
                <CheckCircle size={12} aria-hidden="true" className="task-pill-icon-done" />
                <span className="task-pill-name task-pill-name-done">
                  {task.name || task.task || 'Unnamed task'}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </LayoutGroup>
    </div>
  );
}

/**
 * TaskQueueBar — Wrapper that combines both queued and scheduled task sections
 * at the top of the main content area.
 */
export function TaskQueueBar({
  queuedActiveTasks = [],
  queuedCompletedTasks = [],
  scheduledActiveTasks = [],
  scheduledCompletedTasks = [],
  onCompleteTask,
  t = (en) => en,
}) {
  const hasAny = queuedActiveTasks.length || queuedCompletedTasks.length
               || scheduledActiveTasks.length || scheduledCompletedTasks.length;

  if (!hasAny) return null;

  return (
    <div
      className="task-queue-bar"
      role="region"
      aria-label={t("Task queues", "قوائم المهام")}
      aria-live="polite"
    >
      <TaskQueuesPopover
        type="queued"
        title={t("Queued", "في الانتظار")}
        activeTasks={queuedActiveTasks}
        completedTasks={queuedCompletedTasks}
        onCompleteTask={(id) => onCompleteTask?.(id, 'queued')}
      />
      <TaskQueuesPopover
        type="scheduled"
        title={t("Scheduled", "مجدولة")}
        activeTasks={scheduledActiveTasks}
        completedTasks={scheduledCompletedTasks}
        onCompleteTask={(id) => onCompleteTask?.(id, 'scheduled')}
      />
    </div>
  );
}