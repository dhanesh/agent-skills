import { Fragment, useState } from 'react';
import { useHydrated } from './useHydrated';
import styles from './widgets.module.css';

/**
 * DecisionTree (T5 #1, RT-3 / U3) — guides a reader to a recommendation
 * through a series of multiple-choice questions.
 *
 * Static-first: without JS the entire tree renders as a nested
 * <details>/<ul> outline, so every question and every outcome is reachable.
 * Once hydrated, it becomes a step-by-step interactive walker with real
 * <button> choices and a breadcrumb trail.
 */

/** A leaf outcome — a terminal recommendation. */
export interface DecisionLeaf {
  /** Short recommendation title, e.g. "Use LRU". */
  recommendation: string;
  /** Optional supporting detail. */
  detail?: string;
}

/** A branch out of a question. */
export interface DecisionBranch {
  /** Choice label the reader picks, e.g. "Yes" / "Recency matters". */
  label: string;
  /** Where this choice leads. */
  next: DecisionNode;
}

/** A question node, or a terminal leaf. */
export type DecisionNode =
  | { question: string; branches: DecisionBranch[] }
  | { leaf: DecisionLeaf };

export interface DecisionTreeProps {
  /** Optional heading above the widget. */
  caption?: string;
  /** Root of the decision tree. */
  root: DecisionNode;
}

function isLeaf(node: DecisionNode): node is { leaf: DecisionLeaf } {
  return 'leaf' in node;
}

/** Recursive static outline — fully reachable with no JS. */
function StaticOutline({ node }: { node: DecisionNode }) {
  if (isLeaf(node)) {
    return (
      <div className={styles.dtLeaf}>
        <span className={styles.dtLeafTitle}>→ {node.leaf.recommendation}</span>
        {node.leaf.detail ? <div>{node.leaf.detail}</div> : null}
      </div>
    );
  }
  return (
    <details className={styles.dtOutline} open>
      <summary>{node.question}</summary>
      <ul>
        {node.branches.map((b, i) => (
          <li key={`${b.label}-${i}`}>
            <strong>{b.label}:</strong>
            <StaticOutline node={b.next} />
          </li>
        ))}
      </ul>
    </details>
  );
}

interface Crumb {
  question: string;
  choice: string;
}

export default function DecisionTree({ caption, root }: DecisionTreeProps) {
  const hydrated = useHydrated();
  const [current, setCurrent] = useState<DecisionNode>(root);
  const [path, setPath] = useState<Crumb[]>([]);
  const [history, setHistory] = useState<DecisionNode[]>([]);

  const choose = (branch: DecisionBranch, question: string) => {
    setHistory((h) => [...h, current]);
    setPath((p) => [...p, { question, choice: branch.label }]);
    setCurrent(branch.next);
  };

  const back = () => {
    setHistory((h) => {
      if (h.length === 0) return h;
      const prev = h[h.length - 1];
      setCurrent(prev);
      setPath((p) => p.slice(0, -1));
      return h.slice(0, -1);
    });
  };

  const restart = () => {
    setCurrent(root);
    setPath([]);
    setHistory([]);
  };

  return (
    <div className={styles.widget}>
      {caption ? <div className={styles.widgetHeader}>{caption}</div> : null}

      {hydrated ? (
        <div className={styles.dtCard}>
          {path.length > 0 ? (
            <div className={styles.dtCrumbs}>
              {path.map((c, i) => (
                <Fragment key={i}>
                  {i > 0 ? ' › ' : ''}
                  <span className={styles.dtCrumb}>{c.choice}</span>
                </Fragment>
              ))}
            </div>
          ) : null}

          {isLeaf(current) ? (
            <>
              <div className={styles.dtLeaf}>
                <div className={styles.dtLeafTitle}>
                  Recommendation: {current.leaf.recommendation}
                </div>
                {current.leaf.detail ? <p>{current.leaf.detail}</p> : null}
              </div>
              <div className={styles.dtChoices}>
                <button type="button" className={styles.btn} onClick={back}>
                  ← Back
                </button>
                <button
                  type="button"
                  className={styles.btn}
                  onClick={restart}
                >
                  Start over
                </button>
              </div>
            </>
          ) : (
            <>
              <p className={styles.dtQuestion}>{current.question}</p>
              <div className={styles.dtChoices}>
                {current.branches.map((b, i) => (
                  <button
                    key={`${b.label}-${i}`}
                    type="button"
                    className={`${styles.btn} ${styles.btnPrimary}`}
                    onClick={() => choose(b, current.question)}
                  >
                    {b.label}
                  </button>
                ))}
              </div>
              {history.length > 0 ? (
                <button type="button" className={styles.btn} onClick={back}>
                  ← Back
                </button>
              ) : null}
            </>
          )}
        </div>
      ) : (
        <div className={styles.dtCard}>
          <p className={styles.dtQuestion}>Decision outline</p>
          <StaticOutline node={root} />
        </div>
      )}
    </div>
  );
}
