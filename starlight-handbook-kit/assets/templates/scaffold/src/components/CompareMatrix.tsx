import { useMemo, useState } from 'react';
import { useHydrated } from './useHydrated';
import styles from './widgets.module.css';

/**
 * CompareMatrix (T5 #2, RT-3 / U3) — side-by-side comparison of options
 * across criteria.
 *
 * Static-first: the initial render IS a real semantic <table> with every
 * cell visible, so a no-JS reader sees the complete comparison. Once
 * hydrated, JS layers on column highlight/focus and a "show only" filter.
 */
export interface CompareMatrixProps {
  /** Optional heading shown above the table. */
  caption?: string;
  /** Column options being compared, e.g. ["Kafka", "RabbitMQ", "SQS"]. */
  columns: string[];
  /** Each row: a criterion label plus one cell value per column (by index). */
  rows: Array<{
    /** Criterion name, e.g. "Ordering guarantees". */
    criterion: string;
    /** Cell values, aligned to `columns` by index. */
    cells: string[];
  }>;
}

export default function CompareMatrix({
  caption,
  columns,
  rows,
}: CompareMatrixProps) {
  const hydrated = useHydrated();
  // `null` = show all columns. A number = isolate that column.
  const [focused, setFocused] = useState<number | null>(null);

  const visibleCols = useMemo(
    () =>
      focused === null
        ? columns.map((_, i) => i)
        : columns.map((_, i) => i).filter((i) => i === focused),
    [columns, focused],
  );

  return (
    <div className={styles.widget}>
      {caption ? <div className={styles.widgetHeader}>{caption}</div> : null}
      <div className={styles.body}>
        {hydrated ? (
          <div className={styles.matrixControls}>
            <button
              type="button"
              className={`${styles.btn} ${
                focused === null ? styles.btnPrimary : ''
              }`}
              aria-pressed={focused === null}
              onClick={() => setFocused(null)}
            >
              Show all
            </button>
            {columns.map((col, i) => (
              <button
                key={col}
                type="button"
                className={`${styles.btn} ${
                  focused === i ? styles.btnPrimary : ''
                }`}
                aria-pressed={focused === i}
                onClick={() => setFocused((prev) => (prev === i ? null : i))}
              >
                {col}
              </button>
            ))}
            <span className={styles.matrixHint}>
              Click an option to isolate it.
            </span>
          </div>
        ) : null}

        <div className={styles.matrixWrap}>
          <table className={styles.matrix}>
            {caption ? (
              <caption className={styles.srOnly}>{caption}</caption>
            ) : null}
            <thead>
              <tr>
                <th scope="col">Criterion</th>
                {columns.map((col, i) => {
                  const isVisible = visibleCols.includes(i);
                  const isFocused = focused === i;
                  return (
                    <th
                      key={col}
                      scope="col"
                      hidden={hydrated && !isVisible}
                      className={
                        hydrated && isFocused ? styles.colActive : undefined
                      }
                    >
                      {hydrated ? (
                        <button
                          type="button"
                          className={styles.colBtn}
                          aria-pressed={isFocused}
                          onClick={() =>
                            setFocused((prev) => (prev === i ? null : i))
                          }
                        >
                          {col}
                        </button>
                      ) : (
                        col
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.criterion}>
                  <th scope="row">{row.criterion}</th>
                  {columns.map((col, i) => {
                    const isVisible = visibleCols.includes(i);
                    const isFocused = focused === i;
                    return (
                      <td
                        key={col}
                        hidden={hydrated && !isVisible}
                        className={
                          hydrated && isFocused ? styles.colActive : undefined
                        }
                      >
                        {row.cells[i] ?? '—'}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
