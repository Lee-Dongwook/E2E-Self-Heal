import React, { type ReactNode } from 'react';
import styles from './styles.module.css';

/**
 * HealingDiff — before (broken) vs after (healed) selector, side by side.
 *
 * Reference implementation for DESIGN.md §3.3. Props-driven: it renders whatever
 * selector strings it is given and never hardcodes copy. "Broken" always uses
 * --eeh-broken and "healed" always uses --eeh-healed (semantic colors are locked
 * in DESIGN.md rule §1.5).
 *
 * Example:
 *   <HealingDiff
 *     before={`await page.click('#submit-btn')`}
 *     after={`await page.click('#submit')`}
 *     reason="id changed: submit-btn → submit"
 *     highlight="submit"
 *   />
 */
export interface HealingDiffProps {
  /** The failing / old selector line (rendered red). */
  before: string;
  /** The repaired / new selector line (rendered green). */
  after: string;
  /** Optional one-line explanation of what changed. */
  reason?: string;
  /** Optional opt-in substring to precisely pinpoint the delta fragment. */
  highlight?: string;
  /** Optional labels; default to "Before" / "After". */
  beforeLabel?: string;
  afterLabel?: string;
}

// Helper function to find the 'highlight' string and wrap it in the CSS span
function renderHighlightedText(text: string, highlight?: string, variant?: 'broken' | 'healed'): ReactNode {
  // If no highlight is provided, or the text doesn't contain it, return plain text
  if (!highlight || !text.includes(highlight)) {
    return text;
  }

  const parts = text.split(highlight);
  const highlightClass = variant === 'broken' ? styles.highlightBroken : styles.highlightHealed;

  return (
    <>
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          {part}
          {index < parts.length - 1 && (
            <span className={highlightClass}>{highlight}</span>
          )}
        </React.Fragment>
      ))}
    </>
  );
}

function Panel({
  variant,
  label,
  code,
}: {
  variant: 'broken' | 'healed';
  label: string;
  code: ReactNode; // Changed from 'string' to 'ReactNode' so it accepts our <span>
}): ReactNode {
  const panelClass = variant === 'broken' ? styles.panelBroken : styles.panelHealed;
  return (
    <div className={`${styles.panel} ${panelClass}`}>
      <span className={styles.panelLabel}>{label}</span>
      <pre className={styles.code}>
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function HealingDiff({
  before,
  after,
  reason,
  highlight,
  beforeLabel = 'Before',
  afterLabel = 'After',
}: HealingDiffProps): ReactNode {
  return (
    <figure className={styles.wrapper}>
      <div className={styles.panels}>
        <Panel 
          variant="broken" 
          label={beforeLabel} 
          code={renderHighlightedText(before, highlight, 'broken')} 
        />
        <Panel 
          variant="healed" 
          label={afterLabel} 
          code={renderHighlightedText(after, highlight, 'healed')} 
        />
      </div>
      {reason ? <figcaption className={styles.reason}>{reason}</figcaption> : null}
    </figure>
  );
}
