import type {ReactNode} from 'react';
import styles from './styles.module.css';

/**
 * ArchitectureFlow — vertical pipeline of labeled stages (DESIGN.md §3.2).
 *
 * Props-driven: pass the stages you want to render. The default is the current
 * `heal` repair loop (Diagnoser → Patch Generator → Selector Verifier → Test
 * Runner). The DESIGN.md §3.2 wireframe also describes the roadmap's Shadow
 * Testing pipeline — feed those stages via the `stages` prop to render it.
 */
export interface FlowStage {
  /** Short mono label shown in the box. */
  label: string;
  /** One-line description surfaced on hover (title attribute for now). */
  tooltip?: string;
}

export interface ArchitectureFlowProps {
  stages?: FlowStage[];
  /** Accessible label for the pipeline. */
  ariaLabel?: string;
}

/** The current heal repair loop — the product as shipped today. */
export const HEAL_LOOP_STAGES: FlowStage[] = [
  {
    label: 'Diagnoser',
    tooltip: 'Maps the failing selector to the DOM change and infers the root cause.',
  },
  {
    label: 'Patch Generator',
    tooltip: 'Emits the target line + replacement via structured outputs — selectors/waits only.',
  },
  {
    label: 'Selector Verifier',
    tooltip: 'Checks the patched selector against the live DOM — exactly one match, or re-patch.',
  },
  {
    label: 'Test Runner',
    tooltip: 'Runs `npx playwright test`; on failure the Router loops back (max 3).',
  },
];

export default function ArchitectureFlow({
  stages = HEAL_LOOP_STAGES,
  ariaLabel = 'Repair pipeline',
}: ArchitectureFlowProps): ReactNode {
  return (
    <ol className={styles.flow} aria-label={ariaLabel}>
      {stages.map((stage, i) => (
        <li key={stage.label} className={styles.stageItem}>
          <div
            className={styles.stageBox}
            title={stage.tooltip}
            data-tooltip={stage.tooltip}>
            <span className={styles.stageLabel}>{stage.label}</span>
            {stage.tooltip ? (
              <span className={styles.stageTooltip}>{stage.tooltip}</span>
            ) : null}
          </div>
          {i < stages.length - 1 ? (
            <span className={styles.arrow} aria-hidden="true">
              ▼
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
