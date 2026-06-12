import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { useHydrated } from './useHydrated';
import styles from './widgets.module.css';

/**
 * WhenWhyTabs (T5 #3, RT-3 / U3) — configurable tabbed panel, typically
 * "When to use" / "Why" / "Why NOT".
 *
 * Static-first: without JS, every tab renders as a stacked, headed
 * <section>, so all content is visible and readable. Once hydrated, the
 * sections collapse into an ARIA tablist with full arrow-key navigation.
 */
export interface WhenWhyTab {
  /** Tab label, e.g. "When to use". */
  label: string;
  /** Plain-text or pre-formatted body for the tab. */
  content: string;
}

export interface WhenWhyTabsProps {
  /** Optional heading above the widget. */
  caption?: string;
  /** Tabs in display order. */
  tabs: WhenWhyTab[];
}

export default function WhenWhyTabs({ caption, tabs }: WhenWhyTabsProps) {
  const hydrated = useHydrated();
  const [active, setActive] = useState(0);
  const baseId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const focusTab = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    setActive(next);
    tabRefs.current[next]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        focusTab(index + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault();
        focusTab(index - 1);
        break;
      case 'Home':
        e.preventDefault();
        focusTab(0);
        break;
      case 'End':
        e.preventDefault();
        focusTab(tabs.length - 1);
        break;
    }
  };

  return (
    <div className={styles.widget}>
      {caption ? <div className={styles.widgetHeader}>{caption}</div> : null}

      {hydrated ? (
        <>
          <div role="tablist" aria-label={caption ?? 'Tabs'} className={styles.tabList}>
            {tabs.map((tab, i) => (
              <button
                key={tab.label}
                type="button"
                role="tab"
                id={`${baseId}-tab-${i}`}
                aria-selected={active === i}
                aria-controls={`${baseId}-panel-${i}`}
                tabIndex={active === i ? 0 : -1}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                className={styles.tab}
                onClick={() => setActive(i)}
                onKeyDown={(e) => onKeyDown(e, i)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          {tabs.map((tab, i) => (
            <div
              key={tab.label}
              role="tabpanel"
              id={`${baseId}-panel-${i}`}
              aria-labelledby={`${baseId}-tab-${i}`}
              hidden={active !== i}
              tabIndex={0}
              className={styles.tabPanel}
            >
              {tab.content}
            </div>
          ))}
        </>
      ) : (
        // Static fallback: all panels visible as stacked headed sections.
        tabs.map((tab) => (
          <section key={tab.label} className={styles.staticSection}>
            <h4>{tab.label}</h4>
            <div>{tab.content}</div>
          </section>
        ))
      )}
    </div>
  );
}
