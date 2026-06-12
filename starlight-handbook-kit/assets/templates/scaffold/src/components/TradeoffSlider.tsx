import { useId, useState } from 'react';
import { useHydrated, usePrefersReducedMotion } from './useHydrated';
import styles from './widgets.module.css';

/**
 * TradeoffSlider (T5 #4, RT-3 / U3) — an interactive slider along an axis
 * (e.g. consistency ↔ availability) where each stop reveals a label and a
 * description of that tradeoff point.
 *
 * Static-first: without JS, every stop renders as an ordered list with its
 * label and description, so the full spectrum is readable. Once hydrated,
 * it becomes a native <input type="range"> that reveals one stop at a time,
 * with arrow-key support and proper aria-valuetext. Respects
 * prefers-reduced-motion.
 */
export interface TradeoffStop {
  /** Short label for this position, e.g. "Strong consistency". */
  label: string;
  /** Description of the tradeoff at this position. */
  description: string;
}

export interface TradeoffSliderProps {
  /** Optional heading above the widget. */
  caption?: string;
  /** Label for the left/start end of the axis. */
  leftAxis: string;
  /** Label for the right/end of the axis. */
  rightAxis: string;
  /** Ordered stops from left to right (at least 2). */
  stops: TradeoffStop[];
}

export default function TradeoffSlider({
  caption,
  leftAxis,
  rightAxis,
  stops,
}: TradeoffSliderProps) {
  const hydrated = useHydrated();
  const reduceMotion = usePrefersReducedMotion();
  const [index, setIndex] = useState(0);
  const baseId = useId();
  const max = Math.max(0, stops.length - 1);
  const active = stops[index];

  return (
    <div
      className={`${styles.widget} ${reduceMotion ? styles.reduceMotion : ''}`}
    >
      {caption ? <div className={styles.widgetHeader}>{caption}</div> : null}
      <div className={styles.body}>
        {hydrated ? (
          <>
            <div className={styles.tsAxis}>
              <span>{leftAxis}</span>
              <span>{rightAxis}</span>
            </div>
            <label htmlFor={`${baseId}-range`} className={styles.srOnly}>
              Tradeoff position between {leftAxis} and {rightAxis}
            </label>
            <input
              id={`${baseId}-range`}
              className={styles.tsRange}
              type="range"
              min={0}
              max={max}
              step={1}
              value={index}
              aria-valuemin={0}
              aria-valuemax={max}
              aria-valuenow={index}
              aria-valuetext={active ? active.label : undefined}
              onChange={(e) => setIndex(Number(e.target.value))}
            />
            <div className={styles.tsTicks}>
              {stops.map((stop, i) => (
                <button
                  key={`${stop.label}-${i}`}
                  type="button"
                  className={`${styles.tsTick} ${styles.btn} ${
                    i === index ? styles.tsTickActive : ''
                  }`}
                  aria-pressed={i === index}
                  onClick={() => setIndex(i)}
                >
                  {i + 1}
                </button>
              ))}
            </div>
            <div className={styles.tsReveal} aria-live="polite">
              <div className={styles.tsRevealLabel}>{active?.label}</div>
              <div>{active?.description}</div>
            </div>
          </>
        ) : (
          // Static fallback: every stop visible as an ordered list.
          <>
            <div className={styles.tsAxis}>
              <span>{leftAxis}</span>
              <span>{rightAxis}</span>
            </div>
            <ol className={styles.tsStaticList}>
              {stops.map((stop, i) => (
                <li key={`${stop.label}-${i}`}>
                  <span className={styles.tsStaticLabel}>{stop.label}</span>
                  {' — '}
                  {stop.description}
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </div>
  );
}
